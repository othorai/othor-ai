# SPDX-License-Identifier: Apache-2.0
"""The 06:00 delivery of one brief for one local day (docs/02, docs/09).

payload: {"brief_id": "<uuid>", "date": "YYYY-MM-DD"} (the run date: the issue the run made
that night). Queued by `othor.jobs.delivery_tick` at the brief's time.

payload: {"brief_id": "<uuid>", "issue_id": "<uuid>"}: Run again now's updated issue, queued only
when the owner presses Send (v0.11); sent at once, never waited for.

- The night's run not sealed yet: back in 5 minutes (not a failed attempt). At the brief's time
  plus 4 hours (10:00 by default) it stops waiting and the owner gets 8c "didn't finish".
- The run failed: nothing is sent (8c went to the owner when it failed).
- A send that failed (the relay was down) fails the job, so the runner retries; recipients
  already sent to are skipped.
"""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import timedelta

import sqlalchemy as sa

from othor.briefs import service
from othor.briefs.models import Brief
from othor.core.events import SYSTEM_ACTOR, record_event
from othor.core.localtime import local_now
from othor.core.models import Company
from othor.jobs.models import Job
from othor.jobs.queue import enqueue
from othor.jobs.runner import Reschedule, WorkerContext
from othor.mail import delivery
from othor.mail.relay import MailSendError

WAIT = timedelta(minutes=5)
CUTOFF = timedelta(hours=4)


def brief_time(company: Company, body: dict[str, object] | None) -> str:
    return str((body or {}).get("time") or company.brief_time or "06:00")


def run(ctx: WorkerContext, job: Job) -> None:
    if job.payload.get("issue_id"):
        return send_update(ctx, job)
    now = ctx.clock()
    brief_id = uuid.UUID(job.payload["brief_id"])
    day = dt.date.fromisoformat(job.payload["date"])
    with ctx.sf() as s:
        company = s.scalars(sa.select(Company).where(Company.id == job.company_id)).one()
        out = delivery.deliver_brief(
            s, ctx.settings, ctx.key, ctx.mail(), company, brief_id, day, now
        )
        if out.status == "wait":
            brief = s.scalars(sa.select(Brief).where(Brief.id == brief_id)).one()
            v = service.current(s, brief)
            hh, mm = (int(x) for x in brief_time(company, v.body if v else None).split(":"))
            local = local_now(company, now)
            due = dt.datetime.combine(day, dt.time(hh, mm), tzinfo=local.tzinfo)
            if local < due + CUTOFF:
                raise Reschedule(now + WAIT)
            enqueue(
                s,
                "alert",
                {"kind": "run_late", "date": day.isoformat()},
                dedupe_key=f"alert:run_late:{day.isoformat()}",
            )
        if out.status != "sent":
            record_event(
                s,
                actor=SYSTEM_ACTOR,
                action="brief.not_delivered",
                object_type="brief",
                object_id=brief_id,
                after={"date": day, "reason": "run_late" if out.status == "wait" else out.status},
                at=now,
            )
        s.commit()
    if out.failed:
        raise MailSendError(f"{out.failed} sends failed")


def send_update(ctx: WorkerContext, job: Job) -> None:
    """The owner's "Send the updated brief to N recipients" (v0.11)."""
    now = ctx.clock()
    issue_id = uuid.UUID(job.payload["issue_id"])
    with ctx.sf() as s:
        company = s.scalars(sa.select(Company).where(Company.id == job.company_id)).one()
        out = delivery.deliver_update(s, ctx.settings, ctx.key, ctx.mail(), company, issue_id, now)
        if out.status != "sent":
            record_event(
                s,
                actor=SYSTEM_ACTOR,
                action="brief.not_delivered",
                object_type="brief",
                object_id=job.payload["brief_id"],
                after={"issue": str(issue_id), "reason": out.status},
                at=now,
            )
        s.commit()
    if out.failed:
        raise MailSendError(f"{out.failed} sends failed")
