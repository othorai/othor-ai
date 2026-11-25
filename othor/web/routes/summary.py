# SPDX-License-Identifier: Apache-2.0
"""Summary (v0.16, M16.3): one row per area, its lead number against a comparison, expandable,
reorderable and hideable. Owners only (`signed_in`); every action is an in-place POST that saves
the owner's layout (an event each) and comes back to /summary. The figures come from
`summary_view`, read from the last run's stored series."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from othor.core.localtime import local_date
from othor.core.tenancy import tenant_scope
from othor.web import summary_layout as sl
from othor.web import summary_view as sv
from othor.web.context import services
from othor.web.errors import toast
from othor.web.routes.auth import page
from othor.web.routes.pages import Owner, shell_ctx, signed_in
from othor.web.security import check_csrf

router = APIRouter(include_in_schema=False)
CSRF = [Depends(check_csrf)]


def _back(all_: str) -> RedirectResponse:
    return RedirectResponse("/summary?all=1" if all_ == "1" else "/summary", 303)


@router.get("/summary", response_class=HTMLResponse)
def summary(
    request: Request,
    window: str | None = None,
    compare: str | None = None,
    show: str | None = None,
    split: str | None = None,
    rows: str | None = None,
    all: str | None = None,
    o: Owner = Depends(signed_in),
) -> Response:
    svc = services(request)
    query = {
        k: v
        for k, v in (
            ("window", window),
            ("compare", compare),
            ("show", show),
            ("split", split),
            ("rows", rows),
        )
        if v is not None
    }
    with svc.sf() as s, tenant_scope(o.company.id):
        sm = sv.page(s, svc.settings, local_date(o.company, svc.clock()), query, expanded=True)
        sm["proposal_open"] = _areas_proposal_open(s)
    sm["all"] = all == "1"
    return page(request, "pages/summary.html", {**shell_ctx(o, "summary"), "sm": sm})


def _areas_proposal_open(s: Any) -> bool:
    import sqlalchemy as sa

    from othor.engine.models import Proposal

    return (
        s.scalars(
            sa.select(Proposal.id).where(Proposal.kind == "areas", Proposal.status == "open")
        ).first()
        is not None
    )


@router.post("/summary/view", dependencies=CSRF)
def set_view(
    request: Request,
    window: str = Form(""),
    compare_on: str = Form(""),
    compare: str = Form(""),
    show: str = Form(""),
    split: str = Form(""),
    rows: str = Form(""),
    all_: str = Form("", alias="all"),
    o: Owner = Depends(signed_in),
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        splits = sv.splits_of(s, svc.settings)
        saved = sl.get(s)["view"]
        # Compare off: no comparison line; on: the period before unless last year is picked
        compare = (compare if compare in ("before", "year") else "before") if compare_on else "none"
        got = {"window": window, "compare": compare, "show": show, "split": split, "rows": rows}
        view = sv.View.parse({k: v for k, v in got.items() if v or k == "split"}, saved, splits)
        sl.set_view(s, view.as_dict(), actor=str(o.viewer.id))
        s.commit()
    return _back(all_)


@router.post("/summary/hide", dependencies=CSRF)
def hide(
    request: Request,
    area: str = Form(""),
    metric: str = Form(""),
    all_: str = Form("", alias="all"),
    o: Owner = Depends(signed_in),
) -> Response:
    """Hide an area, or one number in the Every number view (owner, 14 Nov 2026)."""
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        try:
            if metric:
                known = [m for ms in sv.area_metrics(s, svc.settings).values() for m in ms]
                sl.hide_number(s, metric, known=known, actor=str(o.viewer.id))
            else:
                known = list(sv.area_metrics(s, svc.settings))
                sl.hide(s, area, known=known, actor=str(o.viewer.id))
        except sl.LayoutError as e:
            return toast(request, str(e), 400)
        s.commit()
    return _back(all_)


@router.post("/summary/show", dependencies=CSRF)
def show_area(
    request: Request,
    area: str = Form(""),
    metric: str = Form(""),
    all_: str = Form("", alias="all"),
    o: Owner = Depends(signed_in),
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        if metric:
            sl.show_number(s, metric, actor=str(o.viewer.id))
        else:
            sl.show(s, area, actor=str(o.viewer.id))
        s.commit()
    return _back(all_)


@router.post("/summary/merge", dependencies=CSRF)
def merge(
    request: Request,
    keep: str = Form(""),
    drop: str = Form(""),
    o: Owner = Depends(signed_in),
) -> Response:
    """Summary 1.1: two numbers that give the same values, drafted as Home's same-number
    proposal when the night hasn't drafted it yet, then opened in the Othor AI panel. A draft
    only: Apply there keeps one (non-negotiable 6)."""
    import sqlalchemy as sa

    from othor.engine import proposals
    from othor.engine.models import Proposal
    from othor.web import run_cache
    from othor.web.ontology_view import run_data

    svc = services(request)
    fp = "duplicate:" + ":".join(sorted((keep, drop)))
    with svc.sf() as s, tenant_scope(o.company.id):
        found = s.scalars(sa.select(Proposal).where(Proposal.fingerprint == fp)).first()
        if found is None:
            rd = run_data(s, svc.settings)
            if rd.run is None or rd.files is None or not rd.files.series.exists():
                return toast(request, "Summary has no run to read yet.", 400)
            pts = run_cache.points(rd.files.series)
            pairs = proposals.same_numbers(rd.spec, pts)
            if not any({k, d} == {keep, drop} for k, d, _n, _g in pairs):
                return toast(request, "These two numbers no longer give the same values.", 400)
            proposals.duplicate(s, rd.run, rd.spec, pts)
            found = s.scalars(sa.select(Proposal).where(Proposal.fingerprint == fp)).first()
            s.commit()
        if found is None or found.status != "open":
            return toast(request, "This one is already decided on Home.", 400)
        pid = found.id
    return RedirectResponse(f"/summary?panel=othor&subject=proposal:{pid}", 303)


@router.post("/summary/lead", dependencies=CSRF)
def lead(
    request: Request,
    area: str = Form(...),
    metric: str = Form(...),
    all_: str = Form("", alias="all"),
    o: Owner = Depends(signed_in),
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        metrics = sv.area_metrics(s, svc.settings).get(area, [])
        try:
            sl.set_lead(s, area, metric, metrics=metrics, actor=str(o.viewer.id))
        except sl.LayoutError as e:
            return toast(request, str(e), 400)
        s.commit()
    return _back(all_)
