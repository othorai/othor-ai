# SPDX-License-Identifier: Apache-2.0
"""The Ontology page (6a, 6a1), the metric panel (6a2, 10n), Change with Othor AI (6a3) and
History with Compare and Restore (6a4) — M10b.

Every change writes one Ontology version (refused as "No change" when it changes neither
records nor citations) and its event, and applies from the next run. Another company's metric or
version is a 404.
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
from othor.home import changes
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
    "There’s no such record in the Ontology",
    ("Ontology", "/ontology"),
)


def provider_for(settings: Any) -> Any:
    from othor.ai.gateway import provider_for as real

    return real(settings)


CSRF = [Depends(check_csrf)]
NO_CHANGE = "No change: that is already in your Ontology."


def _today(request: Request, o: Owner) -> dt.date:
    return local_date(o.company, services(request).clock())


def _empty(request: Request, o: Owner) -> Response:
    title, empty, text = EMPTY["ontology"]
    return page(
        request,
        "pages/area_empty.html",
        {**shell_ctx(o, "ontology"), "title": title, "empty": empty, "text": text, "resume": None},
    )


def _model(request: Request, s: Session) -> bool:
    from othor.ai.gateway import model_status

    svc = services(request)
    return model_status(svc.settings, s, svc.clock())[0]


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
    values: tuple[str, list[str]] | None = None,
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
        if panel == "chat":
            ctx["chat"] = _chat(s, subject, _model(request, s), done=done, prefill=text)
            d = ctx["chat"]["draft"]
            if values and d and d["id"] == values[0]:
                d["card"] = {**d["card"], "lines": [*d["card"]["lines"], *values[1]]}
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


# --- 6a3 · Change with Othor AI --------------------------------------------------------------


def _thread(s: Session, subject: str, create: bool = False) -> Any:
    from othor.setup.models import ChatThread

    t = s.scalars(
        sa.select(ChatThread).where(
            ChatThread.purpose == "ontology", ChatThread.subject == subject[:200]
        )
    ).first()
    if t is None and create:
        t = ChatThread(purpose="ontology", subject=subject[:200], status="open")
        s.add(t)
        s.flush()
    return t


QUESTION = {
    "add": "Which number should Othor AI compute?",
    "page": "What changed in the business?",
}


def _chat(
    s: Session, subject: str, model: bool, *, done: int | None = None, prefill: str | None = None
) -> dict[str, Any]:
    """Change with Othor AI as a form (v0.11, docs/plans/questions-panel.md part 4): the request
    box, then the last request's draft card (Apply · Edit) or its reply, or the applied card
    (Undo) right after Apply. Earlier exchanges stay in the thread, not on screen."""
    from othor.setup.models import ChatMessage

    t = _thread(s, subject)
    msgs = (
        list(
            s.scalars(
                sa.select(ChatMessage)
                .where(ChatMessage.thread_id == t.id)
                .order_by(ChatMessage.created_at)
            )
        )
        if t is not None
        else []
    )
    said = next((m.text for m in reversed(msgs) if m.role == "user"), "")
    last = next((m for m in reversed(msgs) if m.role in ("ai", "system")), None)
    draft = reply = None
    p = (last.proposal or {}) if last is not None else {}
    card = p.get("card")
    if last is not None and not p.get("discarded"):
        if card and not card.get("error") and (not last.applied or p.get("version") == done):
            draft = {
                "id": str(last.id),
                "card": card,
                "applied": last.applied,
                "version": p.get("version"),
                "said": said,
            }
        elif not last.applied:
            reply = card["error"] if card and card.get("error") else last.text
    n = service.current_number(s)
    title, sub = "Change with Othor AI", f"Ontology · v{n}"
    if subject.startswith("metric:"):
        key = subject.split(":", 1)[1]
        rec = next((r for r in service.active(s, ["metric"]) if r.key == key), None)
        name = str((rec.body.get("name") if rec else None) or key.replace("_", " ").capitalize())
        sub = f"{name} · {sub}"
    if subject == "add":
        title = "Add a number"
    return {
        "subject": subject,
        "title": title,
        "sub": sub,
        "metric": subject.startswith("metric:"),
        "question": QUESTION.get(subject, "What should change about this number?"),
        "draft": draft,
        # a reply with no change (not understood, not available): the request back in the box
        "reply": reply,
        "prefill": (prefill if prefill is not None else (said if reply else ""))[:500],
        "model": model,
        "next": n + 1,
    }


def _subject_ok(s: Session, subject: str) -> bool:
    if subject in ("page", "add"):
        return True
    if subject.startswith("metric:"):
        key = subject.split(":", 1)[1]
        return any(r.key == key for r in service.active(s, ["metric"]))
    return False


@router.post("/ontology/chat", dependencies=CSRF)
def chat_send(
    request: Request,
    line: str = Form(""),
    subject: str = Form("page"),
    o: Owner = Depends(signed_in),
) -> Response:
    from othor.ai.gateway import ModelGateway
    from othor.setup.models import ChatMessage

    svc = services(request)
    line = line.strip()[:500]
    today = _today(request, o)
    back = f"/ontology?panel=chat&subject={subject}"
    shown: tuple[str, list[str]] | None = None
    with svc.sf() as s, tenant_scope(o.company.id):
        if not _subject_ok(s, subject):
            return not_found(request, o.site, *GONE)
        if not line:
            return RedirectResponse(back, 303)
        t = _thread(s, subject, create=True)
        s.add(ChatMessage(thread_id=t.id, role="user", text=line))
        s.commit()  # the gateway logs the call in its own session: no write held open here
        inputs = changes.model_inputs(s, line, subject, today)
        gw = ModelGateway(svc.sf, provider_for(svc.settings), clock=svc.clock)
        res = gw.complete(o.company.id, *changes.PROMPT, inputs)
        if not res.ok:
            s.add(ChatMessage(thread_id=t.id, role="system", text=changes.NOT_AVAILABLE))
        else:
            d = changes.check(s, res.output, inputs, line, today)
            card = None
            if d.change is not None:
                preview = changes.preview_runner(
                    s, svc.key, o.company.id, svc.settings.analytics_path(o.company.id)
                )
                eff = changes.effect(s, o.company, d.change, preview=preview, today=today)
                section, before, after = changes.card_line(s, d.change)
                fresh = changes.value_lines(eff)  # shown now, never stored (6a3 preview)
                eff.values = []
                card = {
                    "change": d.change,
                    "section": section,
                    "before": before,
                    "after": after,
                    "effect": eff.as_json(),
                    "lines": changes.effect_lines(eff),
                    "error": eff.error,
                }
            text = d.reply or (
                "Here is the change." if card else "I couldn’t turn that into a change."
            )
            msg = ChatMessage(thread_id=t.id, role="ai", text=text, proposal={"card": card})
            s.add(msg)
            s.flush()
            if card is not None:
                shown = (str(msg.id), fresh)
        record_event(
            s,
            actor=str(o.viewer.id),
            action="ontology.chat",
            object_type="chat_thread",
            object_id=t.id,
            after={"guard": res.guard},
        )
        s.commit()
    if shown is None:
        return RedirectResponse(back, 303)
    # the preview's values are part of this response only (never stored as a value)
    return ontology(request, panel="chat", subject=subject, o=o, values=shown)


@router.post("/ontology/chat/{mid}/apply", dependencies=CSRF)
def chat_apply(request: Request, mid: str, o: Owner = Depends(signed_in)) -> Response:
    from othor.setup.models import ChatMessage, ChatThread

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        try:
            key = uuid.UUID(mid)
        except ValueError:
            return not_found(request, o.site, *GONE)
        m = s.scalars(sa.select(ChatMessage).where(ChatMessage.id == key)).first()
        card = ((m.proposal or {}).get("card") or {}) if m else {}
        if m is None or not card.get("change") or card.get("error"):
            return not_found(request, o.site, *GONE)
        t = s.scalars(sa.select(ChatThread).where(ChatThread.id == m.thread_id)).one()
        back = f"/ontology?panel=chat&subject={t.subject}"
        if m.applied or (m.proposal or {}).get("discarded"):
            return RedirectResponse(back, 303)  # applied once; or dropped by Edit
        ch = card["change"]
        c = (
            service.retire(ch["type"], ch["key"])
            if ch["op"] == "retire"
            else service.put(ch["type"], ch["key"], ch["body"], {"chat": str(t.id)})
        )
        v = service.apply(
            s,
            [c],
            actor=str(o.viewer.id),
            reason=f"Tell Othor AI: {card.get('after') or ch['key']}"[:300],
        )
        if v is None:
            s.rollback()
            return RedirectResponse(back + "&note=nochange", 303)
        m.applied = True
        m.proposal = {**(m.proposal or {}), "version": v.number}
        compile_version(s, o.company, actor=str(o.viewer.id))
        s.commit()
        n = v.number
    return RedirectResponse(f"/ontology?panel=chat&subject={t.subject}&done={n}", 303)


@router.post("/ontology/chat/{mid}/edit", dependencies=CSRF)
def chat_edit(request: Request, mid: str, o: Owner = Depends(signed_in)) -> Response:
    """Edit: the draft goes (nothing was written), the request comes back to the box."""
    from urllib.parse import quote

    from othor.setup.models import ChatMessage, ChatThread

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        try:
            key = uuid.UUID(mid)
        except ValueError:
            return not_found(request, o.site, *GONE)
        m = s.scalars(sa.select(ChatMessage).where(ChatMessage.id == key)).first()
        if m is None or m.role != "ai":
            return not_found(request, o.site, *GONE)
        t = s.scalars(sa.select(ChatThread).where(ChatThread.id == m.thread_id)).one()
        said = next(
            (
                x.text
                for x in s.scalars(
                    sa.select(ChatMessage)
                    .where(ChatMessage.thread_id == t.id, ChatMessage.role == "user")
                    .order_by(ChatMessage.created_at.desc())
                )
                if x.created_at <= m.created_at
            ),
            "",
        )
        if not m.applied:
            m.proposal = {**(m.proposal or {}), "discarded": True}
            record_event(
                s,
                actor=str(o.viewer.id),
                action="ontology.draft_discarded",
                object_type="chat_message",
                object_id=m.id,
            )
        s.commit()
        subject = t.subject
    return RedirectResponse(f"/ontology?panel=chat&subject={subject}&text={quote(said[:500])}", 303)


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
            return not_found(request, o.site, f"There’s no Ontology v{n}", GONE[1])
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
            return not_found(request, o.site, f"There’s no Ontology v{n}", GONE[1])
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
