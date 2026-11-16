# SPDX-License-Identifier: Apache-2.0
"""Sweep (boards 5a–5f, 10i) and the finding panel (5b, 5c, 4f–4f3, 10p).

Pages read one run (the latest detecting run, or `?run=<number>` among the last seven) through
`sweep_view`, with the owner's snoozes and mutes applied as the overlay. Owner actions write a
row and an event and return to the page; the engine applies snoozes and mutes from the next
run. Every route is inside the company's tenant scope; another company's fingerprint is a 404.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from othor.core import features
from othor.core.events import record_event
from othor.core.localtime import local_date
from othor.core.tenancy import tenant_scope
from othor.engine import holds, watches
from othor.engine.models import Run
from othor.web import sweep_view as sv
from othor.web.context import services
from othor.web.routes.auth import not_found, page
from othor.web.routes.pages import EMPTY, Owner, shell_ctx, signed_in
from othor.web.security import check_csrf

router = APIRouter(include_in_schema=False)
# the 404 (9a): what isn’t there, and one way back
GONE = (
    "There’s no such finding",
    ("Sweep", "/sweep"),
)
CSRF = [Depends(check_csrf)]
TABS = ("findings", "watching", "suppressed", "resolved")


def _today(request: Request, o: Owner) -> dt.date:
    return local_date(o.company, services(request).clock())


def _run(s: Session, number: int | None) -> Run | None:
    runs = sv.detecting_runs(s)
    if number is None:
        return runs[0] if runs else None
    return next((r for r in runs if r.number == number), None)


def _view(request: Request, s: Session, o: Owner, number: int | None) -> sv.RunView | None:
    run = _run(s, number)
    if run is None:
        return None
    return sv.load(s, services(request).settings, run, _today(request, o))


def _page_ctx(request: Request, s: Session, o: Owner, rv: sv.RunView, tab: str) -> dict[str, Any]:
    """Sweep, simplified (M15.2, the owner's design of 12 Nov 2026): the status strip, three
    numbers, the tabs; on Findings, every open finding then the detectors table."""
    base = f"/sweep?run={rv.run.number}"
    early = sv.first_days(rv, s)
    st = sv.strip(rv, s)
    ctx: dict[str, Any] = {
        **shell_ctx(o, "sweep"),
        "rv": rv,
        "run": rv.run.number,
        "summary": sv.summary(rv, s),
        "tabs": sv.tabs(rv, base),
        "tab": tab if tab in TABS else "findings",
        # each run as the header dates it, with its kind: two runs of one day read apart (#39)
        "runs": [(r.number, r.run_date, sv.RUN_KIND.get(r.kind, "")) for r in sv.detecting_runs(s)],
        "strip": st,
        "strip_info": sv.strip_info(st),
        "history": sv.history_counts(rv),
        "findings": sv.finding_rows(rv, s),
        "early": early,
        "detectors": sv.detector_rows(rv, s),
        "watching": sv.watching(rv, s),
        "suppressed": sv.suppressed(rv, s),
        "resolved": sv.resolved(rv),
    }
    if features.BRIEFS:  # Add to a brief (10o): the banner after adding
        added = request.query_params.get("added")
        if added:
            from othor.briefs import service

            try:
                b = service.get(s, uuid.UUID(added))
            except ValueError:
                b = None
            ctx["added"] = b.name if b else None
    panel = request.query_params.get("panel")
    if panel == "questions":  # "Answer 1 question", in place (owner, 13 Nov 2026)
        from othor.ontology.models import Question
        from othor.web import home_view as hv

        q = request.query_params.get("q")
        cur = None
        try:
            cur = s.scalars(sa.select(Question).where(Question.id == uuid.UUID(q or ""))).first()
        except ValueError:
            cur = None
        ctx["chat"] = hv.questions_panel(
            s, o.company, _today(request, o), current=cur, src=f"sweep-{rv.run.number}"
        )
    elif panel == "history" and early:  # "Which numbers?" (the history note)
        ctx["history_panel"] = early
    return ctx


def _empty(request: Request, o: Owner) -> Response:
    title, empty, text = EMPTY["sweep"]
    return page(
        request,
        "pages/area_empty.html",
        {**shell_ctx(o, "sweep"), "title": title, "empty": empty, "text": text, "resume": None},
    )


@router.api_route("/runs/latest", methods=["GET", "HEAD"], include_in_schema=False)
def latest_run(request: Request, o: Owner = Depends(signed_in)) -> Response:
    """The stale-page check (9a, M11a): the newest finished run's number, in a header."""
    from othor.engine.models import Run

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        n = s.scalar(sa.select(sa.func.max(Run.number)).where(Run.status.in_(("done", "partial"))))
    return Response(
        status_code=204, headers={"X-Othor-Run": str(n or 0), "Cache-Control": "no-store"}
    )


@router.get("/sweep", response_class=HTMLResponse)
def sweep(
    request: Request,
    run: int | None = None,
    tab: str = "findings",
    o: Owner = Depends(signed_in),
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        rv = _view(request, s, o, run)
        if rv is None:
            return (
                _empty(request, o)
                if run is None
                else not_found(request, o.site, f"There’s no run #{run}", GONE[1])
            )
        ctx = _page_ctx(request, s, o, rv, tab)
        s.expunge_all()
    return page(request, "sweep/sweep.html", ctx)


@router.get("/sweep/finding/{fp}", response_class=HTMLResponse)
def finding(
    request: Request,
    fp: str,
    run: int | None = None,
    tab: str = "finding",
    o: Owner = Depends(signed_in),
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        rv = _view(request, s, o, run)
        f = sv.finding(rv, fp) if rv else None
        if rv is None or f is None:
            return not_found(request, o.site, *GONE)
        ctx = _page_ctx(request, s, o, rv, "findings")
        ctx["panel"] = sv.panel(rv, s, f, tab)
        if features.BRIEFS and f["status"] == "open":  # Add to a brief, as a dialog (M15.3)
            from othor.web import brief_view as bvw

            ctx["add_dialog"] = {
                "fp": fp,
                "title": (f.get("text") or {}).get("title", ""),
                "money": (f.get("text") or {}).get("money", ""),
                "options": bvw.add_options(s, services(request).settings, f),
            }
        s.expunge_all()
    return page(request, "sweep/sweep.html", ctx)


@router.get("/sweep/finding/{fp}/result.csv")
def result_csv(
    request: Request, fp: str, run: int | None = None, o: Owner = Depends(signed_in)
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        rv = _view(request, s, o, run)
        f = sv.finding(rv, fp) if rv else None
        if rv is None or f is None:
            return not_found(request, o.site, *GONE)
        body = sv.result_csv(rv, f)
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{f["metric"]}-run{rv.run.number}.csv"'
        },
    )


@router.get("/sweep/run/{number}/log.json")
def run_log(request: Request, number: int, o: Owner = Depends(signed_in)) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        rv = _view(request, s, o, number)
        if rv is None:
            return not_found(request, o.site, f"There’s no run #{number}", GONE[1])
        doc = sv.run_log(rv, s)
    return Response(
        json.dumps(doc, indent=1, sort_keys=True, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="run-{number:04d}.json"'},
    )


@router.post("/sweep/run/{number}/replay", dependencies=CSRF)
def replay_run(
    request: Request, number: int, fp: str = Query(""), o: Owner = Depends(signed_in)
) -> Response:
    """Replay on demand (4f3): re-run from the stored inputs, write nothing but an event."""
    from othor.engine.context import EngineContext
    from othor.engine.replay import replay

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        run = _run(s, number)
        if run is None:
            return not_found(request, o.site, f"There’s no run #{number}", GONE[1])
        run_id = run.id
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=svc.clock)
    with tenant_scope(o.company.id):
        r = replay(ctx, run_id)
    with svc.sf() as s, tenant_scope(o.company.id):
        record_event(
            s,
            actor=str(o.viewer.id),
            action="run.replayed",
            object_type="run",
            object_id=run_id,
            after={"ok": r.ok, "output_hash": r.replayed_hash, "differences": len(r.diff)},
        )
        s.commit()
    back = f"/sweep/finding/{fp}?run={number}&tab=run" if fp else f"/sweep?run={number}"
    return RedirectResponse(back, 303)


def _hold(request: Request, o: Owner, fp: str, run: int | None, action: str) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        rv = _view(request, s, o, run)
        f = sv.finding(rv, fp) if rv else None
        if rv is None or f is None:
            return not_found(request, o.site, *GONE)
        actor = str(o.viewer.id)
        if action == "snooze":
            holds.snooze(
                s,
                {**f, "title": (f.get("text") or {}).get("title")},
                actor=actor,
                today=_today(request, o),
            )
        elif action == "mute":
            holds.mute(s, {**f, "title": (f.get("text") or {}).get("title")}, actor=actor)
        else:
            holds.undo(s, fp, actor=actor, now=svc.clock())
        s.commit()
        number = rv.run.number
    back = back_to(request.query_params.get("back"))
    if back:  # Snooze and Mute from a brief card return to the brief (M15.3)
        return RedirectResponse(back, 303)
    tab = "findings" if action == "undo" else "suppressed"
    return RedirectResponse(f"/sweep?run={number}&tab={tab}", 303)


def back_to(value: str | None) -> str | None:
    """Where a hold returns when it came from a brief: "brief-<uuid>" only, so `back` can never
    send the owner anywhere else (M15.3)."""
    import re

    if value and re.fullmatch(
        r"brief-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value
    ):
        return f"/brief/{value[6:]}"
    return None


@router.post("/sweep/finding/{fp}/snooze", dependencies=CSRF)
def snooze(
    request: Request, fp: str, run: int | None = None, o: Owner = Depends(signed_in)
) -> Response:
    return _hold(request, o, fp, run, "snooze")


@router.post("/sweep/finding/{fp}/mute", dependencies=CSRF)
def mute(
    request: Request, fp: str, run: int | None = None, o: Owner = Depends(signed_in)
) -> Response:
    return _hold(request, o, fp, run, "mute")


@router.post("/sweep/finding/{fp}/undo", dependencies=CSRF)
def undo(
    request: Request, fp: str, run: int | None = None, o: Owner = Depends(signed_in)
) -> Response:
    return _hold(request, o, fp, run, "undo")


@router.post("/sweep/finding/{fp}/pause", dependencies=CSRF)
def known_pause(
    request: Request,
    fp: str,
    run: int | None = None,
    until: str = Form(...),
    reason: str = Form(""),
    o: Owner = Depends(signed_in),
) -> Response:
    """Add a known pause (10p): the owner's own entry, applied to the Ontology as a known
    pattern on the entity until a date; Sweep stops testing it from the next run."""
    from othor.ontology import service

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        rv = _view(request, s, o, run)
        f = sv.finding(rv, fp) if rv else None
        if rv is None or f is None or f["detector"] != "missing_records" or not f.get("entities"):
            return not_found(request, o.site, *GONE)
        try:
            end = dt.date.fromisoformat(until)
        except ValueError:
            return RedirectResponse(f"/sweep/finding/{fp}?run={rv.run.number}", 303)
        name = f["entities"][0]
        start = dt.date.fromisoformat((f.get("slots") or {}).get("last") or f["window"][0])
        today = _today(request, o)
        key = "pause_" + "".join(c if c.isalnum() else "_" for c in name.lower())[:40]
        body = {
            "name": (reason.strip() or f"{name} paused")[:200],
            "window": {"kind": "range", "start": start.isoformat(), "end": end.isoformat()},
            "entity": name,
            "answered_on": today.isoformat(),
        }
        service.apply(
            s,
            [service.put("known_pattern", key, body, {"from": "sweep", "finding": fp})],
            actor=str(o.viewer.id),
            reason=f"Known pause added from Sweep for {name}",
        )
        s.commit()
        number = rv.run.number
    return RedirectResponse(f"/sweep/finding/{fp}?run={number}", 303)


@router.get("/sweep/watch/{fp}", response_class=HTMLResponse)
def watch_new(
    request: Request,
    fp: str,
    run: int | None = None,
    threshold: str | None = None,
    o: Owner = Depends(signed_in),
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        rv = _view(request, s, o, run)
        f = sv.finding(rv, fp) if rv else None
        if rv is None or f is None:
            return not_found(request, o.site, *GONE)
        thr = _decimal(threshold)
        draft = sv.watch_draft(rv, f, thr)
        ctx = _page_ctx(request, s, o, rv, "findings")
        ctx["watch"] = draft
        ctx["watch_from"] = {
            "number": next((c["number"] for c in ctx["findings"] if c["fp"] == fp), ""),
            "label": (f.get("text") or {}).get("label", f["detector"]),
        }
        s.expunge_all()
    return page(request, "sweep/sweep.html", ctx)


def _decimal(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    try:
        return str(Decimal(v.strip()))
    except InvalidOperation:
        return None


@router.post("/sweep/watch/{fp}/start", dependencies=CSRF)
def watch_start(
    request: Request,
    fp: str,
    run: int | None = None,
    threshold: str = Form(...),
    owner: str = Form(""),
    o: Owner = Depends(signed_in),
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        rv = _view(request, s, o, run)
        f = sv.finding(rv, fp) if rv else None
        thr = _decimal(threshold)
        if rv is None or f is None:
            return not_found(request, o.site, *GONE)
        draft = sv.watch_draft(rv, f, thr)
        if draft is None or thr is None:
            return RedirectResponse(f"/sweep/watch/{fp}?run={rv.run.number}", 303)
        names = draft.owners
        who = owner if owner in names else draft.owner
        watches.create(
            s,
            label=draft.label,
            indicator=draft.indicator,
            threshold=thr,
            direction=draft.direction,
            owner_name=who,
            since=_today(request, o),
            actor=str(o.viewer.id),
            from_finding=fp,
        )
        s.commit()
        number = rv.run.number
    return RedirectResponse(f"/sweep?run={number}&tab=watching", 303)


@router.post("/sweep/watching/{wid}/stop", dependencies=CSRF)
def watch_stop(
    request: Request, wid: uuid.UUID, run: int | None = None, o: Owner = Depends(signed_in)
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        w = holds.stop_watch(s, wid, actor=str(o.viewer.id), now=svc.clock())
        if w is None:
            return not_found(request, o.site, *GONE)
        s.commit()
    q = f"?run={run}&tab=watching" if run else "?tab=watching"
    return RedirectResponse(f"/sweep{q}", 303)
