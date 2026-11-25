# SPDX-License-Identifier: Apache-2.0
"""Home (3a, 3aa, 3b, 10k; M10a): proposals with Accept, Decline, Undo and Chat about this;
questions answered one at a time in the 480 px chat; banners for a source not read and for
recipients who left. Every owner action writes an event; accepted proposals and answers write an
Ontology version that applies from the next run. Another company's id is a 404.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from othor.core.events import record_event
from othor.core.localtime import local_date
from othor.core.tenancy import tenant_scope
from othor.engine import proposals
from othor.engine.models import Proposal
from othor.home import answers
from othor.ontology.compiler import compile_version
from othor.ontology.models import Question
from othor.web import home_view as hv
from othor.web.context import services
from othor.web.routes.auth import not_found, page
from othor.web.routes.pages import EMPTY, Owner, shell_ctx, signed_in
from othor.web.security import check_csrf

router = APIRouter(include_in_schema=False)
# the 404 (9a): what isn’t there, and one way back
GONE = (
    "There’s nothing to review here",
    ("Home", "/home"),
)
CSRF = [Depends(check_csrf)]

# notes the chat may show, by key (never text from the URL)
NOTES = {
    "until": "Until when?",
    "dates": "Type the dates it runs, below.",
    "plant": "Type the plant's name below.",
    "plant_only": "Type the plant's name only.",
    "choose": "Choose one of the replies above.",
    "na": answers.NOT_AVAILABLE,
    "typed": "Type your answer below.",
    "cant": hv.CANT_DECIDE,
}
NOTE_KEY = {v: k for k, v in NOTES.items()}


def _today(request: Request, o: Owner) -> dt.date:
    return local_date(o.company, services(request).clock())


def _uuid(v: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(v)
    except ValueError:
        return None


def _big_picture(request: Request, s: Session, o: Owner) -> bool:
    from othor.ai.gateway import provider_for
    from othor.web import big_picture_view as bpv

    svc = services(request)
    run = hv.latest_run(s)
    if run is None:
        return False
    lang = o.company.report_language if o.company.report_language in ("en", "hi") else "en"
    if bpv.stored(s, run, lang) is not None:
        return True
    return bpv.available(svc.settings, s, svc.clock(), provider_for(svc.settings))


def _gateway(request: Request) -> Any:
    from othor.ai.gateway import ModelGateway, provider_for

    svc = services(request)
    return ModelGateway(
        svc.sf,
        provider_for(svc.settings),
        clock=svc.clock,
        monthly_tokens=svc.settings.ai_monthly_tokens,
    )


@router.get("/home", response_class=HTMLResponse)
def home(
    request: Request,
    panel: str | None = None,
    q: str | None = None,
    f: int = 0,
    note: str | None = None,
    p: str | None = None,
    done: str | None = None,
    text: str | None = None,
    week: str | None = None,
    size: str | None = None,
    o: Owner = Depends(signed_in),
) -> Response:
    svc = services(request)
    today = _today(request, o)
    with svc.sf() as s, tenant_scope(o.company.id):
        if hv.latest_run(s) is None:
            s.expunge_all()
            return _empty(request, o)
        ctx: dict[str, Any] = {
            **shell_ctx(o, "home"),
            **hv.page(s, svc.settings, o.company, today, svc.clock()),
        }
        from othor.web import again_view

        # Run again now (v0.11): the button by History, its progress in place
        ctx["again"] = again_view.load(
            s, svc.settings, o.company, svc.clock(), request.url.path, dict(request.query_params)
        )
        # Big Picture (v0.19, M19.5): the button, live while a model can write it or a report is
        # already stored for tonight's run
        ctx["big_picture"] = _big_picture(request, s, o)
        # business weather (v0.12 part 1b): the forecast panel for a week card or the Ahead
        # line, and "How this was picked", both in place
        if panel == "forecast":
            from othor.web import weather_view

            ctx["fpanel"] = weather_view.forecast_panel(
                s, svc.settings, ctx.get("weather_run"), (week or "")[:8]
            )
            # #109 (owner, 25 Nov 2026): the quick panel stays on Home and opens that week on
            # Forecasts, with the Forecasts page installed
            if ctx["fpanel"] is not None and weather_view.forecasts_on():
                ctx["fpanel"]["forecasts"] = weather_view.week_on_forecasts(ctx["fpanel"]["week"])
        if panel == "big-picture":  # v0.19 (M19.5): in the side panel, normal or extended
            from othor.ai.gateway import provider_for
            from othor.web import big_picture_view as bpv

            ctx["bpanel"] = bpv.panel(
                s,
                svc.settings,
                o.company,
                svc.clock(),
                provider_for(svc.settings),
                extended=size == "extended",
            )
        if panel == "watch":  # Watch this in place from Home's What to do card (0.33.1)
            from othor.web.routes.sweep import watch_panel

            ctx.update(watch_panel(request, s, o, "home") or {})
        if panel == "lights" and ctx.get("lights"):  # Home's lights (owner, 16 Nov 2026)
            ctx["lpanel"] = ctx["lights"]
        if panel == "weather":
            from othor.web import weather_view

            ctx["wpanel"] = weather_view.picked_panel(svc.settings, ctx.get("weather_run"))
        if panel == "proposal" and p:  # the old Chat about this: the one chat since 0.26
            row = _proposal(s, p)
            if row is None:
                return not_found(request, o.site, *GONE)
            return RedirectResponse(f"/home?panel=othor&subject=proposal:{row.id}", 303)
        if done and (row := _proposal(s, done)) is not None and row.status != "open":
            ctx["proposals"] = _with_decided(s, ctx["proposals"], row)
        ctx["size_lead"] = _size_lead(s, done)
        s.expunge_all()
    ctx["ask_in_title"] = True  # #86: Ask Othor AI sits by Big Picture, not in the top bar
    return page(request, "home/home.html", ctx)


def _size_lead(s: Session, done: str | None) -> dict[str, Any]:
    """#76: the top number suggested for the company's size, first on Suggested by Othor AI, and
    the calm toast with Undo when the owner just set one aside with Not now."""
    from othor.ontology import size_numbers

    out: dict[str, Any] = {"item": size_numbers.home_item(s)}
    row = _proposal(s, done) if done else None
    if row is not None and row.kind == size_numbers.KIND and row.status == "declined":
        out["toast"] = {
            "text": "Not now · Othor AI suggests it again when your numbers change",
            "action": f"/home/proposals/{row.id}/undo?back=home",
            "label": "Undo",
        }
    return out


def _with_decided(s: Session, rows: list[dict[str, Any]], p: Proposal) -> list[dict[str, Any]]:
    """The decided proposal back in its place in the list (same order as the open ones), as the
    row with its Undo (M12b: the note sits where the row was)."""
    order = s.scalars(
        sa.select(Proposal)
        .where(sa.or_(Proposal.status == "open", Proposal.id == p.id), Proposal.kind != "join")
        .order_by(Proposal.created_at, Proposal.id)
    ).all()
    ids = [str(x.id) for x in hv.review_order(order)]
    shown = {r["id"] for r in rows}
    at = len([i for i in ids[: ids.index(str(p.id))] if i in shown])
    declined = (
        "Not now · Othor AI suggests it again when your numbers change"
        if p.kind == "size_number"
        else "Declined · it won’t be proposed again"
    )
    decided = {
        **hv.proposal_row(p),
        "decided": "Accepted · applies from tonight’s run" if p.status == "accepted" else declined,
    }
    return [*rows[:at], decided, *rows[at:]]


def _empty(request: Request, o: Owner) -> Response:
    from othor.core.models import SetupState

    title, empty, text = EMPTY["home"]
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        st = s.scalars(sa.select(SetupState)).first()
        step = st.current_step if st else "company"
    resume = None if step == "done" else f"/setup/{step}"
    return page(
        request,
        "pages/area_empty.html",
        {
            **shell_ctx(o, "home"),
            "title": title,
            "empty": empty,
            "text": text,
            "resume": resume,
            "trial": hv.trial(o.company, svc.clock(), svc.settings),
        },
    )


def _proposal(s: Session, pid: str) -> Proposal | None:
    key = _uuid(pid)
    if key is None:
        return None
    return s.scalars(sa.select(Proposal).where(Proposal.id == key, Proposal.kind != "join")).first()


# --- proposals ---------------------------------------------------------------------------------


@router.post("/home/proposals/{pid}/{decision}", dependencies=CSRF)
def decide(request: Request, pid: str, decision: str, o: Owner = Depends(signed_in)) -> Response:
    if decision not in ("accept", "decline", "undo"):
        return not_found(request, o.site, *GONE)
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        p = _proposal(s, pid)
        if p is None:
            return not_found(request, o.site, *GONE)
        actor = str(o.viewer.id)
        if decision == "undo":
            twins = hv.decided_twins(s, p)  # decided with it, undone with it (newest first)
            proposals.undo(s, p, actor=actor, now=svc.clock())
            for twin in reversed(twins):
                proposals.undo(s, twin, actor=actor, now=svc.clock())
        else:
            for twin in hv.open_twins(s, p):  # the same goal twice: one decision for both
                proposals.decide(s, twin, accept=decision == "accept", actor=actor, now=svc.clock())
            proposals.decide(s, p, accept=decision == "accept", actor=actor, now=svc.clock())
        if decision in ("accept", "undo"):
            compile_version(s, o.company, actor=actor)
        s.commit()
    # External signals reviews its links in place too (owner, 20 Nov 2026): back to that page
    back = request.query_params.get("back")
    if back == "ontology":  # the Business Model page lists them since 23 Nov 2026
        url = "/ontology#proposals" if decision == "undo" else f"/ontology?decided={pid}#proposals"
        return RedirectResponse(url, 303)
    page_ = "/signals" if back == "signals" else "/home"
    return RedirectResponse(page_ if decision == "undo" else f"{page_}?done={pid}", 303)


# --- questions ---------------------------------------------------------------------------------


def _question(s: Session, qid: str) -> Question | None:
    key = _uuid(qid)
    if key is None:
        return None
    return s.scalars(sa.select(Question).where(Question.id == key)).first()


def _src(request: Request) -> str | None:
    """Which page the questions panel is on ("sweep-<run>", M15.2); checked in hv.panel_page."""
    return request.query_params.get("from")


def _back(q: Question, out: answers.Outcome, src: str | None = None) -> str:
    """The panel after an answer: the same question, answered (what it did, Undo), drafted
    (Apply, Edit) or asking for more."""
    url = hv.panel_url_id(q.id, src)
    if out.saved:
        return url
    if out.follow_up:
        url += "&f=1"
    if out.note and out.note in NOTE_KEY:
        url += f"&note={NOTE_KEY[out.note]}"
    return url


@router.post("/home/questions/{qid}/reply", dependencies=CSRF)
def reply(
    request: Request, qid: str, choice: str = Form(...), o: Owner = Depends(signed_in)
) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        q = _question(s, qid)
        if q is None:
            return not_found(request, o.site, *GONE)
        out = answers.reply(
            s, q, choice[:200], actor=str(o.viewer.id), now=svc.clock(), today=_today(request, o)
        )
        if out.saved:
            compile_version(s, o.company, actor=str(o.viewer.id))
        s.commit()
        url = _back(q, out, _src(request))
    return RedirectResponse(url, 303)


@router.post("/home/questions/{qid}/answer", dependencies=CSRF)
def answer(
    request: Request,
    qid: str,
    pick: str = Form(""),
    text: str = Form(""),
    o: Owner = Depends(signed_in),
) -> Response:
    """The panel's one form: a picked reply card is saved as a quick reply (no model); "Something
    else…" or a question with no replies sends the box as a typed answer (a draft)."""
    if pick.startswith(answers.PICK):
        return pick_suggestion(request, qid, pick.removeprefix(answers.PICK)[:300], o)
    if pick and pick != OTHER:
        return reply(request, qid, pick, o)
    if text.strip():
        return free_text(request, qid, text, o)
    url = hv.panel_url_id(qid, _src(request)) + "&note=choose"
    return RedirectResponse(url if pick != OTHER else url.replace("choose", "typed"), 303)


OTHER = "__other"


def pick_suggestion(request: Request, qid: str, key: str, o: Owner) -> Response:
    """A suggested card picked: its record as a draft (Apply writes it), no model."""
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        q = _question(s, qid)
        if q is None:
            return not_found(request, o.site, *GONE)
        out = answers.pick_suggestion(s, q, key, actor=str(o.viewer.id))
        s.commit()
        url = _back(q, out, _src(request))
    return RedirectResponse(url, 303)


@router.post("/home/questions/{qid}/decide", dependencies=CSRF)
def decide_question(request: Request, qid: str, o: Owner = Depends(signed_in)) -> Response:
    """Let Othor AI decide (v0.12): the click is the apply; when Othor AI can't decide, the
    panel says why and the next step."""
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        q = _question(s, qid)
        if q is None:
            return not_found(request, o.site, *GONE)
        done = answers.decide(s, q, actor=str(o.viewer.id), now=svc.clock())
        if done:
            compile_version(s, o.company, actor=str(o.viewer.id))
        s.commit()
    url = hv.panel_url_id(qid, _src(request))
    return RedirectResponse(url if done else url + "&note=cant", 303)


@router.post("/home/questions/{qid}/text", dependencies=CSRF)
def free_text(
    request: Request, qid: str, text: str = Form(""), o: Owner = Depends(signed_in)
) -> Response:
    svc = services(request)
    gw = _gateway(request)

    def draft(inputs: dict[str, Any]) -> dict[str, Any] | None:
        res = gw.complete(o.company.id, *answers.PROMPT, inputs)
        return res.output if res.ok and isinstance(res.output, dict) else None

    with svc.sf() as s, tenant_scope(o.company.id):
        q = _question(s, qid)
        if q is None:
            return not_found(request, o.site, *GONE)
        out = answers.free_text(
            s,
            q,
            text,
            actor=str(o.viewer.id),
            now=svc.clock(),
            today=_today(request, o),
            draft=draft,
        )
        if out.saved:
            compile_version(s, o.company, actor=str(o.viewer.id))
        s.commit()
        url = _back(q, out, _src(request))
    return RedirectResponse(url, 303)


@router.post("/home/questions/{qid}/apply", dependencies=CSRF)
def apply_answer(request: Request, qid: str, o: Owner = Depends(signed_in)) -> Response:
    """Apply a typed answer's draft: only now does the Ontology change (non-negotiable 6)."""
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        q = _question(s, qid)
        if q is None:
            return not_found(request, o.site, *GONE)
        if answers.apply_draft(s, q, actor=str(o.viewer.id), now=svc.clock()):
            compile_version(s, o.company, actor=str(o.viewer.id))
        s.commit()
    return RedirectResponse(hv.panel_url_id(qid, _src(request)), 303)


@router.post("/home/questions/{qid}/edit", dependencies=CSRF)
def edit_answer(request: Request, qid: str, o: Owner = Depends(signed_in)) -> Response:
    """Edit: the draft goes; the panel shows the question again with the typed text."""
    from urllib.parse import quote

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        q = _question(s, qid)
        if q is None:
            return not_found(request, o.site, *GONE)
        text = answers.discard_draft(s, q, actor=str(o.viewer.id))
        s.commit()
    url = hv.panel_url_id(qid, _src(request))
    return RedirectResponse(f"{url}&text={quote(text[:500])}", 303)


@router.post("/home/questions/{qid}/skip", dependencies=CSRF)
def skip(request: Request, qid: str, o: Owner = Depends(signed_in)) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        q = _question(s, qid)
        if q is None:
            return not_found(request, o.site, *GONE)
        today = _today(request, o)
        answers.skip(s, q, actor=str(o.viewer.id), today=today)
        s.flush()
        order = answers.todays_order(s, today, hv._tz(o.company))
        nxt = hv.next_open_after(order, q, today)
        s.commit()
    # Ask me later moves on; with none left the panel closes and Home says what's left
    src = _src(request)
    return RedirectResponse(hv.panel_url(nxt, src) if nxt is not None else hv.panel_page(src), 303)


@router.post("/home/questions/{qid}/undo", dependencies=CSRF)
def undo(
    request: Request, qid: str, change: str = Form(""), o: Owner = Depends(signed_in)
) -> Response:
    """Undo, and Change answer (the same, with a typed answer back in the box)."""
    from urllib.parse import quote

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        q = _question(s, qid)
        if q is None:
            return not_found(request, o.site, *GONE)
        said = q.answer_text or ""
        typed = said and said not in (q.quick_replies or []) and q.kind != "needs_confirmation"
        answers.undo(s, q, actor=str(o.viewer.id))
        compile_version(s, o.company, actor=str(o.viewer.id))
        s.commit()
    url = hv.panel_url_id(qid, _src(request))
    if change and typed and said not in answers.UNTIL:
        url += f"&text={quote(said[:500])}"
    return RedirectResponse(url, 303)


# --- notices -----------------------------------------------------------------------------------


@router.post("/home/notices/{eid}/dismiss", dependencies=CSRF)
def dismiss(request: Request, eid: str, o: Owner = Depends(signed_in)) -> Response:
    from othor.core.models import Event

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        key = _uuid(eid)
        e = s.scalars(sa.select(Event).where(Event.id == key)).first() if key else None
        if e is None or e.action not in ("recipient.unsubscribed", "recipient.bounced"):
            return not_found(request, o.site, *GONE)
        record_event(
            s,
            actor=str(o.viewer.id),
            action="home.notice_dismissed",
            object_type="event",
            object_id=str(e.id),
        )
        s.commit()
    return RedirectResponse("/home", 303)
