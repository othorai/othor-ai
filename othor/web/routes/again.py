# SPDX-License-Identifier: Apache-2.0
"""Run again now (v0.11, owner 11 Nov 2026): the button on the brief's top bar, on Home and in
Sources after metrics are added; and the offer to send the remade brief.

- `POST /runs/again`: queue a run of kind `rerun` (othor.engine.again) or, when a guard stops
  it, come back to the page with the reason (`?again=<code>`). Either way an event.
- `POST /brief/<id>/issues/<issue>/send`: the updated issue to its recipients, through the same
  delivery as the 06:00 email (a `deliver` job). Nothing is sent unless the owner presses it.
- `POST /brief/<id>/issues/<issue>/keep`: "Not now" (an event; the offer goes away).
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from othor.core.events import record_event
from othor.core.tenancy import tenant_scope
from othor.engine import again
from othor.web import again_view
from othor.web.context import services
from othor.web.routes.auth import not_found
from othor.web.routes.pages import Owner, signed_in
from othor.web.security import check_csrf

router = APIRouter(include_in_schema=False)
CSRF = [Depends(check_csrf)]
GONE = ("There’s no such brief", ("Morning Brief", "/brief"))


@router.post("/runs/again", dependencies=CSRF)
def run_again(
    request: Request, back_to: str = Form("/", alias="next"), o: Owner = Depends(signed_in)
) -> Response:
    from othor.briefs import service

    svc = services(request)
    back = again_view.safe_next(back_to)
    with svc.sf() as s, tenant_scope(o.company.id):
        if back == "/brief":  # from Sources: watch it on the first brief
            briefs = service.active(s)
            back = f"/brief/{briefs[0].id}" if briefs else "/brief"
        out = again.request(s, o.company, o.viewer.id, svc.clock())
        s.commit()
    if isinstance(out, again.Refusal):
        return RedirectResponse(again_view.with_reason(back, out.code), 303)
    return RedirectResponse(back, 303)


def _issue(s: sa.orm.Session, bid: str, iid: str):  # type: ignore[no-untyped-def]
    from othor.briefs.models import BriefIssue

    try:
        b, i = uuid.UUID(bid), uuid.UUID(iid)
    except ValueError:
        return None
    return s.scalars(
        sa.select(BriefIssue).where(BriefIssue.id == i, BriefIssue.brief_id == b)
    ).one_or_none()


@router.post("/brief/{bid}/issues/{iid}/send", dependencies=CSRF)
def send_update(request: Request, bid: str, iid: str, o: Owner = Depends(signed_in)) -> Response:
    from othor.jobs.models import Job
    from othor.jobs.queue import enqueue

    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        issue = _issue(s, bid, iid)
        offer = again_view.send_offer(s, o.company, issue, latest=True) if issue else None
        if issue is None or offer is None:
            return not_found(request, o.site, *GONE)
        key = again_view.send_key(issue.id)
        if (
            offer.state == "offer"
            and not s.scalars(sa.select(Job.id).where(Job.dedupe_key == key)).first()
        ):
            job = enqueue(
                s,
                "deliver",
                {"brief_id": str(issue.brief_id), "issue_id": str(issue.id)},
                dedupe_key=key,
            )
            record_event(
                s,
                actor=o.viewer.id,
                action="brief.update_send_requested",
                object_type="brief_issue",
                object_id=issue.id,
                after={"job": str(job.id), "recipients": offer.recipients},
                at=svc.clock(),
            )
            s.commit()
    return RedirectResponse(f"/brief/{bid}", 303)


@router.post("/brief/{bid}/issues/{iid}/keep", dependencies=CSRF)
def keep_update(request: Request, bid: str, iid: str, o: Owner = Depends(signed_in)) -> Response:
    svc = services(request)
    with svc.sf() as s, tenant_scope(o.company.id):
        issue = _issue(s, bid, iid)
        if issue is None or issue.status != "rerun":
            return not_found(request, o.site, *GONE)
        record_event(
            s,
            actor=o.viewer.id,
            action="brief.update_kept",
            object_type="brief_issue",
            object_id=issue.id,
            after={"sent": False},
            at=svc.clock(),
        )
        s.commit()
    return RedirectResponse(f"/brief/{bid}", 303)
