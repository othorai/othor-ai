# SPDX-License-Identifier: Apache-2.0
"""The Morning Brief (row 4, 10g, 10j, 10l): one stored issue per brief per night.

`/brief` opens the first brief; `/brief/<id>` its latest issue, `?date=` an earlier one,
`?view=full` the full detail (4d1). The finding panel (4g, 4f–4f3, 10p) opens over the brief
with the brief's context; its actions are Sweep's. Every route is inside the company's tenant
scope; another company's brief is a 404.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any
from urllib.parse import urlencode

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from othor.core import fmt
from othor.core.localtime import local_date
from othor.core.tenancy import tenant_scope
from othor.web import brief_view as bvw
from othor.web import sweep_view as sv
from othor.web.context import services
from othor.web.routes.auth import not_found, page
from othor.web.routes.pages import EMPTY, Owner, shell_ctx, signed_in
from othor.web.security import check_csrf

router = APIRouter(include_in_schema=False)
TODAY = ("Today’s brief", "/brief")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _day_label(raw: str) -> str:
    """ "2026-02-31" → "31 Feb" (9a), even for a day that doesn't exist."""
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", raw.strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        return "that day"
    return f"{int(m.group(3))} {MONTHS[int(m.group(2)) - 1]}"


# the 404 (9a): what isn’t there, and one way back
GONE = (
    "There’s no such brief",
    ("Morning Brief", "/brief"),
)
CSRF = [Depends(check_csrf)]


def _today(request: Request, o: Owner) -> dt.date:
    return local_date(o.company, services(request).clock())


def _uuid(v: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(v)
    except ValueError:
        return None


def _date(v: str | None) -> dt.date | None:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        return None


def _first_writing(s: Session, svc: Any, o: Owner) -> Any:
    """The first run while it writes the first brief (or stopped before it), else None."""
    from othor.jobs.models import Job
    from othor.setup.confirm import FIRST_RUN_KEY
    from othor.web import run_view

    job = s.scalars(sa.select(Job.status).where(Job.dedupe_key == FIRST_RUN_KEY)).first()
    if job is None or job == "done":  # no setup run, or it's over: the briefs as usual
        return None
    view = run_view.first_run(s, svc.settings, o.company, svc.clock())
    return view if view is not None and not view.brief_ready and not view.finished else None


def _empty(request: Request, o: Owner) -> Response:
    title, empty, text = EMPTY["brief"]
    return page(
        request,
        "pages/area_empty.html",
        {**shell_ctx(o, "brief"), "title": title, "empty": empty, "text": text, "resume": None},
    )


def _ctx(
    request: Request, s: Session, o: Owner, bv: bvw.BriefView, view: str, readonly: bool = False
) -> dict[str, Any]:
    settings = services(request).settings
    return {**shell_ctx(o, "brief"), **issue_ctx(s, bv, view, settings, readonly=readonly)}


def sv_shipped() -> Any:
    from othor.web.sweep_view import shipped

    return shipped()


def issue_ctx(
    s: Session, bv: bvw.BriefView, view: str, settings: Any = None, *, readonly: bool = False
) -> dict[str, Any]:
    """One stored issue for `brief/_issue.html`: the owner's page and the recipients' link.
    With `settings`, the brief's own weather and outlook (v0.12 part 1c) at its head; on a
    read-only page (`readonly`) the outlook has no panel links."""
    from othor.briefs import weather
    from othor.web import again_view, weather_view

    base = f"/brief/{bv.brief.id}"
    q = f"date={bv.issue.date.isoformat()}" if bv.issue else ""
    if bv.issue is not None and not bv.latest_of_day:  # the 06:00 issue after a Run again now
        q += f"&issue={bv.issue.id}"
    keep = "&".join(x for x in (q, "view=full" if view == "full" else "") if x)
    here = f"{base}?{keep}&" if keep else f"{base}?"
    wx, wx_outlook = (
        weather_view.brief_weather(settings, bv.run, bv.brief.id, None if readonly else here)
        if settings is not None and bv.issue is not None and weather.IN_BRIEFS
        else (None, None)
    )
    return {
        "wx": wx,
        "wx_outlook": wx_outlook,
        "wx_close": here[:-1],
        "bv": bv,
        "b": bv.body,
        "words": bv.words,
        "base": base,
        "q": q,
        "view": "full" if view == "full" else "simple",
        "tabs": [(x.name, f"/brief/{x.id}") for x in bv.briefs],
        "tiles": bvw.tiles(bv),
        "items": bvw.items(bv),
        "since_line": bvw.since_line(bv),
        "early": bvw.early(bv),
        "sources": bvw.sources_line(s, bv) if view == "full" else [],
        "stale_banner": bvw.stale_banner(s, bv),
        "detectors": len(sv_shipped()),
        "date_label": (f"{fmt.date_short(bv.issue.date)} {bv.issue.date.year}" if bv.issue else ""),
        "updated": again_view.updated_line(s, bv.issue, bv.run),
        # M15.3: "sent 06:00 to 3", Recipients · N, the watch's "since", what Ask today settles
        "sent": bvw.sent_to(s, bv.issue.id) if bv.issue is not None else (None, 0),
        "recipients": bvw.recipient_count(s, bv.brief.id),
        "watch_since": bvw.watch_since(s, bv.body),
    }


@router.get("/brief", response_class=HTMLResponse)
def brief_home(
    request: Request, created: str | None = None, o: Owner = Depends(signed_in)
) -> Response:
    """Your briefs (4a the first time, 4b after), suggested briefs, and a new one's toast (4c2)."""
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        writing = _first_writing(s, svc, o)
        if writing is not None:  # M11c: the first brief is still being written
            return page(request, "brief/first_run.html", {**shell_ctx(o, "brief"), "view": writing})
        data = bvw.listing(s, svc.settings)
        if not data["cards"]:
            return _empty(request, o)
        made = None
        if created and _uuid(created):
            from othor.briefs import service

            made = service.get(s, _uuid(created))  # type: ignore[arg-type]
        return _list_page(
            request, s, o, created=({"id": made.id, "name": made.name} if made else None)
        )


def _month_tabs(months: list[dt.date], picked: dt.date | None, brief: str | None) -> dict[str, Any]:
    """The archive's months: the latest by its full name ("November 2026"), then "Oct", "Sep"."""
    tail = f"&brief={brief}" if brief else ""
    tabs = [
        (
            m.strftime("%B %Y") if i == 0 else m.strftime("%b"),
            f"/brief/archive?month={m:%Y-%m}{tail}",
        )
        for i, m in enumerate(months)
    ]
    tabs.append(("All", f"/brief/archive?month=all{tail}"))
    on = (
        "All"
        if picked is None
        else next((t for (t, _), m in zip(tabs, months, strict=False) if m == picked), "All")
    )
    return {"month_tabs": tabs if months else [], "month_on": on}


@router.get("/brief/archive", response_class=HTMLResponse)
def archive(
    request: Request,
    brief: str | None = None,
    month: str | None = None,
    failed: int | None = None,
    o: Owner = Depends(signed_in),
) -> Response:
    """Every past issue by date (4e); a failed night's "Why it failed" run panel (4e1)."""
    svc = services(request)
    today = _today(request, o)
    picked = _date(month + "-01") if month and month != "all" else None
    if month and month != "all" and picked is None:
        return not_found(request, o.site, *GONE)
    with svc.sf() as s, tenant_scope(o.company.id):
        all_months = bvw.months(s)
        if month is None and all_months:
            picked = all_months[0]
        bid = _uuid(brief) if brief else None
        data = bvw.archive(s, bid, picked, today)
        panel = bvw.failed_run(s, failed) if failed else None
        if failed and panel is None:
            return not_found(request, o.site, *GONE)
        ctx = {
            **shell_ctx(o, "brief"),
            **data,
            "months": all_months,
            "month": picked,
            "picked_brief": bid,
            "failed_panel": panel,
            "brief_options": [("", "All briefs")] + [(str(b.id), b.name) for b in data["briefs"]],
            **_month_tabs(all_months, picked, brief),
            "query": "&".join(
                x
                for x in (
                    f"brief={brief}" if brief else "",
                    f"month={month}" if month else "",
                )
                if x
            ),
        }
        s.expunge_all()
    return page(request, "brief/archive.html", ctx)


@router.post("/brief/archive/runs/{number}/rerun", dependencies=CSRF)
def rerun(request: Request, number: int, o: Owner = Depends(signed_in)) -> Response:
    """Re-run now (8c, 4e1; M11a): resume the same failed run from the step that stopped, as
    the first run's Run again does. Same run date, spec and inputs, so it replays the same.
    Only the latest run: a later run is chained after this one in the ledger."""
    from othor.core.events import record_event
    from othor.engine.models import Run
    from othor.jobs.models import Job
    from othor.jobs.queue import enqueue

    svc = services(request)
    back = f"/brief/archive?failed={number}"
    with svc.sf() as s, tenant_scope(o.company.id):
        run = s.scalars(sa.select(Run).where(Run.number == number)).first()
        latest = s.scalar(sa.select(sa.func.max(Run.number)))
        if run is None or run.status != "failed" or run.kind != "nightly" or number != latest:
            return not_found(request, o.site, f"There’s no failed run #{number}", GONE[1])
        job = s.scalars(sa.select(Job).where(Job.id == run.job_id)).first() if run.job_id else None
        if job is not None and job.status in ("queued", "running"):
            return RedirectResponse(back, 303)  # already re-running
        if job is not None:
            job.status, job.attempts, job.run_after, job.lease_until = "queued", 0, None, None
        else:
            job = enqueue(
                s,
                "run",
                {"kind": "nightly", "run_date": run.run_date.isoformat()},
                dedupe_key=f"rerun:{run.id}",
            )
            run.job_id = job.id
        record_event(
            s,
            actor=o.viewer.id,
            action="run.rerun_requested",
            object_type="run",
            object_id=run.id,
            before={"status": "failed"},
            after={"job": str(job.id), "status": "queued"},
        )
        s.commit()
    return RedirectResponse(back, 303)


# --- Edit brief (4d2–4d6) -----------------------------------------------------------------------


def _gateway(request: Request) -> Any:
    from othor.ai.gateway import ModelGateway

    svc = services(request)
    return ModelGateway(svc.sf, provider_for(svc.settings), clock=svc.clock)


def provider_for(settings: Any) -> Any:
    from othor.ai.gateway import provider_for as real

    return real(settings)


def _brief(s: Session, bid: str) -> Any:
    from othor.briefs import service

    key = _uuid(bid)
    return service.get(s, key) if key else None


def _edit_page(request: Request, s: Session, o: Owner, brief: Any, ev: bvw.EditView) -> Response:
    bv = bvw.load(s, services(request).settings, brief.id, None, _today(request, o))
    if bv is None:
        return not_found(request, o.site, *GONE)
    ctx = _ctx(request, s, o, bv, "simple")
    ctx["edit"] = ev
    ctx["owner_options"] = [("top_item", "The owner of the top item")] + [
        (x["name"], f"{x['name']} · {x['role']}" if x.get("role") else x["name"]) for x in ev.owners
    ]
    s.expunge_all()
    return page(request, "brief/brief.html", ctx)


@router.get("/brief/{bid}/edit", response_class=HTMLResponse)
def edit_brief(
    request: Request, bid: str, tab: str = "question", q: str = "", o: Owner = Depends(signed_in)
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        brief = _brief(s, bid)
        ev = (
            bvw.edit_view(s, svc.settings, brief, None, tab, q=q, today=_today(request, o))
            if brief
            else None
        )
        if brief is None or ev is None:
            return not_found(request, o.site, *GONE)
        return _edit_page(request, s, o, brief, ev)


@router.post("/brief/{bid}/edit", response_class=HTMLResponse, dependencies=CSRF)
async def edit_post(request: Request, bid: str, o: Owner = Depends(signed_in)) -> Response:
    """Every change goes through the draft (checked against the spec each time) and shows its
    effect today; only Save writes a version (with an event), applied from the next run."""
    import json

    from othor.briefs import drafts, edit, service

    form = await request.form()
    tab = str(form.get("tab") or "question")
    action = str(form.get("action") or "preview")
    try:
        draft = json.loads(str(form.get("draft") or "{}"))
    except ValueError:
        draft = {}
    svc = services(request)
    today = _today(request, o)
    with svc.sf() as s, tenant_scope(o.company.id):
        brief = _brief(s, bid)
        if brief is None:
            return not_found(request, o.site, *GONE)
        parts = bvw.run_parts(s, svc.settings)
        if parts is None:
            return not_found(request, o.site, *GONE)
        _, spec, files = parts
        from othor.web import run_cache

        options = run_cache.scope_options(spec, files.series)
        fields: dict[str, Any] = {k: form.get(k) for k in form}
        fields["goals"] = form.getlist("goals")
        fields["kept"] = {b: form.getlist(f"keep:{b}") for b in options}
        fields["options"] = options
        if action.startswith("remove:"):
            fields["remove"] = action.split(":", 1)[1]
        elif action.startswith("add:"):
            fields["add"] = action.split(":", 1)[1]
        elif action.startswith("up:"):
            fields["up"] = action.split(":", 1)[1]
        draft = edit.apply_form(tab if tab != "add" else "numbers", fields, draft)
        reply = ""
        if action == "ai" and str(form.get("line") or "").strip():
            draft, reply = drafts.change_line(
                _gateway(request),
                o.company.id,
                str(form["line"]),
                edit.clean(draft, spec, options),
                spec,
                options,
            )
        if action == "save":
            body = edit.clean(draft, spec, options)
            current = service.current(s, brief)
            if current is None or body != edit.clean(dict(current.body), spec, options):
                v = service.save_version(s, brief, body, actor=o.viewer.email, reason="Edit brief")
                s.commit()
                return RedirectResponse(f"/brief/{brief.id}?saved={v.number}", status_code=303)
            return RedirectResponse(f"/brief/{brief.id}", status_code=303)
        next_tab = str(form.get("goto") or tab)
        ev = bvw.edit_view(
            s,
            svc.settings,
            brief,
            draft,
            next_tab,
            reply=reply,
            q=str(form.get("q") or ""),
            today=today,
        )
        if ev is None:
            return not_found(request, o.site, *GONE)
        return _edit_page(request, s, o, brief, ev)


@router.post("/brief/{bid}/recipients", dependencies=CSRF)
async def add_recipient(request: Request, bid: str, o: Owner = Depends(signed_in)) -> Response:
    """Add a recipient (4d6): an event; they get the next email and a read-only link."""
    import re

    from othor.briefs.models import Recipient
    from othor.core.events import record_event

    form = await request.form()
    email = str(form.get("r_email") or "").strip().lower()
    name = str(form.get("r_name") or "").strip()[:120]
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        brief = _brief(s, bid)
        if brief is None:
            return not_found(request, o.site, *GONE)
        if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            got = s.scalars(
                sa.select(Recipient).where(Recipient.brief_id == brief.id, Recipient.email == email)
            ).first()
            if got is None:
                s.add(Recipient(brief_id=brief.id, email=email, name=name, added_by=o.viewer.email))
            else:  # added back after an unsubscribe, a bounce or a removal (4d6)
                got.status, got.unsubscribed_at, got.bounced_at = "active", None, None
            record_event(
                s,
                actor=o.viewer.email,
                action="brief.recipient_added",
                object_type="brief",
                object_id=brief.id,
                after={"email": email},
            )
            s.commit()
    return RedirectResponse(f"/brief/{bid}/edit?tab=delivery", status_code=303)


@router.post("/brief/{bid}/recipients/{rid}/remove", dependencies=CSRF)
def remove_recipient(
    request: Request, bid: str, rid: str, o: Owner = Depends(signed_in)
) -> Response:
    from othor.briefs.models import Recipient
    from othor.core.events import record_event

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        brief = _brief(s, bid)
        key = _uuid(rid)
        r = (
            s.scalars(
                sa.select(Recipient).where(Recipient.id == key, Recipient.brief_id == brief.id)
            ).first()
            if brief and key
            else None
        )
        if r is None:
            return not_found(request, o.site, *GONE)
        r.status = "removed"
        from othor.mail.links import revoke_for

        revoke_for(s, r.id, svc.clock())  # their signed links stop at once
        record_event(
            s,
            actor=o.viewer.email,
            action="brief.recipient_removed",
            object_type="brief",
            object_id=brief.id,
            before={"email": r.email},
        )
        s.commit()
    return RedirectResponse(f"/brief/{bid}/edit?tab=delivery", status_code=303)


# --- New brief (4c, 4c1, 4c2) and suggested briefs (4b) ----------------------------------------


def _list_page(request: Request, s: Session, o: Owner, **extra: Any) -> Response:
    svc = services(request)
    data = bvw.listing(s, svc.settings)
    ctx = {
        **shell_ctx(o, "brief"),
        **data,
        "suggested": bvw.suggestions(s),
        "today": local_date(o.company, svc.clock()),
        **extra,
    }
    s.expunge_all()
    return page(request, "brief/list.html", ctx)


def _ask_step(request: Request, s: Session, o: Owner, **extra: Any) -> Response:
    """New brief, step 1: the question box and up to 3 suggested questions (v0.11)."""
    svc = services(request)
    cards = bvw.ask_cards(s, svc.settings, bvw.suggestions(s))
    return _list_page(request, s, o, new={"step": "ask", "cards": cards, **extra})


# New brief, model down (v0.11): one amber line, not a paragraph
RULES_LINE = (
    "Drafted by rules: Othor AI isn’t available right now, so these are your headline numbers "
    "with your question."
)
RULES_CHANGE = (
    "Drafted by rules: Othor AI isn’t available right now. Create the brief and change it in "
    "Edit brief."
)


@router.get("/brief/new", response_class=HTMLResponse)
def new_brief(request: Request, o: Owner = Depends(signed_in)) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        return _ask_step(request, s, o)


@router.post("/brief/new", response_class=HTMLResponse, dependencies=CSRF)
async def new_draft(request: Request, o: Owner = Depends(signed_in)) -> Response:
    """The question → a draft (Othor AI, or the default brief's numbers when it is off) →
    tomorrow's brief computed from last night's data. Nothing is made until Create."""
    import json

    from othor.briefs import drafts
    from othor.briefs.assemble import default_version
    from othor.web import run_cache

    form = await request.form()
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        parts = bvw.run_parts(s, svc.settings)
        if parts is None:
            return _ask_step(request, s, o, error="The first run hasn’t finished yet.")
        _, spec, files = parts
        options = run_cache.scope_options(spec, files.series)
        # a suggestion card fills the box; an older page may still post its question beside the
        # (empty) text box: the last one given
        asked = [str(x).strip() for x in form.getlist("question") if str(x).strip()]
        question = asked[-1][:300] if asked else ""
        line = str(form.get("line") or "").strip()
        suggestion = str(form.get("suggestion") or "")
        reply, rules = "", form.get("rules") == "1"
        if form.get("draft"):
            try:
                draft = json.loads(str(form["draft"]))
            except ValueError:
                draft = {}
            # the name is edited in place in the panel's header
            name = str(form.get("name") or "").strip()[:120]
            if name:
                draft["name"] = name
            if line:
                draft, reply = drafts.change_line(
                    _gateway(request), o.company.id, line, draft, spec, options
                )
                if reply == drafts.UNAVAILABLE:
                    reply, rules = RULES_CHANGE, True
        elif question:
            fallback = default_version(spec)
            sug_name = ""
            if suggestion:
                from othor.briefs.models import SuggestedBrief

                sug = s.scalars(
                    sa.select(SuggestedBrief).where(SuggestedBrief.id == _uuid(suggestion))
                ).first()
                if sug is not None and sug.question.strip().lower() == question.lower():
                    sug_name = sug.name
                    if sug.draft.get("headline"):
                        fallback = {**fallback, "headline": sug.draft["headline"]}
            from othor.briefs import service

            draft, how = drafts.draft_brief(
                _gateway(request),
                o.company.id,
                question,
                spec,
                options,
                fallback,
                taken=[b.name for b in service.active(s)],
                name=sug_name,
            )
            rules = how != "drafted"
            reply = RULES_LINE if rules else ""
        else:
            return _ask_step(request, s, o, error="Ask one question.")
        tomorrow = _today(request, o) + dt.timedelta(days=1)
        pv = bvw.new_preview(s, svc.settings, draft, tomorrow)
        return _list_page(
            request,
            s,
            o,
            new={
                "step": "draft",
                "pv": pv,
                "reply": reply,
                "rules": rules,
                "suggestion": suggestion,
            },
        )


@router.post("/brief/new/create", dependencies=CSRF)
async def create_brief(request: Request, o: Owner = Depends(signed_in)) -> Response:
    import json

    from othor.briefs import edit, service
    from othor.briefs.models import SuggestedBrief
    from othor.core.events import record_event
    from othor.web import run_cache

    form = await request.form()
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        parts = bvw.run_parts(s, svc.settings)
        if parts is None:
            return not_found(request, o.site, *GONE)
        _, spec, files = parts
        try:
            draft = json.loads(str(form.get("draft") or "{}"))
        except ValueError:
            draft = {}
        if str(form.get("name") or "").strip():  # edited in place in the panel's header
            draft["name"] = str(form["name"]).strip()[:120]
        body = edit.clean(draft, spec, run_cache.scope_options(spec, files.series))
        if not body["question"] or not body["headline"]:
            return RedirectResponse("/brief/new", status_code=303)
        b = service.create(s, body, actor=o.viewer.email, reason="New brief")
        sug = _uuid(str(form.get("suggestion") or ""))
        if sug:
            row = s.scalars(sa.select(SuggestedBrief).where(SuggestedBrief.id == sug)).first()
            if row is not None:
                row.status = "created"
                record_event(
                    s,
                    actor=o.viewer.email,
                    action="suggested_brief.created",
                    object_type="suggested_brief",
                    object_id=row.id,
                    after={"brief": str(b.id)},
                )
        s.commit()
        return RedirectResponse(f"/brief?created={b.id}", status_code=303)


@router.post("/brief/{bid}/undo-create", dependencies=CSRF)
def undo_create(request: Request, bid: str, o: Owner = Depends(signed_in)) -> Response:
    from othor.core.events import record_event

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        b = _brief(s, bid)
        if b is None or b.is_default:
            return not_found(request, o.site, *GONE)
        b.status = "archived"
        record_event(
            s,
            actor=o.viewer.email,
            action="brief.removed",
            object_type="brief",
            object_id=b.id,
            before={"status": "active"},
            after={"status": "archived", "undo_of": "brief.created"},
        )
        s.commit()
    return RedirectResponse("/brief", status_code=303)


@router.post("/brief/suggested/{sid}/dismiss", dependencies=CSRF)
def dismiss_suggestion(request: Request, sid: str, o: Owner = Depends(signed_in)) -> Response:
    from othor.briefs.models import SuggestedBrief
    from othor.core.events import record_event

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        key = _uuid(sid)
        row = (
            s.scalars(sa.select(SuggestedBrief).where(SuggestedBrief.id == key)).first()
            if key
            else None
        )
        if row is None:
            return not_found(request, o.site, *GONE)
        row.status = "dismissed"
        record_event(
            s,
            actor=o.viewer.email,
            action="suggested_brief.dismissed",
            object_type="suggested_brief",
            object_id=row.id,
            after={"question": row.question},
        )
        s.commit()
    return RedirectResponse("/brief", status_code=303)


# --- the read-only brief (10d): rendered here; M9 serves it from a signed link --------------


def _shared(
    request: Request, bid: str, date: str | None, fp: str | None, tab: str, o: Owner
) -> Response:
    svc = services(request)
    key = _uuid(bid)
    if key is None:
        return not_found(request, o.site, *GONE)
    today = _today(request, o)
    with svc.sf() as s, tenant_scope(o.company.id):
        bv = bvw.load(s, svc.settings, key, _date(date), today)
        if bv is None or bv.issue is None:
            return not_found(request, o.site, *GONE)
        ctx = _ctx(request, s, o, bv, "simple", readonly=True)
        ctx["base"] = f"/brief/{bv.brief.id}/shared"
        ctx["pdf"] = pdf_ctx(bv)
        ctx["shared_by"] = o.viewer.name or o.viewer.email
        ctx["viewer_line"] = "Preview · as recipients see it"
        # the owner's way back to the brief (#23); a recipient's own link has none
        day = f"?date={bv.issue.date.isoformat()}" if date else ""
        ctx["back"] = f"/brief/{bv.brief.id}{day}"
        if fp is not None:
            item = next((i for i in bv.body.get("items") or [] if i["fingerprint"] == fp), None)
            rv = sv.load(s, svc.settings, bv.run, today) if bv.run else None
            f = sv.finding(rv, fp) if rv else None
            if item is None or rv is None or f is None:
                return not_found(request, o.site, *GONE)
            where = f"{bv.brief.name} brief · {fmt.date_short(bv.issue.date)} · read-only"
            ctx["panel"] = sv.panel(rv, s, f, tab, where=where)
            ctx["run"] = bv.run.number if bv.run else None
        s.expunge_all()
    return page(request, "brief/shared.html", ctx)


@router.get("/brief/{bid}/shared", response_class=HTMLResponse)
def shared(
    request: Request, bid: str, date: str | None = None, o: Owner = Depends(signed_in)
) -> Response:
    """What a recipient sees (10d): the issue with no actions, details read-only. The owner
    previews it here; recipients get it from their signed link (`/l/<token>`, routes/links.py)."""
    return _shared(request, bid, date, None, "finding", o)


@router.get("/brief/{bid}/shared/finding/{fp}", response_class=HTMLResponse)
def shared_finding(
    request: Request,
    bid: str,
    fp: str,
    date: str | None = None,
    tab: str = "finding",
    o: Owner = Depends(signed_in),
) -> Response:
    return _shared(request, bid, date, fp, tab, o)


# --- one brief as a PDF (M15.7): the stored issue, downloaded; an event each time ---


def pdf_ctx(bv: bvw.BriefView) -> dict[str, Any] | None:
    """The Download PDF dialog's form: where it goes and which issue (the page's own)."""
    if bv.issue is None:
        return None
    hidden = {"date": bv.issue.date.isoformat()}
    if not bv.latest_of_day:  # the 06:00 issue after a Run again now
        hidden["issue"] = str(bv.issue.id)
    return {"action": f"/brief/{bv.brief.id}/pdf", "hidden": hidden}


def pdf_response(
    s: Session, svc: Any, company: Any, bv: bvw.BriefView, charts: bool, appendix: bool
) -> Response:
    from othor.web import brief_pdf

    data = brief_pdf.build(s, svc.settings, company, bv, charts=charts, appendix=appendix)
    assert bv.issue is not None
    name = brief_pdf.file_name(company, bv.brief.name, bv.issue.date)
    return Response(
        data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/brief/{bid}/pdf")
def brief_pdf_download(
    request: Request,
    bid: str,
    date: str | None = None,
    issue: str | None = None,
    charts: int = 0,
    appendix: int = 0,
    o: Owner = Depends(signed_in),
) -> Response:
    from othor.core.events import record_event

    svc = services(request)
    key = _uuid(bid)
    if key is None or (date and _date(date) is None):
        return not_found(request, o.site, *GONE)
    with svc.sf() as s, tenant_scope(o.company.id):
        iid = _uuid(issue) if issue else None
        bv = bvw.load(s, svc.settings, key, _date(date), _today(request, o), issue_id=iid)
        if bv is None or bv.issue is None:
            return not_found(request, o.site, *GONE)
        resp = pdf_response(s, svc, o.company, bv, bool(charts), bool(appendix))
        record_event(
            s,
            actor=o.viewer.id,
            action="brief.pdf",
            object_type="brief_issue",
            object_id=bv.issue.id,
            after={"charts": bool(charts), "appendix": bool(appendix)},
        )
        s.commit()
    return resp


# --- the brief page and its panel: last, so /brief/new and /brief/archive match first ---


@router.get("/brief/{bid}", response_class=HTMLResponse)
def brief(
    request: Request,
    bid: str,
    date: str | None = None,
    view: str = "simple",
    saved: int | None = None,
    issue: str | None = None,
    panel: str | None = None,
    metric: str | None = None,
    week: str | None = None,
    o: Owner = Depends(signed_in),
) -> Response:
    from othor.web import again_view, weather_view
    from othor.web import ontology_view as ov

    svc = services(request)
    key = _uuid(bid)
    if key is None:
        return not_found(request, o.site, *GONE)
    if date and _date(date) is None:
        return not_found(request, o.site, f"There’s no brief for {_day_label(date)}", TODAY)
    with svc.sf() as s, tenant_scope(o.company.id):
        iid = _uuid(issue) if issue else None
        bv = bvw.load(s, svc.settings, key, _date(date), _today(request, o), issue_id=iid)
        if bv is None:
            if date:
                return not_found(request, o.site, f"There’s no brief for {_day_label(date)}", TODAY)
            return not_found(request, o.site, *GONE)
        ctx = _ctx(request, s, o, bv, view)
        ctx["pdf"] = pdf_ctx(bv)
        ctx["saved"] = saved
        ctx["held"] = bvw.held(s, _today(request, o))  # Snooze and Mute from a card (M15.3)
        # #38: a headline tile opens its number's panel (the Ontology's), in place
        keep = [
            (k, v) for k, v in request.query_params.multi_items() if k not in ("panel", "metric")
        ]
        here = request.url.path + ("?" + urlencode(keep) if keep else "")
        ctx["tile_href"] = here + ("&" if keep else "?") + "panel=metric&metric="
        if panel == "metric" and metric:
            mp = ov.metric_panel(s, svc.settings, o.company, metric, _today(request, o))
            if mp is None:
                return not_found(request, o.site, f"There’s no number called {metric}", GONE[1])
            ctx["metric"], ctx["metric_close"] = mp, here
        # business weather (v0.12 part 1c): the brief's own forecast panel, in place
        if panel == "forecast" and bv.issue is not None:
            ctx["fpanel"] = weather_view.forecast_panel(
                s, svc.settings, bv.run, (week or "")[:8], brief=bv.brief.id
            )
        # Run again now (v0.11): the button, its progress in place, and the send offer
        ctx["again"] = again_view.load(
            s, svc.settings, o.company, svc.clock(), request.url.path, dict(request.query_params)
        )
        ctx["send"] = (
            again_view.send_offer(s, o.company, bv.issue, bv.latest_of_day)
            if not date and not issue
            else None
        )
        s.expunge_all()
    return page(request, "brief/brief.html", ctx)


@router.get("/brief/{bid}/finding/{fp}", response_class=HTMLResponse)
def finding(
    request: Request,
    bid: str,
    fp: str,
    date: str | None = None,
    tab: str = "finding",
    issue: str | None = None,
    o: Owner = Depends(signed_in),
) -> Response:
    svc = services(request)
    key = _uuid(bid)
    if key is None:
        return not_found(request, o.site, *GONE)
    today = _today(request, o)
    with svc.sf() as s, tenant_scope(o.company.id):
        iid = _uuid(issue) if issue else None
        bv = bvw.load(s, svc.settings, key, _date(date), today, issue_id=iid)
        if bv is None or bv.run is None:
            return not_found(request, o.site, *GONE)
        rv = sv.load(s, svc.settings, bv.run, today)
        f = sv.finding(rv, fp) if rv else None
        item = next((i for i in bv.body.get("items") or [] if i["fingerprint"] == fp), None)
        if rv is None or f is None or item is None:
            return not_found(request, o.site, *GONE)
        assert bv.issue is not None
        when = f"{fmt.date_short(bv.issue.date)}"
        days = int(f.get("days_open") or 1)
        where = f"{bv.brief.name} brief · {when} · In the brief · day {days}"
        ctx = _ctx(request, s, o, bv, "simple")
        ctx["panel"] = sv.panel(rv, s, f, tab, where=where)
        ctx["run"] = bv.run.number
        s.expunge_all()
    return page(request, "brief/brief.html", ctx)


# --- Add to a brief (10o), from Sweep and the finding panel ------------------------------------


@router.get("/sweep/finding/{fp}/brief", response_class=HTMLResponse)
def add_to_brief(
    request: Request, fp: str, run: int | None = None, o: Owner = Depends(signed_in)
) -> Response:
    from othor.web.routes import sweep as sweep_routes

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        rv = sweep_routes._view(request, s, o, run)
        f = sv.finding(rv, fp) if rv else None
        if rv is None or f is None or f["status"] != "open":
            return not_found(request, o.site, *GONE)
        ctx = sweep_routes._page_ctx(request, s, o, rv, "findings")
        ctx["add_brief"] = {
            "fp": fp,
            "title": (f.get("text") or {}).get("title", ""),
            "money": (f.get("text") or {}).get("money", ""),
            "options": bvw.add_options(s, svc.settings, f),
        }
        s.expunge_all()
    return page(request, "sweep/sweep.html", ctx)


@router.post("/sweep/finding/{fp}/brief", dependencies=CSRF)
async def add_to_brief_post(
    request: Request, fp: str, run: int | None = None, o: Owner = Depends(signed_in)
) -> Response:
    """Add as an item: a new version of the brief with the finding pinned (an event), from
    tomorrow's run. Watch it: the Watch panel, its breach shows in the brief's Watching line."""
    from othor.briefs import service
    from othor.engine.findings import pattern_of
    from othor.web.routes import sweep as sweep_routes

    form = await request.form()
    q = f"?run={run}" if run else ""
    if str(form.get("mode") or "item") == "watch":
        return RedirectResponse(f"/sweep/watch/{fp}{q}", status_code=303)
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        rv = sweep_routes._view(request, s, o, run)
        f = sv.finding(rv, fp) if rv else None
        brief = _brief(s, str(form.get("brief") or ""))
        if rv is None or f is None or brief is None:
            return not_found(request, o.site, *GONE)
        v = service.current(s, brief)
        if v is None:
            return not_found(request, o.site, *GONE)
        body = dict(v.body)
        pat = pattern_of(f)
        if pat not in (body.get("pinned") or []):
            body["pinned"] = [*(body.get("pinned") or []), pat]
            service.save_version(s, brief, body, actor=o.viewer.email, reason="Add to a brief")
            s.commit()
    sep = "&" if q else "?"
    if request.query_params.get("back") == "panel":  # from the dialog: the panel stays (M15.3)
        return RedirectResponse(f"/sweep/finding/{fp}{q}{sep}added={brief.id}", status_code=303)
    return RedirectResponse(f"/sweep{q}{sep}added={brief.id}", status_code=303)
