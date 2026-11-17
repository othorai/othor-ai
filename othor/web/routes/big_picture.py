# SPDX-License-Identifier: Apache-2.0
"""Big Picture (v0.19, M19.5), owners only, in Home's side panel (`/home?panel=big-picture`).
The stored report for the latest sealed run in the company's language; else written in place by
one model call (`/make`, htmx); the model down: "Not available right now", the briefs untouched.
"Email it to me" sends the stored report again, no model call. Every action writes an event."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from othor.ai.gateway import ModelGateway, provider_for
from othor.core.localtime import local_date
from othor.core.tenancy import tenant_scope
from othor.web import big_picture_view as bpv
from othor.web import home_view as hv
from othor.web.context import services
from othor.web.routes.auth import not_found, page
from othor.web.routes.pages import Owner, signed_in
from othor.web.security import check_csrf

router = APIRouter(include_in_schema=False)
CSRF = [Depends(check_csrf)]
GONE = ("There’s no report here", ("Home", "/home"))


def _language(o: Owner) -> str:
    return o.company.report_language if o.company.report_language in ("en", "hi") else "en"


def _partial(request: Request, ctx: dict[str, Any]) -> Response:
    return page(request, "big_picture/_report.html", ctx)


@router.get("/big-picture")
def big_picture(request: Request, o: Owner = Depends(signed_in)) -> Response:
    """The report lives in Home's side panel (owner, 15 Nov 2026); this address (the email's
    link) opens it there."""
    return RedirectResponse("/home?panel=big-picture", 303)


@router.post("/big-picture/make", response_class=HTMLResponse, dependencies=CSRF)
def make(request: Request, size: str = Form(""), o: Owner = Depends(signed_in)) -> Response:
    svc = services(request)
    lang = _language(o)
    provider = provider_for(svc.settings)
    with svc.sf() as s, tenant_scope(o.company.id):
        run = hv.latest_run(s)
        if run is None:
            return _partial(request, {"report": None})
        row = bpv.stored(s, run, lang)
        if row is None:
            if not bpv.available(svc.settings, s, svc.clock(), provider):
                return _partial(request, {"report": None})
            facts = bpv.gather(s, svc.settings, run, local_date(o.company, svc.clock()))
            gateway = ModelGateway(
                svc.sf, provider, clock=svc.clock, monthly_tokens=svc.settings.ai_monthly_tokens
            )
            row, _guard = bpv.make(s, gateway, o.company, run, lang, facts, o.viewer.id)
        out = {
            "report": bpv.report(row, o.company.name) if row is not None else None,
            "extended": size == "extended",
        }
        s.expunge_all()
    return _partial(request, out)


@router.post("/big-picture/{rid}/email", response_class=HTMLResponse, dependencies=CSRF)
def email(request: Request, rid: str, o: Owner = Depends(signed_in)) -> Response:
    from othor.core.events import record_event
    from othor.mail.messages import big_picture_email

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        row = bpv.find(s, rid)
        if row is None:
            return not_found(request, o.site, *GONE)
        r = bpv.report(row, o.company.name)
        msg = big_picture_email(
            r,
            to=o.viewer.email,
            company_name=o.company.name,
            slug=o.company.slug,
            settings=svc.settings,
        )
        svc.relay.send(msg)
        record_event(
            s,
            actor=o.viewer.id,
            action="big_picture.emailed",
            object_type="big_picture",
            object_id=row.id,
            after={"to": "owner"},
        )
        s.commit()
        s.expunge_all()
    # the header's button becomes the note, in place
    return page(request, "big_picture/_sent.html", {"sent": f"Sent to {o.viewer.email}"})
