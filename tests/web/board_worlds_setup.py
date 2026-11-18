# SPDX-License-Identifier: Apache-2.0
"""Board-state builders for the `setup` world: onboarding steps (M12b). See board_worlds.py.

Each builder drives the app's own routes (and its worker, inline) the way the web tests do
(tests/web/test_setup_web.py, test_sources_web.py, test_chat_web.py, test_first_run_web.py). No
model: drafts are by rules, so captures are deterministic. States the board shows half-way (a
scan, drafting, the first run) are reached by running the job, then putting its rows back the
way they are half-way through (`_mid_scan`, `_mid_drafting`, `_mid_run`).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from othor.jobs.runner import WorkerContext, run_pending
from tests.web.board_worlds import World, prep

PW = "othor_ro_dev"
MYSQL = {
    "host": "localhost",
    "port": "3307",
    "database": "erp_db",
    "user": "othor_ro",
    "password": PW,
    "ssl": "disable",
    "setup": "1",
}
POSTGRES = {
    "host": "localhost",
    "port": "5443",
    "database": "sales_dw",
    "user": "othor_ro",
    "password": PW,
    "ssl": "disable",
    "setup": "1",
}
DATA_LIVES = "erp_db, sales_dw, Order tracker, MIS-Weekly"


def _jobs(w: World) -> None:
    svc = w.svc
    run_pending(WorkerContext(svc.settings, svc.sf, svc.key, clock=svc.clock))


def _ok(r: Any) -> Any:
    assert r.status_code in (200, 303), (r.status_code, r.text[:500])
    return r


# --- 2c · 2d · 10a: upload, business, sources --------------------------------------------------


@prep
def files_read(w: World) -> None:
    """2c: the five sample files uploaded and read (by code: the screenshot waits for the model)."""
    from tests.fixtures.sample_company.uploads import write_uploads

    files = write_uploads(w.sample, w.tmp / "files")
    tok = w.csrf("/setup/upload")
    _ok(
        w.env.client.post(
            w.host + "/setup/upload/files",
            data={"csrf": tok},
            files=[("files", (f.name, f.read_bytes())) for f in files],
            follow_redirects=False,
        )
    )
    _jobs(w)


@prep
def at_business_step(w: World) -> None:
    """2d: files read, onboarding at Tell us about the business."""
    files_read(w)
    _ok(w.post("/setup/upload/continue", page="/setup/upload"))


@prep
def at_sources_step(w: World) -> None:
    """10a: the business message sent with where the data lives; the connector picker."""
    from tests.integration import setup_world as sw

    at_business_step(w)
    _ok(
        w.post(
            "/setup/business/continue",
            {"text": sw.MESSAGE, "data_lives": DATA_LIVES},
            page="/setup/business",
        )
    )


# --- 10a1 · 2j · 2k–2ke · 2l: connect and scan databases -----------------------------------------


def _sql_tested(w: World, kind: str, form: dict[str, str]) -> str:
    panel = f"/setup/sources?panel=connect&kind={kind}"
    r = _ok(w.post(f"/sources/sql/{kind}/test", form, page=panel))
    sid = re.search(r'name="sid" value="([0-9a-f-]{36})"', r.text)
    assert sid, r.text[:500]
    _jobs(w)
    return sid.group(1)


def _sql_connected(w: World, kind: str, form: dict[str, str], scan: bool = True) -> str:
    sid = _sql_tested(w, kind, form)
    _ok(w.post(f"/sources/{sid}/connect", page=f"/setup/sources?panel=connect&kind={kind}"))
    if scan:
        _jobs(w)
    return sid


@prep
def mysql_tested(w: World) -> dict[str, str]:
    """10a1: source 1, erp_db on MySQL, details filled in and Test connection passed."""
    at_sources_step(w)
    return {"sid": _sql_tested(w, "mysql", MYSQL)}


@prep
def postgres_tested(w: World) -> dict[str, str]:
    """2j: erp_db connected and scanned; source 2, sales_dw on PostgreSQL, tested."""
    at_sources_step(w)
    _sql_connected(w, "mysql", MYSQL)
    return {"sid": _sql_tested(w, "postgres", POSTGRES)}


def _mid_scan(w: World, sid: str, schema: str, after: str) -> None:
    """Put a finished scan back half-way through `schema`: the tables named after `after` not
    read yet (the first of them being read), the scan running."""
    from othor.connectors.models import Scan, SourceSchema, SourceTable

    src_id = uuid.UUID(sid)
    with w.svc.sf() as s, tenant_scope(w.cid):
        left = s.scalars(
            sa.select(SourceTable)
            .where(
                SourceTable.source_id == src_id,
                SourceTable.schema == schema,
                SourceTable.name > after,
            )
            .order_by(SourceTable.name)
        ).all()
        for i, t in enumerate(left):
            t.state = "reading" if i == 0 else "queued"
        sch = s.scalars(
            sa.select(SourceSchema).where(
                SourceSchema.source_id == src_id, SourceSchema.name == schema
            )
        ).one()
        sch.scanned_count = sch.table_count - len(left)
        sc = s.scalars(
            sa.select(Scan).where(Scan.source_id == src_id).order_by(Scan.created_at.desc())
        ).first()
        assert sc is not None
        sc.status, sc.finished_at = "running", None
        p = dict(sc.progress or {})
        p["scanned"] = int(p.get("total", 0)) - len(left)
        p["schemas_done"] = int(p.get("schemas", 1)) - 1
        p["eta_s"] = 60
        sc.progress = p
        s.commit()


@prep
def postgres_scanning(w: World) -> dict[str, str]:
    """2k–2ke: erp_db scanned; sales_dw connected and its scan half-way through sales_dw."""
    from othor.connectors.models import SourceTable

    at_sources_step(w)
    _sql_connected(w, "mysql", MYSQL)
    sid = _sql_connected(w, "postgres", POSTGRES)
    _mid_scan(w, sid, "sales_dw", "fact_order")
    with w.svc.sf() as s, tenant_scope(w.cid):
        ids = {
            t.name: str(t.id)
            for t in s.scalars(
                sa.select(SourceTable).where(SourceTable.source_id == uuid.UUID(sid))
            )
        }
    # the owner ignored public.vendor_rating from its Use menu (2kb, 2ke D)
    url = f"{w.host}/setup/sources?panel=tables&s={sid}"
    r = w.env.client.post(
        f"{w.host}/sources/tables/{ids['vendor_rating']}/use",
        data={"use": "ignored", "reason": "not_needed"},
        headers={"HX-Request": "true", "HX-Current-URL": url, "X-CSRF-Token": w.env.csrf(url)},
    )
    assert r.status_code == 204, r.status_code
    return {"sid": sid, "employee": ids["employee"]}


@prep
def postgres_scanned(w: World) -> dict[str, str]:
    """2l (M11c p3), 2kf: erp_db and sales_dw connected and scanned."""
    at_sources_step(w)
    _sql_connected(w, "mysql", MYSQL)
    return {"sid": _sql_connected(w, "postgres", POSTGRES)}


# --- 10b · 10c · 2la: Google Sheets (the fixture client over tests' sample sheets) -------------


def _sheets_signed_in(w: World, data: dict[str, str] | None = None) -> str:
    """Sign in with Google (the fixture client: no Google call) and return the new source."""
    from tests.fixtures.sample_company.load import write_sheets

    write_sheets(w.sample, w.tmp / "sample" / "sheets")
    r = _ok(w.post("/sources/google/start", data or {"setup": "1"}, page="/setup/sources/sheets"))
    return r.headers["location"].split("sid=")[1].split("&")[0]


def _tab_ids(w: World, sid: str) -> dict[str, str]:
    from othor.connectors.models import SheetTab

    with w.svc.sf() as s, tenant_scope(w.cid):
        rows = s.scalars(sa.select(SheetTab).where(SheetTab.source_id == uuid.UUID(sid))).all()
        return {t.tab_name: str(t.id) for t in rows}


def _sheet_picked(w: World, file: str, data: dict[str, str] | None = None) -> str:
    sid = _sheets_signed_in(w, data)
    _ok(w.post(f"/sources/{sid}/files", {"file": file}, page="/setup/sources/sheets"))
    _jobs(w)
    return sid


def _sheet_connected(w: World, file: str, tabs: tuple[str, ...]) -> str:
    """Pick the spreadsheet and tabs, confirm every tab that needs a check (10b → 10c → 2l)."""
    sid = _sheet_picked(w, file)
    ids = _tab_ids(w, sid)
    r = _ok(w.post(f"/sources/{sid}/tabs", {"tab": [ids[t] for t in tabs]}, page="/setup/sources"))
    while "/check/" in r.headers.get("location", ""):
        loc = r.headers["location"]
        r = _ok(w.post(loc + "/confirm", page=loc))
    _jobs(w)
    return sid


@prep
def sheets_signed_in(w: World) -> dict[str, str]:
    """10b: erp_db and sales_dw connected; source 3, Google Sheets signed in, Order tracker
    FY27 chosen and its tabs found."""
    postgres_scanned(w)
    return {"sid": _sheet_picked(w, "sample-order-tracker-fy27")}


@prep
def sheets_tab_check(w: World) -> dict[str, str]:
    """10c: Open orders and Deals chosen; Open orders' header and range to check."""
    vals = sheets_signed_in(w)
    sid = vals["sid"]
    ids = _tab_ids(w, sid)
    r = _ok(
        w.post(
            f"/sources/{sid}/tabs",
            {"tab": [ids["Open orders"], ids["Deals"]]},
            page="/setup/sources/sheets",
        )
    )
    return {"sid": sid, "check": r.headers["location"]}


@prep
def four_sources(w: World) -> None:
    """2la: erp_db, sales_dw, the Order tracker and the MIS sheet, each connected and scanned."""
    postgres_scanned(w)
    _sheet_connected(w, "sample-order-tracker-fy27", ("Open orders", "Deals"))
    _sheet_connected(w, "sample-mis-weekly", ("Sales", "Receivables"))


# --- 2m · 2n · 2n0 · 2na–2nf: Metrics ------------------------------------------------------------


def _drafted(w: World, *, tracker: bool = True, run: bool = True) -> None:
    """As tests/web/test_chat_web.py `_drafted`: erp_db and sales_dw in the catalog, four files
    read (the data dictionary kept back for 2ne), the business message, metrics drafted by
    rules. `tracker`: Order book value waits for the Order tracker (Google Sheets)."""
    from othor.core.models import SetupState
    from othor.jobs.queue import enqueue
    from othor.setup import uploads
    from othor.setup.models import BusinessMessage, SetupDraft
    from tests.fixtures.sample_company.uploads import DICTIONARY, write_uploads
    from tests.integration import setup_world as sw

    svc = w.svc
    sw.connect_sample(svc.sf, svc.key, w.cid, w.sample)
    files = [f for f in write_uploads(w.sample, w.tmp / "files") if f.name != DICTIONARY]
    with svc.sf() as s, tenant_scope(w.cid):
        for f in files:
            up, _ = uploads.add(
                s,
                svc.settings,
                svc.key,
                w.cid,
                filename=f.name,
                data=f.read_bytes(),
                actor=sw.OWNER,
            )
            enqueue(s, "upload", {"upload_id": str(up.id)})
        s.add(BusinessMessage(text=sw.MESSAGE, data_lives=["Order tracker"] if tracker else []))
        s.scalars(sa.select(SetupState)).one().current_step = "metrics"
        s.commit()
    _jobs(w)
    with svc.sf() as s, tenant_scope(w.cid):
        s.add(
            SetupDraft(
                kind="meta",
                key="drafting",
                body={"state": "queued", "mode": "draft"},
                origin={"by": "system"},
            )
        )
        enqueue(s, "draft", {"mode": "draft", "actor": sw.OWNER})
        s.commit()
    if run:
        _jobs(w)
    if tracker:
        with svc.sf() as s, tenant_scope(w.cid):
            s.add(
                SetupDraft(
                    kind="metric",
                    key="order_book_value",
                    body={
                        "name": "Order book value",
                        "status": "needs_source",
                        "kind": "snapshot",
                        "grain": "day",
                        "description": "Open orders not yet invoiced",
                        "needs": "Order tracker FY27 · Open orders (Google Sheets)",
                    },
                    origin={"by": "model"},
                    state="needs_source",
                    confidence="check",
                    position=0,
                )
            )
            s.commit()


def _mid_drafting(w: World) -> None:
    """Put a finished (re)draft back part-way: the last third of the metrics still drafting."""
    from othor.setup import drafts
    from othor.setup.models import SetupDraft

    with w.svc.sf() as s, tenant_scope(w.cid):
        rows = [r for r in drafts.stored(s) if r.state not in ("excluded_table", "needs_source")]
        for r in rows[len(rows) * 2 // 3 :]:
            r.value = None
        meta = s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "meta")).one()
        meta.body = {**meta.body, "state": "running"}
        s.commit()


@prep
def metrics_drafting(w: World) -> None:
    """2m: drafting by rules, part-way: the last third of the metrics still drafting."""
    _drafted(w, tracker=False)
    _mid_drafting(w)


@prep
def metrics_drafted(w: World) -> dict[str, str]:
    """2n, 2n0, 2na–2nc, 2ng–2ni: metrics drafted by rules, the GST conflict open, Order book
    value waiting for the Order tracker, the data dictionary not uploaded yet."""
    from othor.setup.models import SetupDraft

    _drafted(w)
    with w.svc.sf() as s, tenant_scope(w.cid):
        first = s.scalars(
            sa.select(SetupDraft.key)
            .where(SetupDraft.kind == "discovery")
            .order_by(SetupDraft.position, SetupDraft.key)
        ).first()
    return {"assumption": first or ""}


@prep
def edit_change_computed(w: World) -> None:
    """2nd: Edit on Receivables over 60 days; the threshold changed to 90 days, today's value
    computed, Apply waiting."""
    _drafted(w)
    page = w.env.client.get(
        w.host + "/setup/metrics?panel=chat&purpose=edit&key=receivables_over_60&edit=1"
    ).text
    i = re.search(
        r'name="reply" value="(\d+)">\s*<input type="hidden" name="patch" value="set_threshold"',
        page,
    )
    assert i, "threshold reply"
    _ok(
        w.post(
            "/setup/metrics/receivables_over_60/chat/edit",
            {"reply": i.group(1), "patch": "set_threshold", "value": "90"},
            page="/setup/metrics",
        )
    )
    _jobs(w)


def _tracker_choose_data(w: World) -> str:
    _drafted(w)
    sid = _sheets_signed_in(w, {"setup": "1", "for": "order_book_value"})
    _ok(
        w.post(
            f"/sources/{sid}/files", {"file": "sample-order-tracker-fy27"}, page="/setup/metrics"
        )
    )
    _jobs(w)
    return sid


@prep
def tracker_choose_data(w: World) -> dict[str, str]:
    """2nca: Connect the Order tracker from Metrics: signed in, the spreadsheet chosen, its tabs
    to pick (in the connector panel)."""
    return {"sid": _tracker_choose_data(w)}


@prep
def tracker_connected(w: World) -> dict[str, str]:
    """2ncb: the Order tracker connected from Metrics; Order book value redrafted from it."""
    sid = _tracker_choose_data(w)
    ids = _tab_ids(w, sid)
    r = _ok(
        w.post(
            f"/sources/{sid}/tabs",
            {"tab": [ids["Open orders"], ids["Deals"]]},
            page="/setup/metrics",
        )
    )
    loc = r.headers["location"]
    _ok(w.post(loc + "/confirm", page=loc))
    _jobs(w)
    return {"sid": sid}


def _upload_dictionary(w: World) -> None:
    from tests.fixtures.sample_company.uploads import DICTIONARY, write_dictionary

    (w.tmp / "doc").mkdir(exist_ok=True)
    path = write_dictionary(w.tmp / "doc" / DICTIONARY)
    tok = w.csrf("/setup/metrics")
    _ok(
        w.env.client.post(
            w.host + "/setup/metrics/document",
            data={"csrf": tok},
            files={"file": (DICTIONARY, path.read_bytes(), "application/pdf")},
            follow_redirects=False,
        )
    )


@prep
def document_redrafting(w: World) -> None:
    """2ne: the data dictionary uploaded on Metrics; the redraft with it part-way, what it
    changed so far listed."""
    _drafted(w)
    _upload_dictionary(w)
    _jobs(w)
    _mid_drafting(w)


@prep
def document_redrafted(w: World) -> None:
    """2nf: draft 2, after the data dictionary: what it changed, marked on Metrics."""
    _drafted(w)
    _upload_dictionary(w)
    _jobs(w)


# --- 2o: the first run ----------------------------------------------------------------------------


@prep
def first_run_running(w: World) -> None:
    """2o: metrics confirmed; dry run #0001 part-way: queries, baselines, forecasts and
    detectors done, the brief being written (the app runs forecasts before detectors)."""
    from othor.engine.models import Run, RunStep
    from othor.jobs.models import Job
    from othor.setup import confirm
    from othor.setup.models import SetupDraft
    from tests.integration import setup_world as sw

    _drafted(w, tracker=False)
    with w.svc.sf() as s, tenant_scope(w.cid):
        for r in s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "metric")):
            if r.confidence == "conflict":
                confirm.choose(s, r, "source", actor=sw.OWNER)
        s.commit()
    r = _ok(w.post("/setup/metrics/confirm", page="/setup/metrics"))
    assert r.headers["location"] == "/setup/first-run", r.headers.get("location")
    _jobs(w)
    with w.svc.sf() as s, tenant_scope(w.cid):  # as it is while the brief is written
        run = s.scalars(sa.select(Run)).one()
        run.status, run.run_hash, run.ended_at = "running", None, None
        steps = s.scalars(
            sa.select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.ordinal)
        ).all()
        at = next(st.ordinal for st in steps if st.name == "brief")
        for st in steps:
            if st.ordinal == at:
                st.status = "running"
            elif st.ordinal > at:
                st.status = "queued"
        s.scalars(
            sa.select(Job).where(Job.dedupe_key == confirm.FIRST_RUN_KEY)
        ).one().status = "running"
        s.commit()


@prep
def first_run_left(w: World) -> None:
    """2oa, 2ob (M11c): the owner went to the app while the first brief was being written."""
    first_run_running(w)
    r = w.post("/setup/first-run/continue", page="/setup/first-run")
    assert "othor_first_run=1" in r.headers.get("set-cookie", ""), r.headers


@prep
def first_run_done(w: World) -> None:
    """2oc (M11c): dry run #0001 finished, the first brief written; Open the brief is on."""
    from othor.setup import confirm
    from othor.setup.models import SetupDraft
    from tests.integration import setup_world as sw

    _drafted(w, tracker=False)
    with w.svc.sf() as s, tenant_scope(w.cid):
        for r in s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "metric")):
            if r.confidence == "conflict":
                confirm.choose(s, r, "source", actor=sw.OWNER)
        s.commit()
    r = _ok(w.post("/setup/metrics/confirm", page="/setup/metrics"))
    assert r.headers["location"] == "/setup/first-run", r.headers.get("location")
    _jobs(w)
