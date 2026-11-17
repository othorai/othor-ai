# SPDX-License-Identifier: Apache-2.0
"""Sources: onboarding step 4 (10a, 2j, 10a1, 2k–2ke, 2l, 10b, 10c) and the Sources area
(6c, 6d, 6e–6i panels, 10m, 10r). Source credentials are never decrypted here: tests, scans and
reads are jobs for the worker. Every action writes an event row (othor.connectors.service).
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any
from urllib.parse import urlencode

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from othor.connectors import service
from othor.connectors.models import SheetTab, Source, SourceTable
from othor.connectors.sheets.client import SHEETS_MIME, FixtureSheetsClient
from othor.connectors.sheets.oauth import authorize_url
from othor.connectors.sql import hostguard
from othor.connectors.sql.engine import DEFAULT_PORT
from othor.core import fmt
from othor.core.events import record_event
from othor.core.models import Event, SetupState
from othor.core.tenancy import Repo, tenant_scope
from othor.setup import connect
from othor.web import sources_view as sv
from othor.web.context import company_url, local_path, resolve_site, root_url, services
from othor.web.routes.auth import STEP_ORDER, not_found, page
from othor.web.routes.pages import Owner, shell_ctx, signed_in
from othor.web.security import check_csrf

router = APIRouter(include_in_schema=False)
# the 404 (9a): what isn’t there, and one way back
GONE = (
    "There’s no such source",
    ("Data sources", "/sources"),
)
CSRF = [Depends(check_csrf)]
SQL_KINDS = {"postgres": "postgresql", "mysql": "mysql"}
MANAGE_TABS = {
    "overview": "Overview",
    "connection": "Connection",
    "tables": "Tables",
    "sync": "Sync",
}
SSL_OPTIONS = [("require", "Require"), ("verify-ca", "Verify CA"), ("disable", "Disable")]
# MySQL names the same modes REQUIRED / VERIFY_CA / DISABLED (10a1)
SSL_LABELS_MYSQL = {"require": "Required", "verify-ca": "Verify CA", "disable": "Disabled"}


# ---------------------------------------------------------------------------------------------
# helpers


def _sources_step(request: Request, o: Owner) -> bool:
    """True while onboarding is at (or past) Sources; the setup pages are only for that."""
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        st = s.scalars(sa.select(SetupState)).first()
    cur = st.current_step if st else "company"
    return cur == "done" or STEP_ORDER.index(cur) >= STEP_ORDER.index("sources")


def _source(s: Session, sid: uuid.UUID) -> Source | None:
    src = Repo(s).get(Source, sid)
    return None if src is None or src.deleted_at is not None else src


def _table(s: Session, tid: uuid.UUID) -> SourceTable | None:
    return Repo(s).get(SourceTable, tid)


def _setup_ctx(o: Owner, **kw: Any) -> dict[str, Any]:
    return {**shell_ctx(o, "setup"), "step_index": 4, **kw}


def _names(o: Owner) -> dict[str, str]:
    return {str(o.viewer.id): o.viewer.name.split(" ")[0] if o.viewer.name else "you"}


def _now(request: Request) -> Any:
    return services(request).clock()


def _hx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _current(request: Request) -> str | None:
    """The page an htmx action came from (HX-Current-URL), as a path on this host, or None."""
    raw = request.headers.get("HX-Current-URL")
    return (local_path(raw, "", request.url.netloc) or None) if raw else None


def _redirect(request: Request, url: str) -> Response:
    if _hx(request):
        r = Response(status_code=204)
        r.headers["HX-Redirect"] = url
        return r
    return RedirectResponse(url, 303)


def _sql_config(
    kind: str, host: str, port: str, database: str, user: str, ssl: str, schemas: list[str]
) -> dict[str, Any]:
    """M11c: the form has no schema field. A new source reads every schema its role can see
    (`schemas` empty); a source saved with named schemas keeps them when edited."""
    return {
        "host": host.strip(),
        "port": int(port) if port.strip().isdigit() else DEFAULT_PORT[SQL_KINDS[kind]],
        "database": database.strip(),
        "user": user.strip(),
        "ssl": ssl if ssl in dict(SSL_OPTIONS) else "require",
        "schemas": schemas if kind == "postgres" else [],
    }


def _role_sql(kind: str, cfg: dict[str, Any], egress: list[str]) -> str:
    db = cfg.get("database") or "<database>"
    if kind == "postgres":
        return (
            "CREATE ROLE othor_ro LOGIN PASSWORD '…';\n"
            f"GRANT CONNECT ON DATABASE {db} TO othor_ro;\n"
            "GRANT USAGE ON SCHEMA public TO othor_ro;\n"
            "GRANT SELECT ON ALL TABLES IN SCHEMA public TO othor_ro;\n"
            "-- repeat the last two lines for each other schema to read"
        )
    host = "<egress range>"
    if egress:
        host = egress[0].rsplit(".", 1)[0] + ".%" if "." in egress[0] else egress[0]
    return (
        f"CREATE USER 'othor_ro'@'{host}' IDENTIFIED BY '…';\n"
        f"GRANT SELECT, SHOW VIEW ON {db}.* TO 'othor_ro'@'{host}';"
    )


def _with_param(url: str, **params: str | None) -> str:
    from urllib.parse import parse_qsl, urlsplit, urlunsplit

    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query))
    for k, v in params.items():
        if v is None:
            q.pop(k, None)
        else:
            q[k] = v
    return urlunsplit(("", "", parts.path, urlencode(q), ""))


def _toast(s: Session, toast: str | None) -> dict[str, str] | None:
    """The Undo toast after a use change (from its event row; 204 + reload carries the id)."""
    if not toast:
        return None
    try:
        ev = Repo(s).get(Event, uuid.UUID(toast))
    except ValueError:
        return None
    if ev is None or not ev.action.startswith("table_use.") or ev.action == "table_use.undone":
        return None
    t = Repo(s).get(SourceTable, uuid.UUID(str(ev.object_id)))
    if t is None:
        return None
    return {
        "text": f"{t.schema}.{t.name} · {sv.use_chip(t)['label']}",
        "undo": f"/sources/tables/{t.id}/undo/{ev.id}",
    }


def _list_url(base: str, **params: Any) -> str:
    q = {k: v for k, v in params.items() if v not in (None, "", 1, "candidates")}
    return base + ("?" + urlencode(q) if q else "")


def _after_setup(s: Session, src: Source) -> bool:
    """A source connected after setup is confirmed: its metrics are drafted for review (6h)."""
    from othor.ontology import service as ontology

    return not src.in_setup and ontology.confirmed(s)


def _for_metric(src: Source) -> str | None:
    """A source connected from Metrics (2nca) goes back to that panel."""
    key = connect.for_key(src)
    return f"/setup/metrics?panel=connect&key={key}&kind={src.kind}&sid={src.id}" if key else None


# ---------------------------------------------------------------------------------------------
# 10a · pick a connector · 2l · summary


SETUP = "/setup/sources"


def _sug_line(x: Any) -> tuple[str, str]:
    """A suggestion's sub line and where it was seen (computed from the files and message)."""
    what = ", ".join(x.tables[:3]) if x.tables else ""
    seen = f"seen in {' and '.join(x.files[:2])}" if x.files else "named in your message"
    line = " · ".join(p for p in (what, seen) if p)
    return line[:1].upper() + line[1:], seen[:1].upper() + seen[1:]


def _suggestions(s: Session, keep: Source | None = None) -> list[dict[str, Any]]:
    out = []
    for x in connect.suggested(s, keep=keep):
        line, seen = _sug_line(x)
        name = x.name or ""
        href = (
            "/setup/sources/sheets"
            if x.kind == "gsheets"
            else f"{SETUP}?{urlencode({'panel': 'connect', 'kind': x.kind, 'n': name})}"
        )
        out.append(
            {
                "kind": x.kind,
                "tile": sv.KIND_TILE[x.kind],
                "title": f"{sv.KIND_NAME[x.kind]} · {name}" if name else sv.KIND_NAME[x.kind],
                "name": name,
                "line": line,
                "seen": seen,
                "href": href,
            }
        )
    return out


def _connected_rows(s: Session, svc: Any, added: str | None) -> list[dict[str, Any]]:
    rows = []
    for src in sv.sources(s, in_setup=True):
        c = sv.source_card(s, src, svc.settings)
        view = sv.scan_view(s, src)
        sheets = src.kind == "gsheets"
        unit = "tabs" if sheets else "tables"
        if view.running:
            done = fmt.num(min(view.scanned, view.total))
            sub = f"Scanning · {done} of {fmt.num(view.total)} {unit}"
        else:
            sub = (
                f"{fmt.num(c.tables_used)} of {fmt.num(c.tables_total)} {unit} used · "
                f"{fmt.num(c.rows_used)} rows"
            )
            if src.kind == "postgres" and len(c.schemas) > 1:
                sub += f" · {fmt.num(len(c.schemas))} schemas"
        rows.append(
            {
                "src": src,
                "tile": c.tile,
                "title": f"Google Sheets · {c.title}" if sheets else c.title,
                "sub": sub,
                "scanning": view.running,
                "just_added": added == str(src.id),
            }
        )
    return rows


def _request_ctx(request: Request) -> dict[str, Any]:
    """M15.4 · Request a connector: the form's choices, its error and the sent note."""
    from othor.connectors.models import REACH

    q = request.query_params
    return {
        "reach": list(REACH.items()),
        "error": "Name the system you use." if q.get("err") == "system" else None,
        "requested": (q.get("requested") or "")[:120] or None,
    }


def _setup_list(request: Request, o: Owner, **panel: Any) -> Response:
    """Step 4 (M11c, p0–p3): Connected, Suggested from your files, Other connectors folded;
    connecting, scanning, schemas and tables open in a side panel over it."""
    svc = services(request)
    q = request.query_params
    with svc.sf() as s, tenant_scope(o.company.id):
        connected = _connected_rows(s, svc, q.get("added"))
        suggested = _suggestions(s)
        s.expunge_all()
    return page(
        request,
        "sources/setup_pick.html",
        _setup_ctx(
            o,
            connected=connected,
            suggested=suggested,
            connectors=sv.CONNECTORS,
            has_sources=bool(connected),
            **panel,
        ),
    )


@router.get("/setup/sources", response_class=HTMLResponse)
def setup_sources(
    request: Request,
    panel: str | None = None,
    kind: str | None = None,
    sid: uuid.UUID | None = None,
    s_: str | None = Query(None, alias="s"),
    tab: str = "used",
    q: str = "",
    o: Owner = Depends(signed_in),
) -> Response:
    if not _sources_step(request, o):
        return RedirectResponse("/setup/company", 303)
    ctx: dict[str, Any] = {"panel": None, "creq": _request_ctx(request)}
    if panel == "request":
        ctx["panel"] = "request"
    elif panel == "connect" and kind in SQL_KINDS:
        ctx = {"panel": "connect", "sql": _sql_ctx(request, o, kind, sid, setup=True)}
    elif panel in ("scan", "schemas", "tables") and s_:
        try:
            src_id = uuid.UUID(s_)
        except ValueError:
            return not_found(request, o.site, *GONE)
        pctx = _source_panel_ctx(request, o, panel, src_id, tab, q)
        if pctx is None:
            return not_found(request, o.site, *GONE)
        ctx = {"panel": panel, **pctx}
    return _setup_list(request, o, **ctx)


def _source_panel_ctx(
    request: Request, o: Owner, panel: str, sid: uuid.UUID, tab: str, q: str
) -> dict[str, Any] | None:
    """The scanning, Schemas and Tables panels (M11c, p2, g3, t1–t2), in setup and the app."""
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, sid)
        if src is None:
            return None
        card = sv.source_card(s, src, svc.settings)
        view = sv.scan_view(s, src)
        out: dict[str, Any] = {"psrc": src, "card": card, "view": view}
        if panel == "scan":
            out["steps"] = sv.scan_steps(s, src, view)
            p = (view.scan.progress if view.scan else {}) or {}
            out["eta"] = sv.eta_text(int(p.get("eta_s") or 0)) if view.running else ""
        elif panel == "schemas":
            out["schemas"] = sv.schema_rows(s, src)
        else:
            key = {"used": "candidates", "ignored": "ignored", "excluded": "excluded"}.get(
                tab, "candidates"
            )
            out["tl"] = sv.table_list(s, src, tab="all" if q else key, q=q, owner_names=_names(o))
            out["ttab"] = tab if tab in ("used", "ignored", "excluded") else "used"
            out["toast"] = _toast(s, request.query_params.get("toast"))
        s.expunge_all()
    return out


@router.get("/setup/sources/new")
def setup_pick(request: Request, o: Owner = Depends(signed_in)) -> Response:
    return RedirectResponse(SETUP, 303)


@router.get("/setup/sources/{kind}/new")
def sql_new(
    request: Request, kind: str, sid: uuid.UUID | None = None, o: Owner = Depends(signed_in)
) -> Response:
    """The connect page became the connect panel (M11c); old links land on it."""
    if kind == "gsheets":
        return RedirectResponse("/setup/sources/sheets", 303)
    if kind not in SQL_KINDS:
        return not_found(request, o.site, *GONE)
    url = f"{SETUP}?panel=connect&kind={kind}" + (f"&sid={sid}" if sid else "")
    return RedirectResponse(url, 303)


def _sql_ctx(
    request: Request, o: Owner, kind: str, sid: uuid.UUID | None, *, setup: bool = False
) -> dict[str, Any]:
    svc = services(request)
    src = None
    if sid:
        with svc.sf() as s, tenant_scope(o.company.id):
            src = _source(s, sid)
            if src is not None:
                s.expunge(src)
    with svc.sf() as s, tenant_scope(o.company.id):
        n = len(sv.sources(s, in_setup=True))
        # the name the owner gave this source in their message or files ("erp_db", 10a1)
        named = [x for x in _suggestions(s, keep=src) if x["kind"] == kind]
    want = request.query_params.get("n") or ""
    sug = next((x for x in named if x["name"] == want), named[0] if named else None)
    cfg = (
        dict(src.config)
        if src
        else {
            "host": "",
            "port": DEFAULT_PORT[SQL_KINDS[kind]],
            # the name it was suggested under is usually the database's ("sales_dw")
            "database": (sug["name"] if sug else ""),
            "user": "othor_ro",
            "ssl": svc.settings.default_ssl,
            "schemas": [],
        }
    )
    return {
        "kind": kind,
        "kind_name": sv.KIND_NAME[kind],
        "for_name": sug["name"] if sug else "",
        "seen": sug["seen"] if sug else "",
        "src": src,
        "cfg": cfg,
        "setup": setup,
        "ssl_options": SSL_OPTIONS
        if kind == "postgres"
        else [(v, SSL_LABELS_MYSQL[v]) for v, _ in SSL_OPTIONS],
        "role_sql": _role_sql(kind, cfg, svc.settings.egress_ips),
        "egress": svc.settings.egress_ips,
        "egress_v4": [ip for ip in svc.settings.egress_ips if ":" not in ip],
        "egress_v6": [ip for ip in svc.settings.egress_ips if ":" in ip],
        "ips_url": svc.settings.ips_url,
        "source_no": n + 1,
        "test": (src.last_test if src else None),
        "close": SETUP
        if setup
        else ("/sources?panel=manage&s=" + str(src.id) if src else "/sources"),
    }


@router.post("/sources/sql/{kind}/test", response_class=HTMLResponse, dependencies=CSRF)
def sql_test(
    request: Request,
    kind: str,
    sid: str = Form(""),
    host: str = Form(""),
    port: str = Form(""),
    database: str = Form(""),
    user: str = Form(""),
    password: str = Form(""),
    ssl: str = Form("require"),
    setup: str = Form(""),
    go: str = Form(""),
    o: Owner = Depends(signed_in),
) -> Response:
    """Save the details (password encrypted at once), queue Test connection, return the chip.
    With go=1 (the panel's Connect, M11c) the chip connects once the test passes."""
    if kind not in SQL_KINDS:
        return not_found(request, o.site, *GONE)
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, uuid.UUID(sid)) if sid else None
        keep = list((src.config or {}).get("schemas") or []) if src else []
        cfg = _sql_config(kind, host, port, database, user, ssl, keep)
        missing = [k for k in ("host", "database", "user") if not cfg[k]]
        if missing or (src is None and not password):
            return page(
                request,
                "sources/_test_chip.html",
                {
                    "test": {"ok": False, "message": "Fill in host, database, user and password."},
                    "src": {"id": src.id} if src else None,
                    "kind": kind,
                    "go": go,
                },
            )
        # othor.ai's source-host guard (owner, 16 Nov 2026): a private or sample host is refused
        # before anything is saved; checked again on every connection (make_engine)
        if hostguard.applies(slug=o.company.slug):
            why, _addrs = hostguard.refusal(cfg["host"], cfg["port"])
            if why:
                record_event(
                    s,
                    actor=str(o.viewer.id),
                    action="source.refused_host",
                    object_type="source",
                    object_id=src.id if src else o.company.id,
                    after={"kind": kind, "reason": why},  # never the host or an address
                )
                s.commit()
                return page(
                    request,
                    "sources/_test_chip.html",
                    {
                        "test": {"ok": False, "message": hostguard.MESSAGE},
                        "src": {"id": src.id} if src else None,
                        "kind": kind,
                        "go": go,
                    },
                )
        if src is None:
            src = service.create_sql_source(
                s,
                svc.key,
                o.company.id,
                kind=kind,
                config=cfg,
                password=password,
                actor=str(o.viewer.id),
                in_setup=setup == "1",
            )
        else:
            service.update_sql_source(
                s, svc.key, src, config=cfg, password=password or None, actor=str(o.viewer.id)
            )
        service.request_test(s, src)
        s.commit()
        test = dict(src.last_test or {})
        sid_out = src.id
    if not test.get("pending"):  # a test that finished at once (tests, a fixture worker)
        return test_status(request, sid_out, go, o)
    return page(
        request,
        "sources/_test_chip.html",
        {"test": test, "src": {"id": sid_out}, "kind": kind, "go": go},
    )


def _connected_url(src: Source) -> str:
    """Where Connect lands: the scanning panel (setup or the app), or back to the metric that
    asked for the source (2nca)."""
    if src.in_setup:
        return _for_metric(src) or f"{SETUP}?panel=scan&s={src.id}"
    return f"/sources?panel=scan&s={src.id}"


def _do_connect(request: Request, o: Owner, s: Session, src: Source) -> None:
    service.connect(s, src, actor=str(o.viewer.id), now=_now(request))
    if _after_setup(s, src):  # 6h: drafted after the scan (jobs run in order)
        connect.connected(s, src, actor=str(o.viewer.id))


@router.get("/sources/{sid}/test-status", response_class=HTMLResponse)
def test_status(
    request: Request, sid: uuid.UUID, go: str = "", o: Owner = Depends(signed_in)
) -> Response:
    svc = services(request)
    nxt = None
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, sid)
        if src is None:
            return not_found(request, o.site, *GONE)
        test = dict(src.last_test or {})
        kind = src.kind
        if go == "1" and test.get("ok") and not test.get("pending"):
            if src.status == "draft":
                _do_connect(request, o, s, src)
                s.commit()
                nxt = _connected_url(src)
            else:  # new credentials for a connected source: back to where it is managed
                nxt = SETUP if src.in_setup else f"/sources?panel=manage&s={sid}"
    return page(
        request,
        "sources/_test_chip.html",
        {"test": test, "src": {"id": sid}, "kind": kind, "go": go, "next": nxt},
    )


@router.post("/sources/{sid}/connect", dependencies=CSRF)
def sql_connect(request: Request, sid: uuid.UUID, o: Owner = Depends(signed_in)) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, sid)
        if src is None:
            return not_found(request, o.site, *GONE)
        try:
            _do_connect(request, o, s, src)
        except service.ActionRefused:
            panel = SETUP if src.in_setup else "/sources"
            return RedirectResponse(f"{panel}?panel=connect&kind={src.kind}&sid={sid}", 303)
        s.commit()
        url = _connected_url(src)
    return _redirect(request, url)


# ---------------------------------------------------------------------------------------------
# 2k–2kd · scanning, the table list, search


@router.get("/setup/sources/{sid}/scan")
def scan_page(
    request: Request, sid: uuid.UUID, tab: str = "", q: str = "", o: Owner = Depends(signed_in)
) -> Response:
    """The scan page became the scanning and Tables panels (M11c); old links land on them."""
    if tab or q:
        t = {"candidates": "used"}.get(tab, tab)
        return RedirectResponse(
            f"{SETUP}?{urlencode({'panel': 'tables', 's': str(sid), 'tab': t or 'used', 'q': q})}",
            303,
        )
    return RedirectResponse(f"{SETUP}?panel=scan&s={sid}", 303)


@router.post("/sources/{sid}/schemas/{name}/skip", dependencies=CSRF)
def schema_skip(
    request: Request, sid: uuid.UUID, name: str, o: Owner = Depends(signed_in)
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, sid)
        if src is None:
            return not_found(request, o.site, *GONE)
        service.skip_schema(s, src, name, actor=str(o.viewer.id))
        s.commit()
    return _redirect(request, _current(request) or f"/setup/sources/{sid}/scan")


@router.post("/sources/{sid}/schemas", dependencies=CSRF)
async def schemas_save(request: Request, sid: uuid.UUID, o: Owner = Depends(signed_in)) -> Response:
    """The Schemas panel's Save (M11c): read the ticked schemas; a change rescans."""
    form = await request.form()
    read = {str(x) for x in form.getlist("schema")}
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, sid)
        if src is None:
            return not_found(request, o.site, *GONE)
        rescan = service.set_schemas(s, src, read, actor=str(o.viewer.id))
        s.commit()
        base = SETUP if src.in_setup else "/sources"
    return _redirect(request, f"{base}?panel=scan&s={sid}" if rescan else base)


# --- the Use menu (2ka, 2ke), bulk bring back (2kb), use without personal columns (2kc) ----


@router.post("/sources/tables/{tid}/use", dependencies=CSRF)
def table_use(
    request: Request,
    tid: uuid.UUID,
    use: str = Form(...),
    reason: str = Form(""),
    o: Owner = Depends(signed_in),
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        t = _table(s, tid)
        if t is None:
            return not_found(request, o.site, *GONE)
        try:
            if use in ("without:metric_source", "without:dimension"):
                ev = service.use_without_personal(
                    s, t, use.split(":")[1], actor=str(o.viewer.id), now=_now(request)
                )
            elif use == "keep_excluded":
                ev = service.keep_excluded(s, t, actor=str(o.viewer.id), now=_now(request))
            else:
                ev = service.set_use(
                    s, t, use, reason or None, actor=str(o.viewer.id), now=_now(request)
                )
        except service.ActionRefused as e:
            return HTMLResponse(str(e), status_code=400)
        s.commit()
    url = _current(request) or f"/sources/{t.source_id}"
    return _redirect(request, _with_param(url, toast=str(ev) if ev else None))


@router.post("/sources/tables/{tid}/undo/{event_id}", dependencies=CSRF)
def table_use_undo(
    request: Request, tid: uuid.UUID, event_id: uuid.UUID, o: Owner = Depends(signed_in)
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        t = _table(s, tid)
        ev = Repo(s).get(Event, event_id)
        if t is None or ev is None or str(ev.object_id) != str(tid) or not ev.before:
            return not_found(request, o.site, *GONE)
        service.undo_use(s, t, dict(ev.before), actor=str(o.viewer.id), now=_now(request))
        s.commit()
    url = _current(request) or f"/sources/{t.source_id}"
    return _redirect(request, _with_param(url, toast=None))


@router.post("/sources/{sid}/bring-back", dependencies=CSRF)
async def bring_back(request: Request, sid: uuid.UUID, o: Owner = Depends(signed_in)) -> Response:
    form = await request.form()
    use = str(form.get("use", ""))
    ids = [uuid.UUID(str(x)) for x in form.getlist("t")]
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        n = 0
        for tid in ids:
            t = _table(s, tid)
            if t is None or t.source_id != sid:
                continue
            try:
                service.bring_back(s, t, use, actor=str(o.viewer.id), now=_now(request))
                n += 1
            except service.ActionRefused:
                continue
        s.commit()
    return _redirect(request, _current(request) or f"/setup/sources/{sid}/scan?tab=ignored")


# ---------------------------------------------------------------------------------------------
# 10b · Google Sheets: sign in, choose spreadsheets (Picker, drive.file only), tabs


@router.get("/setup/sources/sheets", response_class=HTMLResponse)
def sheets_page(
    request: Request, sid: uuid.UUID | None = None, o: Owner = Depends(signed_in)
) -> Response:
    if not _sources_step(request, o):
        return RedirectResponse("/setup/company", 303)
    return page(request, "sources/setup_sheets.html", _setup_ctx(o, **_sheets_ctx(request, o, sid)))


def _sheets_ctx(request: Request, o: Owner, sid: uuid.UUID | None) -> dict[str, Any]:
    svc = services(request)
    src, rows, running = None, [], False
    with svc.sf() as s, tenant_scope(o.company.id):
        if sid:
            src = _source(s, sid)
        if src is None:  # a Sheets source signed in but not finished
            src = s.scalars(
                sa.select(Source)
                .where(
                    Source.kind == "gsheets",
                    Source.status == "scanning",
                    Source.deleted_at.is_(None),
                )
                .order_by(Source.created_at.desc())
            ).first()
        if src is not None:
            rows = sv.tab_rows(s, src)
            view = sv.scan_view(s, src)
            running = view.running
        n = len(sv.sources(s, in_setup=True))
        s.expunge_all()
    picked = [dict(f) for f in ((src.config or {}).get("files", []) if src else [])]
    today = svc.clock().date()
    for f in picked:
        if f.get("modified"):
            d = dt.datetime.fromisoformat(f["modified"]).date()
            when = (
                "today"
                if d == today
                else ("yesterday" if (today - d).days == 1 else fmt.day_month(d))
            )
            f["edited"] = f"edited {when}" + (f" by {f['editor']}" if f.get("editor") else "")
    return {
        "src": src,
        "account": (src.config or {}).get("account") if src else None,
        "rows": rows,
        "running": running,
        "picked": picked,
        "selected": sum(1 for r in rows if r.tab.selected),
        "to_check": sum(1 for r in rows if r.tab.selected and r.needs_check),
        "source_no": n + (0 if src and src.in_setup else 1),
        "mode": svc.settings.sheets_mode,
        "google_api_key": svc.settings.google_api_key.get_secret_value(),
        "google_app_id": svc.settings.google_app_id,
        "sheets_mime": SHEETS_MIME,
    }


@router.post("/sources/google/start", dependencies=CSRF)
def google_start(
    request: Request,
    setup: str = Form(""),
    reconnect: str = Form(""),
    for_: str = Form("", alias="for"),
    o: Owner = Depends(signed_in),
) -> Response:
    """Sign in with Google (drive.file only). In fixture mode, sign in as the stand-in account.
    `reconnect` keeps the same source, spreadsheets and tabs (10m)."""
    svc = services(request)
    if svc.settings.sheets_mode == "google":
        state = svc.signer("google-oauth").dumps(
            {
                "c": str(o.company.id),
                "s": o.company.slug,
                "setup": setup,
                "r": reconnect,
                "f": for_,
            },
            svc.clock(),
        )
        return RedirectResponse(authorize_url(svc.settings, _callback(request), state, None), 303)
    folder = svc.settings.sample_dir / "sheets"
    account = FixtureSheetsClient(folder).account() if (folder / "auth.json").exists() else ""
    return _signed_in_google(request, o, {"fixture": True}, account, setup == "1", reconnect, for_)


def _signed_in_google(
    request: Request,
    o: Owner,
    secret: dict[str, Any],
    account: str,
    in_setup: bool,
    reconnect: str = "",
    for_key: str = "",
) -> Response:
    from othor.connectors import credentials

    svc = services(request)
    if reconnect:
        with svc.sf() as s, tenant_scope(o.company.id):
            src = _source(s, uuid.UUID(reconnect))
            if src is None or src.kind != "gsheets":
                return not_found(request, o.site, *GONE)
            credentials.store(s, svc.key, o.company.id, src.id, secret)
            before = src.status
            src.status, src.disconnected_at, src.disconnect_reason = "connected", None, None
            src.config = {**src.config, "account": account}
            record_event(
                s,
                actor=str(o.viewer.id),
                action="source.reconnected",
                object_type="source",
                object_id=src.id,
                before={"status": before},
                after={"status": "connected", "account": account},
            )
            # fresh within minutes, not at 02:00
            service.read_now(s, src, actor=str(o.viewer.id), now=svc.clock())
            s.commit()
        return RedirectResponse(f"/sources?panel=manage&s={reconnect}", 303)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = service.create_sheets_source(
            s,
            svc.key,
            o.company.id,
            files=[],
            secret=secret,
            account=account,
            actor=str(o.viewer.id),
            in_setup=in_setup,
        )
        src.status = "scanning"
        if for_key and re.fullmatch(r"[a-z0-9_]{1,60}", for_key):
            connect.mark_for(src, for_key)
        s.commit()
        sid = src.id
        back = _for_metric(src)
    if back:
        return RedirectResponse(back, 303)
    return RedirectResponse(
        f"/setup/sources/sheets?sid={sid}" if in_setup else f"/sources?panel=sheets&s={sid}", 303
    )


def _callback(request: Request) -> str:
    """The one redirect URI registered at Google, on the root host (Google allows no wildcard
    redirect URIs, so a company host can't be one; M11b)."""
    return root_url(request, "/sources/google/callback")


def _google_state(request: Request, state: str) -> dict[str, Any]:
    svc = services(request)
    try:
        data = svc.signer("google-oauth").loads(state, svc.clock(), 600)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


@router.get("/sources/google/callback", name="google_callback")
def google_callback(request: Request, code: str = "", state: str = "") -> Response:
    """Google returns here, on the root host, where no one is signed in. The signed `state`
    names the company; the owner goes back to that company's host to finish there. Nothing is
    exchanged here, and a state that isn't ours goes nowhere."""
    site = resolve_site(request)
    data = _google_state(request, state)
    slug = str(data.get("s") or "")
    if not site.is_root or not code or not re.fullmatch(r"[a-z0-9-]{1,63}", slug):
        return not_found(request, site)
    q = urlencode({"code": code, "state": state})
    return RedirectResponse(company_url(request, slug, f"/sources/google/finish?{q}"), 303)


@router.get("/sources/google/finish")
def google_finish(
    request: Request, code: str = "", state: str = "", o: Owner = Depends(signed_in)
) -> Response:
    from othor.connectors.sheets import oauth
    from othor.connectors.sheets.client import GoogleSheetsClient

    svc = services(request)
    data = _google_state(request, state)
    if data.get("c") != str(o.company.id) or not code:
        return RedirectResponse("/setup/sources/sheets", 303)
    tokens = oauth.exchange(svc.settings, code, _callback(request))
    account = GoogleSheetsClient(tokens.access_token).account()
    return _signed_in_google(
        request,
        o,
        tokens.secret(),
        account,
        data.get("setup") == "1",
        str(data.get("r") or ""),
        str(data.get("f") or ""),
    )


@router.get("/sources/{sid}/picker", response_class=HTMLResponse)
def picker(
    request: Request, sid: uuid.UUID, link: str = "", o: Owner = Depends(signed_in)
) -> Response:
    """Fixture mode's stand-in for Google Picker: the spreadsheets a Picker would offer."""
    svc = services(request)
    folder = svc.settings.sample_dir / "sheets"
    files = FixtureSheetsClient(folder).picker_files() if (folder / "index.json").exists() else []
    link = link or request.headers.get("HX-Prompt", "")  # Paste a link (hx-prompt)
    pre = link.rsplit("/d/", 1)[-1].split("/")[0] if "/d/" in link else link
    return page(request, "sources/_picker.html", {"sid": sid, "files": files, "pre": pre})


@router.post("/sources/{sid}/picker-token", dependencies=CSRF)
def picker_token(request: Request, sid: uuid.UUID, o: Owner = Depends(signed_in)) -> Response:
    """Real mode only: the Picker runs in the browser with the owner's own drive.file token.
    The one place the web process reads a source secret, and only for Google Picker."""
    from fastapi.responses import JSONResponse

    from othor.connectors import credentials
    from othor.connectors.sheets.access import client_for

    svc = services(request)
    if svc.settings.sheets_mode != "google":
        return not_found(request, o.site, *GONE)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, sid)
        if src is None or src.kind != "gsheets":
            return not_found(request, o.site, *GONE)
        secret = credentials.load(s, svc.key, o.company.id, src.id) or {}
        _client, new = client_for(svc.settings, secret)
        if new:
            credentials.store(s, svc.key, o.company.id, src.id, new)
            s.commit()
            secret = new
    return JSONResponse(
        {"token": secret.get("access_token", "")}, headers={"Cache-Control": "no-store"}
    )


@router.post("/sources/{sid}/files", dependencies=CSRF)
async def choose_files(request: Request, sid: uuid.UUID, o: Owner = Depends(signed_in)) -> Response:
    form = await request.form()
    ids = [str(x) for x in form.getlist("file") if str(x).strip()]
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, sid)
        if src is None or src.kind != "gsheets":
            return not_found(request, o.site, *GONE)
        # Google's Picker hands back one spreadsheet at a time, so a pick adds to the
        # connection's spreadsheets; Remove takes one away (owner, 11 Nov 2026)
        have = list((src.config or {}).get("files", []))
        known = {f["id"] for f in have}
        files = have + [{"id": i} for i in dict.fromkeys(ids) if i not in known]
        src.config = {**(src.config or {}), "files": files}
        record_event(
            s,
            actor=str(o.viewer.id),
            action="sheet_files.chosen",
            object_type="source",
            object_id=src.id,
            after={"files": ids},
        )
        service.start_scan(s, src, actor=str(o.viewer.id))
        s.commit()
        in_setup = src.in_setup
        back = _for_metric(src)
    return _redirect(
        request,
        back
        or (f"/setup/sources/sheets?sid={sid}" if in_setup else f"/sources?panel=sheets&s={sid}"),
    )


@router.post("/sources/{sid}/files/{file_id}/remove", dependencies=CSRF)
def remove_file(
    request: Request, sid: uuid.UUID, file_id: str, o: Owner = Depends(signed_in)
) -> Response:
    """Take one spreadsheet off a Sheets connection: its tabs go with it, so the nightly read,
    the Ontology and the source card stop counting them."""
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, sid)
        if src is None or src.kind != "gsheets":
            return not_found(request, o.site, *GONE)
        files = list((src.config or {}).get("files", []))
        gone = next((f for f in files if f.get("id") == file_id), None)
        if gone is not None:
            src.config = {**(src.config or {}), "files": [f for f in files if f is not gone]}
            tabs = s.scalars(
                sa.select(SheetTab).where(
                    SheetTab.source_id == sid, SheetTab.spreadsheet_id == file_id
                )
            ).all()
            now = svc.clock()
            for tab in tabs:
                if tab.table_id is not None:
                    t = _table(s, tab.table_id)
                    if t is not None and t.gone_at is None:
                        t.gone_at = now
                s.delete(tab)
            record_event(
                s,
                actor=str(o.viewer.id),
                action="sheet_file.removed",
                object_type="source",
                object_id=sid,
                before={"file": file_id, "title": gone.get("title"), "tabs": len(tabs)},
            )
            s.commit()
        in_setup = src.in_setup
    return _redirect(
        request,
        f"/setup/sources/sheets?sid={sid}" if in_setup else f"/sources?panel=sheets&s={sid}",
    )


@router.post("/sources/{sid}/tabs", dependencies=CSRF)
async def choose_tabs(request: Request, sid: uuid.UUID, o: Owner = Depends(signed_in)) -> Response:
    form = await request.form()
    chosen = {uuid.UUID(str(x)) for x in form.getlist("tab")}
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, sid)
        if src is None:
            return not_found(request, o.site, *GONE)
        tabs = s.scalars(sa.select(SheetTab).where(SheetTab.source_id == sid)).all()
        rows = {r.tab.id: r for r in sv.tab_rows(s, src)}
        for tab in tabs:
            want = tab.id in chosen and not rows[tab.id].disabled
            if tab.selected != want:
                tab.selected = want
        record_event(
            s,
            actor=str(o.viewer.id),
            action="sheet_tabs.chosen",
            object_type="source",
            object_id=sid,
            after={"tabs": sorted(t.tab_name for t in tabs if t.selected)},
        )
        s.commit()
        nxt = next((t for t in tabs if t.selected and rows[t.id].needs_check), None)
        in_setup = src.in_setup
        if nxt is None:
            try:
                service.finish_sheets(s, src, actor=str(o.viewer.id), now=_now(request))
                back = _for_metric(src)
                if back or _after_setup(s, src):  # 2ncb / 6h: read now, redraft
                    connect.connected(s, src, actor=str(o.viewer.id))
                s.commit()
            except service.ActionRefused:
                return _redirect(request, _for_metric(src) or f"/setup/sources/sheets?sid={sid}")
            if back:
                return _redirect(request, back)
            return _redirect(
                request, "/setup/sources" if in_setup else f"/sources?panel=manage&s={sid}"
            )
    return _redirect(request, f"/setup/sources/{sid}/check/{nxt.id}")


# --- 10c · check a tab ------------------------------------------------------------------------


@router.get("/setup/sources/{sid}/check/{tab_id}", response_class=HTMLResponse)
def check_tab(
    request: Request, sid: uuid.UUID, tab_id: uuid.UUID, o: Owner = Depends(signed_in)
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, sid)
        tab = Repo(s).get(SheetTab, tab_id)
        if src is None or tab is None or tab.source_id != sid:
            return not_found(request, o.site, *GONE)
        view = sv.check_view(tab)
        rows = sv.tab_rows(s, src)
        running = sv.scan_view(s, src).running
        s.expunge_all()
    to_check = [r for r in rows if r.tab.selected and r.needs_check]
    idx = next((i for i, r in enumerate(to_check) if r.tab.id == tab_id), 0)
    ctx = _setup_ctx(
        o,
        src=src,
        tab=tab,
        v=view,
        running=running,
        to_check=len(to_check),
        idx=idx + 1,
        max_row=(tab.shape or {}).get("grid_rows", 0),
        shell="shells/onboarding.html" if src.in_setup else "shells/app.html",
    )
    if not src.in_setup:
        ctx["nav"] = "sources"
    if request.query_params.get("fix"):
        ctx["fix"] = _tab_fix_ctx(request, o, tab)
    if _hx(request) and request.headers.get("HX-Target") == "check-live":
        return page(request, "sources/_check_live.html", ctx)
    return page(request, "sources/setup_check.html", ctx)


def _tab_fix_ctx(request: Request, o: Owner, tab: SheetTab) -> dict[str, Any]:
    """10c: Fix with Othor AI beside the check (quick units form by code, typed line by AI)."""
    from othor.ai.gateway import model_status
    from othor.setup import chat, tab_fix

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        t = tab_fix.thread(s, tab.id, create=False)
        msgs = chat.messages(s, t)
        available, _ = model_status(svc.settings, s, svc.clock())
        pending = any(
            m.role == "user" and (m.proposal or {}).get("state") == "pending" for m in msgs
        )
        s.expunge_all()
    return {
        "messages": msgs,
        "pending": pending,
        "model": available,
        "numbers": tab_fix.number_columns(tab),
        "units": tab_fix.UNITS,
        "not_available": chat.NOT_AVAILABLE,
    }


@router.post("/setup/sources/{sid}/check/{tab_id}/fix", dependencies=CSRF)
def check_fix(
    request: Request,
    sid: uuid.UUID,
    tab_id: uuid.UUID,
    text: str = Form(""),
    o: Owner = Depends(signed_in),
) -> Response:
    """A typed line to Othor AI about this tab; the worker answers (job 'chat')."""
    from othor.jobs.queue import enqueue
    from othor.setup import chat, tab_fix

    svc = services(request)
    url = f"/setup/sources/{sid}/check/{tab_id}?fix=1"
    with svc.sf() as s, tenant_scope(o.company.id):
        tab = Repo(s).get(SheetTab, tab_id)
        if tab is None or tab.source_id != sid:
            return not_found(request, o.site, *GONE)
        if text.strip():
            t = tab_fix.thread(s, tab_id)
            assert t is not None
            m = chat.add_turn(s, t, text=text.strip(), patch=None, actor=str(o.viewer.id))
            enqueue(s, "chat", {"message_id": str(m.id)})
            s.commit()
    return _redirect(request, url)


@router.post("/setup/sources/{sid}/check/{tab_id}/shape", dependencies=CSRF)
def check_shape(
    request: Request,
    sid: uuid.UUID,
    tab_id: uuid.UUID,
    header_row: int = Form(...),
    last_row: str = Form(""),
    unit_col: str = Form(""),
    unit: str = Form(""),
    msg: str = Form(""),
    o: Owner = Depends(signed_in),
) -> Response:
    """The header stepper, the last-row selector and a column's unit (10c, by hand or from
    Fix with Othor AI): re-read the tab with the owner's fix."""
    from othor.connectors.sheets.detect import UNIT_FACTOR

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, sid)
        tab = Repo(s).get(SheetTab, tab_id)
        if src is None or tab is None or tab.source_id != sid:
            return not_found(request, o.site, *GONE)
        hr = max(1, min(header_row, 50))
        override: dict[str, Any] = {"header_row": hr}
        if last_row.isdigit() and int(last_row) > hr:
            override["last_row"] = int(last_row)
        headers = {c.get("header") for c in (tab.shape or {}).get("columns", [])}
        if unit_col in headers and unit in UNIT_FACTOR:
            override["units"] = {**(tab.units or {}), unit_col: unit}
        record_event(
            s,
            actor=str(o.viewer.id),
            action="sheet_tab.shape_changed",
            object_type="sheet_tab",
            object_id=tab_id,
            before={"header_row": tab.header_row, "last_row": tab.last_row, "units": tab.units},
            after=override,
        )
        if msg:
            from othor.setup import tab_fix

            tab_fix.mark_applied(s, msg, actor=str(o.viewer.id))
        service.start_scan(s, src, actor=str(o.viewer.id), tabs={tab.tab_name: override})
        s.commit()
    back = f"/setup/sources/{sid}/check/{tab_id}"
    return _redirect(request, back + ("?fix=1" if msg or unit_col else ""))


@router.post("/setup/sources/{sid}/check/{tab_id}/confirm", dependencies=CSRF)
def check_confirm(
    request: Request, sid: uuid.UUID, tab_id: uuid.UUID, o: Owner = Depends(signed_in)
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, sid)
        tab = Repo(s).get(SheetTab, tab_id)
        if src is None or tab is None or tab.source_id != sid:
            return not_found(request, o.site, *GONE)
        fp = (tab.shape or {}).get("fingerprint")
        if not fp:
            return HTMLResponse("This tab has no table to confirm.", status_code=400)
        service.confirm_tab(s, tab, actor=str(o.viewer.id), now=_now(request), fingerprint=fp)
        s.commit()
        rows = sv.tab_rows(s, src)
        nxt = next((r.tab for r in rows if r.tab.selected and r.needs_check), None)
        in_setup = src.in_setup
        if nxt is None:
            try:
                service.finish_sheets(s, src, actor=str(o.viewer.id), now=_now(request))
                back = _for_metric(src)
                if back or _after_setup(s, src):  # 2ncb / 6h: read now, redraft
                    connect.connected(s, src, actor=str(o.viewer.id))
                s.commit()
            except service.ActionRefused:
                return _redirect(request, _for_metric(src) or f"/setup/sources/sheets?sid={sid}")
            if back:
                return _redirect(request, back)
            return _redirect(
                request, "/setup/sources" if in_setup else f"/sources?panel=manage&s={sid}"
            )
    return _redirect(request, f"/setup/sources/{sid}/check/{nxt.id}")


# ---------------------------------------------------------------------------------------------
# 6c · Sources page (with the panels 6e–6i, 10r, 10m)


def _newest(s: Any, settings: Any, srcs: Any) -> dict[str, str]:
    """Each source's newest data, as Home's Data sources table shows it (owner, 15 Nov 2026):
    from the latest run's stored facts, so the two tables always agree."""
    from othor.web import home_view as hv
    from othor.web import weather_view

    run = hv.latest_run(s)
    got = weather_view._stored(settings, run) if run is not None else None
    facts = got[1].get("facts") if got is not None else None
    return {str(x.id): hv.source_status(x.name, facts)[2] for x in srcs}


@router.get("/sources", response_class=HTMLResponse)
def sources_page(
    request: Request,
    tab: str = "connected",
    s_: str | None = Query(None, alias="s"),
    panel: str | None = None,
    kind: str | None = None,
    sid: uuid.UUID | None = None,
    confirm: str | None = None,
    o: Owner = Depends(signed_in),
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        srcs = sv.sources(s)
        cards = [sv.source_card(s, x, svc.settings, _names(o)) for x in srcs]
        waiting = connect.waiting_on_sources(s)
        newest = _newest(s, svc.settings, srcs)
        sel = next((c for c in cards if str(c.src.id) == s_), cards[0] if cards else None)
        panel_ctx: dict[str, Any] = {}
        # M15.1: the table list only for Manage (its Tables rows); the scan panel's view comes from
        # _source_panel_ctx below. Both read every scanned table (4,000 on a big source).
        if panel == "manage" and sel is not None:
            panel_ctx = {"tl": sv.table_list(s, sel.src, owner_names=_names(o))}
        if panel == "sheets":
            target = _source(s, uuid.UUID(s_)) if s_ else None
            panel_ctx = {
                "sheets_src": target,
                "rows": sv.tab_rows(s, target) if target else [],
                # the real Picker in production, as in setup (11 Nov 2026: was fixture-only)
                "sheets_mode": svc.settings.sheets_mode,
                "google_api_key": svc.settings.google_api_key.get_secret_value(),
                "google_app_id": svc.settings.google_app_id,
                "sheets_mime": SHEETS_MIME,
            }
        if panel == "review" and sel is not None:
            panel_ctx["review"] = _review_ctx(s, sel.src, request.query_params.get("added"))
            if panel_ctx["review"]["added"]:  # "Run now with the new metrics?" (v0.11)
                from othor.web import again_view

                panel_ctx["again"] = again_view.load(
                    s, svc.settings, o.company, svc.clock(), "/brief", {}
                )
        if panel == "reconnect" and sel is not None:
            panel_ctx["affects"] = sv.affects(s, svc.settings, sel.src)
        if panel == "chat" and sel is not None:
            chat_panel = _chat_panel(request, s, sel.src)
            if chat_panel is None:
                panel = "review"
                panel_ctx["review"] = _review_ctx(s, sel.src)
            else:
                panel_ctx["chat_panel"] = chat_panel
        found = sv.matches(s, svc.settings, sel.src) if sel is not None else []
        s.expunge_all()
    if panel in ("schemas", "tables", "scan") and sel is not None:  # M11c: the onboarding panels
        pctx = _source_panel_ctx(
            request, o, panel, sel.src.id, tab, request.query_params.get("q", "")
        )
        if pctx:
            panel_ctx.update(pctx)
    return page(
        request,
        "sources/sources.html",
        {
            **shell_ctx(o, "sources"),
            "tab": tab
            if tab in ("connected", "suggested", "all") and panel not in ("manage", "tables")
            else "connected",
            # 10r: the panel's tab is URL state (?panel=manage&tab=sync); Overview by default
            "mtab": tab if panel == "manage" and tab in MANAGE_TABS else "overview",
            "manage_tabs": MANAGE_TABS,
            "confirm": confirm if panel == "manage" else None,
            "cards": cards,
            "newest": newest,
            "waiting": waiting,
            "sel": sel,
            "matches": found,
            "connectors": sv.CONNECTORS,
            "kinds_connected": {c.src.kind for c in cards},
            "panel": panel,
            "creq": _request_ctx(request),
            "kind": kind,
            "run_time": o.company.run_time,
            **panel_ctx,
            "sql": _sql_ctx(request, o, kind, sid)
            if panel == "connect" and kind in SQL_KINDS
            else None,
        },
    )


def _review_ctx(s: Session, src: Source, added: str | None = None) -> dict[str, Any]:
    """6h (M11c G): what the new source adds, in the onboarding's Metrics to confirm panel.
    Waiting metrics redrafted, then new drafts, each with today's computed value. Confirm
    stages a row; "Add N to the Ontology" writes the staged rows as one version."""
    from othor.ontology.models import OntologyVersion
    from othor.setup import confirm
    from othor.setup import drafts as setup_drafts
    from othor.web import setup_view

    rv = connect.review(s, src.id)
    rows: list[dict[str, Any]] = []
    for r in rv.rows:
        if r.state in ("skipped", "confirmed"):
            continue
        m = setup_view.confirm_item(r, confirmed=confirm.is_staged(r))
        m["edit"] = f"/sources?panel=chat&s={src.id}&key={r.key}&purpose=fix"
        m["blocked"] = None if confirm.can_stage(r) else (r.note or "Fix it before confirming")
        rows.append(m)
    found = [
        {"type": d.body["type"].capitalize(), "text": d.body["text"]}
        for d in setup_drafts.stored(s, "discovery")
        if d.state != "skipped" and src.name.lower() in d.body.get("text", "").lower()
    ]
    done = None
    if added and added.isdigit():
        v = s.scalars(
            sa.select(OntologyVersion).where(OntologyVersion.number == int(added))
        ).first()
        if v is not None:
            n = sum(1 for d in v.diff.get("changes", []) if d.get("type") == "metric")
            done = {"version": v.number, "n": n}
    staged = [m["key"] for m in rows if m["confirmed"]]
    return {
        "files": [],
        "databases": rows,
        "n": len(rows),
        "confirmed": len(staged),
        "staged": staged,
        "remaining": [m["key"] for m in rows if not m["confirmed"] and not m["blocked"]],
        "waiting": rv.waiting,
        "new": len(rows) - rv.waiting,
        "drafting": rv.drafting,
        "found": found,
        "added": done,
    }


def _chat_panel(request: Request, s: Session, src: Source) -> dict[str, Any] | None:
    """6ha: the fix chat for one of the new source's drafts, beside the review."""
    from othor.ai.gateway import model_status
    from othor.setup import chat
    from othor.web import setup_view
    from othor.web.routes.setup import _replies

    q = request.query_params
    key, purpose = q.get("key") or "", q.get("purpose") or "fix"
    row = chat.find_row(s, key)
    if row is None or purpose not in chat.PURPOSES:
        return None
    svc = services(request)
    available, _ = model_status(svc.settings, s, svc.clock())
    page = f"/sources?panel=chat&s={src.id}&key={key}&purpose={purpose}"
    return setup_view.chat_ctx(
        s,
        row,
        purpose,
        _replies(s, row, purpose),
        available,
        page=page,
        close=f"/sources?panel=review&s={src.id}",
    )


def _review_rows(s: Session, sid: uuid.UUID) -> list[Any]:
    return [r for r in connect.review(s, sid).rows if r.state not in ("skipped", "confirmed")]


def _review_url(sid: uuid.UUID, added: int | None = None) -> str:
    return f"/sources?panel=review&s={sid}" + (f"&added={added}" if added is not None else "")


async def _stage(
    request: Request, sid: uuid.UUID, o: Owner, keys: set[str] | None, on: bool
) -> Response:
    """Confirm or Undo rows in the 6h panel: staged only, the Ontology doesn't change."""
    from othor.setup import confirm

    if keys is None:
        keys = {str(k) for k in (await request.form()).getlist("key")}
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        if _source(s, sid) is None:
            return not_found(request, o.site, *GONE)
        for r in _review_rows(s, sid):
            if r.key in keys:
                confirm.stage(s, r, actor=str(o.viewer.id), on=on)
        s.commit()
    return RedirectResponse(_review_url(sid), 303)


@router.post("/sources/{sid}/confirm-metrics/stage-all", dependencies=CSRF)
async def review_stage_all(
    request: Request, sid: uuid.UUID, o: Owner = Depends(signed_in)
) -> Response:
    """ "Confirm the N remaining" (6h): one request, one event per metric."""
    return await _stage(request, sid, o, None, True)


@router.post("/sources/{sid}/confirm-metrics/unstage", dependencies=CSRF)
async def review_unstage(
    request: Request, sid: uuid.UUID, o: Owner = Depends(signed_in)
) -> Response:
    return await _stage(request, sid, o, None, False)


@router.post("/sources/{sid}/confirm-metrics/undo", dependencies=CSRF)
async def review_undo(request: Request, sid: uuid.UUID, o: Owner = Depends(signed_in)) -> Response:
    """The toast's Undo: the added version is undone and its metrics return to the panel."""
    from othor.core.models import Company
    from othor.ontology.models import OntologyVersion
    from othor.setup import confirm

    form = await request.form()
    n = str(form.get("version") or "")
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        if _source(s, sid) is None or not n.isdigit():
            return not_found(request, o.site, *GONE)
        if s.scalars(sa.select(OntologyVersion).where(OntologyVersion.number == int(n))).first():
            rows = [r for r in connect.review(s, sid).rows if r.state == "confirmed"]
            company = s.scalars(sa.select(Company).where(Company.id == o.company.id)).one()
            confirm.undo_added(s, company, int(n), rows, actor=str(o.viewer.id))
            s.commit()
    return RedirectResponse(_review_url(sid), 303)


@router.post("/sources/{sid}/confirm-metrics/{key}/stage", dependencies=CSRF)
async def review_stage(
    request: Request, sid: uuid.UUID, key: str, o: Owner = Depends(signed_in)
) -> Response:
    return await _stage(request, sid, o, {key}, True)


@router.post("/sources/{sid}/confirm-metrics", dependencies=CSRF)
async def confirm_metrics(
    request: Request, sid: uuid.UUID, o: Owner = Depends(signed_in)
) -> Response:
    """6h: "Add N to the Ontology": the staged metrics → one new Ontology version, one event
    per metric; the panel shows a toast with Undo."""
    from othor.core.models import Company
    from othor.setup import confirm

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, sid)
        if src is None:
            return not_found(request, o.site, *GONE)
        try:
            v = confirm.add_staged(
                s,
                s.scalars(sa.select(Company).where(Company.id == o.company.id)).one(),
                _review_rows(s, sid),
                actor=str(o.viewer.id),
                today=svc.clock().date(),
                reason=f"Connected {src.name}",
            )
        except confirm.NotReady:
            return RedirectResponse(_review_url(sid), 303)
        if v is not None:
            from othor.setup.discover import offer_splits

            offer_splits(s, o.company.id)  # the new numbers may reach splits (v0.11)
        s.commit()
        number = v.number if v is not None else None
    return RedirectResponse(_review_url(sid, added=number) if number else _review_url(sid), 303)


@router.get("/sources/{sid}", response_class=HTMLResponse)
def source_detail(
    request: Request,
    sid: uuid.UUID,
    tab: str = "",
    q: str = "",
    page_no: int = Query(1, alias="page"),
    expand: str | None = None,
    toast: str | None = None,
    o: Owner = Depends(signed_in),
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, sid)
        if src is None:
            return not_found(request, o.site, *GONE)
        card = sv.source_card(s, src, svc.settings, _names(o))
        if q and tab not in sv.TABS:
            tab = "all"
        tl = sv.table_list(
            s,
            src,
            tab=tab if tab in (*sv.TABS, "all") else "candidates",
            q=q,
            page=page_no,
            expand=uuid.UUID(expand) if expand else None,
            owner_names=_names(o),
        )
        view = sv.scan_view(s, src)
        toast_ctx = _toast(s, toast)
        s.expunge_all()
    return page(
        request,
        "sources/detail.html",
        {
            **shell_ctx(o, "sources"),
            "src": src,
            "card": card,
            "tl": tl,
            "view": view,
            "base": f"/sources/{sid}",
            "list_url": _list_url,
            "toast": toast_ctx,
        },
    )


@router.post("/sources/{sid}/action/{action}", dependencies=CSRF)
def source_action(
    request: Request,
    sid: uuid.UUID,
    action: str,
    next_: str = Form("", alias="next"),
    o: Owner = Depends(signed_in),
) -> Response:
    """Test connection · Read now · Re-scan · Pause/Resume · Acknowledge · Disconnect (10r, 6d)."""
    svc = services(request)
    actor = str(o.viewer.id)
    with svc.sf() as s, tenant_scope(o.company.id):
        src = _source(s, sid)
        if src is None:
            return not_found(request, o.site, *GONE)
        if action == "test" and src.kind != "gsheets":
            service.request_test(s, src)
        elif action == "read":
            service.read_now(s, src, actor=actor, now=svc.clock())
        elif action == "rescan":
            service.start_scan(s, src, actor=actor)
        elif action in ("pause", "resume"):
            service.pause(s, src, action == "pause", actor=actor)
        elif action == "acknowledge":
            service.acknowledge_schema_change(s, src, actor=actor)
        elif action == "disconnect":
            service.disconnect(s, src, actor=actor, now=_now(request))
        else:
            return not_found(request, o.site, *GONE)
        s.commit()
    if action == "disconnect":
        return _redirect(request, "/sources")
    if next_.startswith("/") and not next_.startswith("//"):
        return _redirect(request, next_)
    return _redirect(request, _current(request) or f"/sources?panel=manage&s={sid}")


@router.post("/sources/test-all", dependencies=CSRF)
def test_all(request: Request, o: Owner = Depends(signed_in)) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        for src in sv.sources(s):
            if src.kind != "gsheets" and src.status in ("connected", "paused"):
                service.request_test(s, src)
        s.commit()
    return _redirect(request, "/sources")


@router.post("/sources/{sid}/proposals/{pid}/{decision}", dependencies=CSRF)
def decide_proposal(
    request: Request,
    sid: uuid.UUID,
    pid: uuid.UUID,
    decision: str,
    tab: str | None = None,
    o: Owner = Depends(signed_in),
) -> Response:
    """Accept or decline a join proposed by cross-source matching (6h, 5 Nov 2026). Accepting
    writes the join rule as a new Ontology version; until then no metric uses the join."""
    from othor.engine import proposals
    from othor.engine.models import Proposal

    if decision not in ("accept", "decline"):
        return not_found(request, o.site, *GONE)
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        p = s.scalars(sa.select(Proposal).where(Proposal.id == pid)).first()
        if p is None or _source(s, sid) is None:
            return not_found(request, o.site, *GONE)
        proposals.decide(
            s, p, accept=decision == "accept", actor=str(o.viewer.id), now=svc.clock(), tab=tab
        )
        s.commit()
    # the proposal sits in Manage (owner, 12 Nov 2026): Accept and Decline keep it open
    return RedirectResponse(f"/sources?panel=manage&s={sid}", 303)


# --- M15.4 · Request a connector --------------------------------------------------------------

REQUEST_BACK = {"/sources": "/sources?panel=add", "/setup/sources": "/setup/sources"}


@router.post("/sources/request", dependencies=CSRF)
def request_connector(
    request: Request,
    system: str = Form(""),
    version: str = Form(""),
    wants: str = Form(""),
    reach: str = Form(""),
    next_: str = Form("/sources", alias="next"),
    o: Owner = Depends(signed_in),
) -> Response:
    """From Add a source (app only, owner 13 Nov 2026): stored with an event, emailed to support,
    counted in `othor admin requests`. Returns to the list it came from, never elsewhere."""
    from urllib.parse import quote

    from othor.connectors import requests as creq
    from othor.connectors.models import REACH
    from othor.mail.messages import connector_request

    back = REQUEST_BACK.get(next_, REQUEST_BACK["/sources"])
    form_page = ("/setup/sources" if next_ == "/setup/sources" else "/sources") + "?panel=request"
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        try:
            row = creq.record(
                s,
                system=system,
                version=version,
                wants=wants,
                reach=reach,
                actor=str(o.viewer.id),
            )
        except ValueError:
            return RedirectResponse(form_page + "&err=system", 303)
        msg = connector_request(
            to=svc.settings.access_request_to,
            company=o.company.name,
            requester=o.viewer.email,
            system=row.system,
            version=row.version,
            wants=row.wants,
            reach=REACH.get(row.reach or ""),
            settings=svc.settings,
        )
        s.commit()
        name = row.system
    svc.relay.send(msg)
    sep = "&" if "?" in back else "?"
    return RedirectResponse(f"{back}{sep}requested={quote(name)}", 303)
