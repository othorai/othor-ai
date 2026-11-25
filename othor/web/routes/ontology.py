# SPDX-License-Identifier: Apache-2.0
"""The Ontology page (6a, 6a1), the metric panel (6a2, 10n), Change with Othor AI (6a3) and
History with Compare and Restore (6a4) — M10b.

Every change writes one Ontology version (refused as "No change" when it changes neither
records nor citations) and its event, and applies from the next run. Another company's metric or
version is a 404.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from othor.core import fmt
from othor.core.localtime import local_date
from othor.core.tenancy import tenant_scope
from othor.ontology import service
from othor.ontology.compiler import compile_version
from othor.ontology.models import OntologyVersion
from othor.web import ontology_view as ov
from othor.web.context import services
from othor.web.routes.auth import not_found, page
from othor.web.routes.pages import EMPTY, Owner, shell_ctx, signed_in
from othor.web.security import check_csrf

router = APIRouter(include_in_schema=False)
# the 404 (9a): what isn’t there, and one way back
GONE = (
    "There’s no such record in the Business Model",
    ("Business Model", "/ontology"),
)


def provider_for(settings: Any) -> Any:
    from othor.ai.gateway import provider_for as real

    return real(settings)


CSRF = [Depends(check_csrf)]
NO_CHANGE = "No change: that is already in your Business Model."


def _today(request: Request, o: Owner) -> dt.date:
    return local_date(o.company, services(request).clock())


def _empty(request: Request, o: Owner) -> Response:
    title, empty, text = EMPTY["ontology"]
    return page(
        request,
        "pages/area_empty.html",
        {**shell_ctx(o, "ontology"), "title": title, "empty": empty, "text": text, "resume": None},
    )


@router.get("/ontology", response_class=HTMLResponse)
def ontology(
    request: Request,
    numbers: str | None = None,
    metric: str | None = None,
    panel: str | None = None,
    subject: str = "page",
    compare: str | None = None,
    restore: int | None = None,
    done: int | None = None,
    decided: str | None = None,
    note: str | None = None,
    text: str | None = None,
    o: Owner = Depends(signed_in),
) -> Response:
    svc = services(request)
    today = _today(request, o)
    with svc.sf() as s, tenant_scope(o.company.id):
        if not service.confirmed(s):
            s.expunge_all()
            return _empty(request, o)
        ctx: dict[str, Any] = {
            **shell_ctx(o, "ontology"),
            **ov.page(s, svc.settings, o.company, today, numbers),
        }
        # Proposals for the Business Model, at the top of this page (owner, 23 Nov 2026)
        from othor.web import home_view as hv
        from othor.web.routes.home import _proposal, _with_decided

        ctx["proposals"] = hv.proposal_rows(s)
        from othor.ontology import size_numbers

        ctx["size_lead"] = size_numbers.lead_line(s)  # #76: the size, above its suggestions
        if decided and (row := _proposal(s, decided)) is not None and row.status != "open":
            ctx["proposals"] = _with_decided(s, ctx["proposals"], row)
        # the tabs (owner, 23 Nov 2026): the full Numbers list opens on its tab
        tabs = {"business", "numbers", "patterns"} | ({"level"} if ctx.get("levels") else set())
        want = request.query_params.get("tab") or ""
        if request.query_params.get("level") == "open":
            want = "level"
        on_numbers = ctx.get("expanded") or metric  # a number's panel opens over its own tab
        ctx["tab"] = want if want in tabs else ("numbers" if on_numbers else "business")
        if ctx.get("levels"):
            ctx["levels"]["open"] = True  # the level's own tab shows its checklist open; Hide shuts
        if request.query_params.get("level") == "open" and ctx.get("levels"):
            ctx["levels"]["open"] = True  # 2.4 L3: back from Look in my files, the list open
        if metric:
            mp = ov.metric_panel(s, svc.settings, o.company, metric, today)
            if mp is None:
                return not_found(request, o.site, f"There’s no number called {metric}", GONE[1])
            ctx["metric"] = mp
        if panel == "history":
            ctx["history"] = {
                "rows": ov.history(s, ov.user_names(s)),
                "replay": ov.replay_card(s),
                "restore": restore,
            }
            if compare:
                try:
                    a, b = (int(x) for x in compare.split("-", 1))
                except ValueError:
                    return not_found(request, o.site, *GONE)
                cmp_ = ov.compare(s, a, b)
                if cmp_ is None:
                    return not_found(request, o.site, *GONE)
                ctx["history"]["compare"] = cmp_
        if done:
            v = s.scalars(sa.select(OntologyVersion).where(OntologyVersion.number == done)).first()
            if v is not None:
                ctx["done"] = {"number": v.number, "reason": v.reason}
        if note == "nochange":
            ctx["note"] = NO_CHANGE
        elif note == "ceiling":
            ctx["note"] = service.ceiling_text()
        s.expunge_all()
    return page(request, "ontology/ontology.html", ctx)


@router.post("/ontology/level/find", dependencies=CSRF)
def level_find(
    request: Request, back: str = Form("/ontology"), o: Owner = Depends(signed_in)
) -> Response:
    """2.4 L3: Othor AI looks in the files and sources for the checklist's missing items and
    drafts what it finds (each a set to apply; nothing written). Without a model, nothing."""
    from othor.ai.gateway import ModelGateway
    from othor.chat import level_drafts
    from othor.web.context import local_path

    svc = services(request)
    gw = ModelGateway(
        svc.sf,
        provider_for(svc.settings),
        clock=svc.clock,
        monthly_tokens=svc.settings.ai_monthly_tokens,
    )
    with svc.sf() as s, tenant_scope(o.company.id):
        if service.confirmed(s):
            level_drafts.find(s, gw, o.company, actor=str(o.viewer.id), today=_today(request, o))
            s.commit()
    url = local_path(back, "/ontology")
    return RedirectResponse(url + ("&" if "?" in url else "?") + "level=open", 303)


EXAMPLE_PDF = (
    Path(__file__).resolve().parents[1] / "static" / "docs" / "complete-business-model.pdf"
)


@router.get("/ontology/example.pdf")
def example_pdf(o: Owner = Depends(signed_in)) -> Response:
    """2.4 L4: "See a complete Business Model": the sample company's, definitions only, built by
    scripts/build_example_business_model.py from the sample's hand-built Business Model."""
    return FileResponse(
        EXAMPLE_PDF,
        media_type="application/pdf",
        filename="Othor AI · a complete Business Model.pdf",
        headers={"Cache-Control": "private, max-age=3600"},
    )


# --- 6a3 · Change with Othor AI: the one Othor AI chat since 0.26 (othor.chat.changes) ------


# --- versions: Undo, Restore, Remove ---------------------------------------------------------


def _version(s: Session, n: int) -> OntologyVersion | None:
    return s.scalars(sa.select(OntologyVersion).where(OntologyVersion.number == n)).first()


@router.post("/ontology/decided/{rid}/confirm", dependencies=CSRF)
def confirm_decided(request: Request, rid: str, o: Owner = Depends(signed_in)) -> Response:
    """Confirm a decision by Othor AI (v0.12): it becomes the owner's, the tag goes and the
    confidence cap lifts from the next run (one version, one event)."""
    from othor.home import answers

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        r = next((x for x in answers.decided_records(s) if str(x.id) == rid), None)
        if r is None:
            return not_found(request, o.site, *GONE)
        if answers.confirm_decision(s, r, actor=str(o.viewer.id)) is not None:
            compile_version(s, o.company, actor=str(o.viewer.id))
        s.commit()
    return RedirectResponse("/ontology#decided", 303)


@router.post("/ontology/versions/{n}/undo", dependencies=CSRF)
def undo(
    request: Request, n: int, back: str = Form("/ontology"), o: Owner = Depends(signed_in)
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        if _version(s, n) is None:
            return not_found(request, o.site, f"There’s no Business Model v{n}", GONE[1])
        nv = service.undo(s, n, actor=str(o.viewer.id))
        if nv is not None:
            compile_version(s, o.company, actor=str(o.viewer.id))
        s.commit()
    target = back if back.startswith("/ontology") else "/ontology"
    return RedirectResponse(
        target if nv is not None else target + ("&" if "?" in target else "?") + "note=nochange",
        303,
    )


@router.post("/ontology/versions/{n}/restore", dependencies=CSRF)
def restore(request: Request, n: int, o: Owner = Depends(signed_in)) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        if _version(s, n) is None:
            return not_found(request, o.site, f"There’s no Business Model v{n}", GONE[1])
        nv = service.restore(s, n, actor=str(o.viewer.id))
        if nv is None:
            return RedirectResponse("/ontology?panel=history&note=nochange", 303)
        compile_version(s, o.company, actor=str(o.viewer.id))
        s.commit()
        m = nv.number
    return RedirectResponse(f"/ontology?panel=history&done={m}", 303)


@router.post("/ontology/floor", dependencies=CSRF)
def set_floor(request: Request, value: str = Form(""), o: Owner = Depends(signed_in)) -> Response:
    """#107 (owner, 21 Nov 2026): the floor, changed in place on The business. Typed as money
    ("₹5 L", "5 lakh", "500000"); saved as the same `materiality` record Settings wrote (M15.6),
    so the night compiles it as before: one version, applied from tonight, and an event with
    who changed it from what to what. The line comes back in place with a toast and Undo."""
    from othor.core.events import record_event
    from othor.ontology.types import validate_body
    from othor.web.errors import toast

    try:
        amount = ov.parse_floor(value)
    except ValueError:
        return toast(request, "Enter an amount above zero, like ₹5 L or 500000", 400)
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        if not service.confirmed(s):
            return not_found(request, o.site, *GONE)
        before = ov.floor_line(s, svc.settings, o.company)
        body = validate_body("materiality", {"amount": f"{amount:f}", "source": "owner"})
        v = service.apply(
            s,
            [service.put("materiality", "company", body)],
            actor=str(o.viewer.id),
            reason=f"{ov.FLOOR_LEAD}: over {fmt.money(amount)}",
        )
        done = None
        if v is not None:
            record_event(
                s,
                actor=o.viewer.id,
                action="ontology.floor_changed",
                object_type="ontology_version",
                object_id=v.id,
                before={"floor": f"{before['amount']:f}", "owner": before["owner"]},
                after={"floor": f"{amount:f}", "owner": True},
            )
            compile_version(s, o.company, actor=str(o.viewer.id))
            done = v.number
        s.commit()
        floor = ov.floor_line(s, svc.settings, o.company)
        s.expunge_all()
    return page(request, "ontology/_floor_row.html", {"floor": floor, "done": done})


@router.post("/ontology/metric/{key}/area", dependencies=CSRF)
def set_area(
    request: Request, key: str, area: str = Form(""), o: Owner = Depends(signed_in)
) -> Response:
    """The metric panel's Area (M16.1): the area Summary groups this number under, from the short
    list or the owner's own name. A grouping, not a change of meaning: a new version, no new
    series."""
    from othor.ontology import areas
    from othor.web.errors import toast

    try:
        value = areas.clean(area)
    except ValueError as e:
        return toast(request, str(e).capitalize(), 400)
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        rec = next((r for r in service.active(s, ["metric"]) if r.key == key), None)
        if rec is None:
            return not_found(request, o.site, *GONE)
        body = {k: v for k, v in rec.body.items() if k != "area"}
        if value:
            body["area"] = value
        v = service.apply(
            s,
            [service.put("metric", key, body)],
            actor=str(o.viewer.id),
            reason=f"Area of {rec.body.get('name') or key}: {areas.label(value)}",
        )
        if v is not None:
            compile_version(s, o.company, actor=str(o.viewer.id))
        s.commit()
    return RedirectResponse(f"/ontology?metric={key}", 303)


@router.post("/ontology/metric/{key}/remove", dependencies=CSRF)
def remove_metric(request: Request, key: str, o: Owner = Depends(signed_in)) -> Response:
    """10n's Remove this number: a metric that needs a source is retired (a new version; Undo)."""
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        rec = next((r for r in service.active(s, ["metric"]) if r.key == key), None)
        if rec is None or rec.body.get("status") != "needs_source":
            return not_found(request, o.site, *GONE)
        v = service.apply(
            s,
            [service.retire("metric", key)],
            actor=str(o.viewer.id),
            reason=f"Removed {rec.body.get('name') or key} (needed a source)",
        )
        compile_version(s, o.company, actor=str(o.viewer.id))
        s.commit()
        n = v.number if v else None
    return RedirectResponse(f"/ontology?done={n}" if n else "/ontology", 303)
