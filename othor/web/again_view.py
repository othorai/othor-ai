# SPDX-License-Identifier: Apache-2.0
"""Run again now on the pages (v0.11): the button, the run's progress in place, the reason when a
guard stops it, and on a remade brief the "Updated 15:40 · run #0034" line and the offer to send
it. Every word here comes from the run, job, delivery and recipient rows; nothing is written.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core import fmt
from othor.core.config import Settings
from othor.core.localtime import local_now
from othor.core.models import Company, Event
from othor.engine import again
from othor.engine.ledger import last_sealed
from othor.engine.models import Run
from othor.jobs.models import Job
from othor.web import run_view

REFUSALS = {"running", "window", "limit", "not_ready"}


@dataclass
class Again:
    shown: bool  # the button: once the first run has sealed
    busy: bool  # a run is queued or running (any kind): the button waits
    run: run_view.FirstRun | None  # Run again's progress, while it runs or when it stopped
    polling: bool
    reason: str | None  # why the last press didn't start a run (the guard's words)
    poll_url: str
    next_url: str


def load(
    s: Session,
    settings: Settings,
    company: Company,
    now: dt.datetime,
    path: str,
    query: dict[str, str],
) -> Again:
    keep = {k: v for k, v in query.items() if k != "again"}
    here = path + (f"?{urlencode(keep)}" if keep else "")
    code = query.get("again")
    reason = None
    if code in REFUSALS:
        why = again.refusal(s, company, now)
        # the guard's words as they stand now (a run that finished meanwhile says nothing)
        reason = why.text if why is not None and why.code == code else None
    job = again.latest_job(s, company, now)
    view = None
    polling = False
    if job is not None:
        run = s.scalars(sa.select(Run).where(Run.job_id == job.id)).first()
        if job.status in ("queued", "running"):
            view = (
                run_view.progress(s, settings, company, run, job, now)
                if run is not None
                else run_view.starting(job)
            )
            polling = True
        elif run is not None and run.status == "failed":
            latest = s.scalar(sa.select(sa.func.max(Run.number)))
            if run.number == latest:  # a later run replaced it: nothing to say
                view = run_view.progress(s, settings, company, run, job, now)
    return Again(
        shown=last_sealed(s) is not None,
        busy=again.active_job(s, now) is not None,
        run=view,
        polling=polling,
        reason=reason,
        poll_url=here,
        next_url=here,
    )


def safe_next(raw: str | None) -> str:
    """Where the button goes back to: a path on this site only."""
    if not raw or not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return "/"
    return raw


def with_reason(url: str, code: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}again={code}"


# --- the remade brief ---------------------------------------------------------------------------


def updated_line(s: Session, issue: Any, run: Run | None) -> str | None:
    """'Updated 15:40 · run #0034' on an issue Run again now made; None on the 06:00 one."""
    if issue is None or run is None or issue.status != "rerun":
        return None
    company = s.scalars(sa.select(Company).where(Company.id == run.company_id)).one()
    at = local_now(company, run.ended_at or run.started_at)
    return f"Updated {fmt.time_hm(at.time())} · run #{run.number:04d}"


@dataclass
class SendOffer:
    state: str  # offer · sending · sent
    recipients: int
    issue_id: uuid.UUID
    sent: int = 0


def send_key(issue_id: uuid.UUID) -> str:
    return f"deliver:update:{issue_id}"


def send_offer(s: Session, company: Company, issue: Any, latest: bool) -> SendOffer | None:
    """After Run again now: "Send the updated brief to N recipients?" Not re-sent by default;
    offered on the day's newest issue while email is on and the brief has recipients."""
    from othor.briefs.models import Recipient
    from othor.mail.models import Delivery

    if issue is None or issue.status != "rerun" or not latest or not company.email_delivery:
        return None
    n = int(
        s.scalar(
            sa.select(sa.func.count(Recipient.id)).where(
                Recipient.brief_id == issue.brief_id, Recipient.status == "active"
            )
        )
        or 0
    )
    job = s.scalars(sa.select(Job).where(Job.dedupe_key == send_key(issue.id))).first()
    if job is not None:
        if job.status in ("queued", "running"):
            return SendOffer("sending", n, issue.id)
        sent = int(
            s.scalar(
                sa.select(sa.func.count(Delivery.id)).where(
                    Delivery.brief_issue_id == issue.id, Delivery.status == "sent"
                )
            )
            or 0
        )
        return SendOffer("sent", n, issue.id, sent=sent)
    kept = s.scalars(
        sa.select(Event.id).where(
            Event.action == "brief.update_kept", Event.object_id == str(issue.id)
        )
    ).first()
    if kept is not None or n == 0:
        return None
    return SendOffer("offer", n, issue.id)
