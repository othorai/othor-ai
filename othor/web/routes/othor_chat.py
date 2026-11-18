# SPDX-License-Identifier: Apache-2.0
"""The Othor AI chat's actions (Chat 1.0): a reply, Apply, and a button's choice. Each posts in
place and lands back on the page the chat was opened on, the panel still open (`back`)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from othor.core.localtime import local_date
from othor.core.tenancy import tenant_scope
from othor.web.context import local_path, services
from othor.web.routes.pages import Owner, signed_in
from othor.web.security import check_csrf

router = APIRouter()
CSRF = [Depends(check_csrf)]


def provider_for(settings: Any) -> Any:
    from othor.ai.gateway import provider_for as real

    return real(settings)


def _back(back: str, subject: str) -> str:
    """Only a path on this site, with the chat open (the one redirect check, `local_path`)."""
    return local_path(back, f"/home?panel=othor&subject={subject}")


@router.post("/othor/reply", dependencies=CSRF)
def reply(
    request: Request,
    subject: str = Form("ask"),
    text: str = Form(""),
    line: str = Form(""),  # the Business Model page's Tell Othor AI line
    back: str = Form(""),
    o: Owner = Depends(signed_in),
) -> Response:
    from othor.ai.gateway import ModelGateway
    from othor.chat import service as chat

    text = text or line
    svc = services(request)
    gw = ModelGateway(
        svc.sf,
        provider_for(svc.settings),
        clock=svc.clock,
        monthly_tokens=svc.settings.ai_monthly_tokens,
    )
    from othor.chat import changes, proposals

    with svc.sf() as s, tenant_scope(o.company.id):
        if proposals.is_subject(subject):  # Home's proposals (M26.4)
            if proposals.subject_ok(s, subject):
                t = chat.thread(s, subject)
                proposals.reply(s, gw, o.company.id, t, text, actor=str(o.viewer.id))
        elif changes.is_subject(subject):  # Business Model changes (M26.4)
            if not changes.subject_ok(s, subject):
                return RedirectResponse(_back(back, "ontology"), 303)
            t = chat.thread(s, subject)
            changes.draft(
                s,
                gw,
                o.company,
                t,
                text,
                actor=str(o.viewer.id),
                today=local_date(o.company, svc.clock()),
            )
        elif subject == "ask":  # Ask v0.1 (M26.6): read-only tools through the one gate
            from othor import tools
            from othor.chat import ask

            t = chat.thread(s, subject)
            tctx = tools.Ctx(s, svc.settings, o.company, local_date(o.company, svc.clock()))
            ask.answer(s, gw, tctx, t, text, actor=str(o.viewer.id))
        else:
            t = chat.thread(s, subject)
            chat.reply(s, gw, o.company.id, t, text, actor=str(o.viewer.id))
        s.commit()
    return RedirectResponse(_back(back, subject), 303)


@router.post("/othor/apply", dependencies=CSRF)
def apply(
    request: Request,
    subject: str = Form("questions"),
    back: str = Form(""),
    decision: str = Form(""),
    o: Owner = Depends(signed_in),
) -> Response:
    from othor.chat import changes, proposals
    from othor.chat import service as chat
    from othor.ontology.compiler import compile_version

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        t = chat.thread(s, subject)
        if proposals.is_subject(t.subject):
            proposals.apply(
                s,
                o.company,
                t,
                actor=str(o.viewer.id),
                now=svc.clock(),
                decline=decision == "decline",
            )
            s.commit()
            return RedirectResponse(_back(back, subject), 303)
        if changes.is_subject(t.subject):
            changes.apply(s, o.company, t, actor=str(o.viewer.id))
            s.commit()
            return RedirectResponse(_back(back, subject), 303)
        done = chat.apply(
            s, t, actor=str(o.viewer.id), now=svc.clock(), today=local_date(o.company, svc.clock())
        )
        if done is not None and (done.proposal or {}).get("version"):
            compile_version(s, o.company, actor=str(o.viewer.id))
        s.commit()
    return RedirectResponse(_back(back, subject), 303)


@router.post("/othor/choose", dependencies=CSRF)
def choose(
    request: Request,
    subject: str = Form("questions"),
    item: str = Form(""),
    option: str = Form(""),
    back: str = Form(""),
    o: Owner = Depends(signed_in),
) -> Response:
    from othor.chat import service as chat

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        t = chat.thread(s, subject)
        chat.choose(s, t, item, option, actor=str(o.viewer.id))
        s.commit()
    return RedirectResponse(_back(back, subject), 303)


@router.get("/othor/preview", response_class=HTMLResponse)
def preview(
    request: Request,
    item: str = "",
    option: str = "",
    subject: str = "",
    o: Owner = Depends(signed_in),
) -> Response:
    """A choice's real rows (M26.3), read live by code from the choice's own names: the item
    must be a question still open and the option one of its own cards. Never stored, never sent
    to a model (rule 3). A Business Model change's item (`c:<n>`, M26.4) shows today's values
    before and after instead, computed the same way."""
    if item.startswith("c:"):
        return _change_values(request, o, subject, item)
    from othor.chat import questions
    from othor.engine.context import EngineContext
    from othor.home import answers
    from othor.ontology import preview as pv
    from othor.ontology import service
    from othor.ontology.compiler.catalog import load_catalog
    from othor.web.templating import templates

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        q = questions._question(s, item)
        card = answers._card(q, option) if q is not None and q.status != "answered" else None
        ask = pv.for_change(dict(card.get("change") or {})) if card else None
        if ask is None:
            return HTMLResponse('<div class="oc-preview"></div>')
        cat = load_catalog(s)
        records = {(r.type, r.key): dict(r.body) for r in service.active(s)}
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=svc.clock)
    with tenant_scope(o.company.id):
        got = pv.read(ctx, o.company.id, records, cat, ask)
    return templates.TemplateResponse(
        request, "components/othor_preview.html", {"pv": got}, headers={"Cache-Control": "no-store"}
    )


def _change_values(request: Request, o: Owner, subject: str, item: str) -> Response:
    """Today's value before and after a drafted change, for up to three numbers it changes:
    through the read-only role, bounded, logged as a "preview" query (6a3); shown, never
    stored as a value."""
    from othor.chat import changes
    from othor.chat import service as chat
    from othor.home import changes as hc
    from othor.web.templating import templates

    svc = services(request)
    empty = HTMLResponse('<div class="oc-preview"></div>')
    with svc.sf() as s, tenant_scope(o.company.id):
        if not changes.is_subject(subject) or not changes.subject_ok(s, subject):
            return empty
        m = chat.last_set(s, chat.thread(s, subject))
        it = next((i for i in (m.proposal["items"] if m else []) if i.get("id") == item), None)
        if it is None or not it.get("change") or not it.get("high_impact"):
            return empty
        runner = hc.preview_runner(
            s, svc.key, o.company.id, svc.settings.analytics_path(o.company.id)
        )
        eff = hc.effect(
            s, o.company, it["change"], preview=runner, today=local_date(o.company, svc.clock())
        )
        s.commit()  # the preview's query log
    return templates.TemplateResponse(
        request,
        "components/othor_values.html",
        {"lines": hc.value_lines(eff), "note": eff.note or ""},
        headers={"Cache-Control": "no-store"},
    )
