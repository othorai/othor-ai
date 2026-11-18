# SPDX-License-Identifier: Apache-2.0
"""The Ontology page (6a, 6a1), the metric panel (6a2, 10n), Change with Othor AI (6a3) and
History with Compare and Restore (6a4) — M10b.

Every change writes one Ontology version (refused as "No change" when it changes neither
records nor citations) and its event, and applies from the next run. Another company's metric or
version is a 404.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

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
