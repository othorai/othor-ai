# SPDX-License-Identifier: Apache-2.0
"""Run again now (v0.11, owner 11 Nov 2026): the nightly steps on demand, as a new run of kind
`rerun` with its own number in the ledger, replayable like any run. Each active brief gets a new
version of the day's issue (`brief_issue.status` `rerun`); the 06:00 one stays in the Archive.
No email goes out unless the owner sends the update.

Guards, checked when the page draws the button and again when the owner presses it:
- one run at a time per company (a `run` job queued or running);
- at most `DAILY` a day per company, counted by local day (each one reads the sources again);
- not from 01:00 to 06:30 company time, so it can't meet the 02:00 run or the 06:00 delivery;
- not before the first run has sealed (nothing to remake yet).
Every request is an event (`run.again_requested`, or `run.again_refused` with the reason).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core.events import record_event
from othor.core.localtime import local_now
from othor.core.models import Company
from othor.engine.ledger import last_sealed
from othor.jobs.models import MAX_ATTEMPTS, Job  # a job past MAX_ATTEMPTS never runs again
from othor.jobs.queue import enqueue

KIND = "rerun"
DAILY = 3
QUIET_FROM = dt.time(1, 0)
QUIET_UNTIL = dt.time(6, 30)
KEY = "again:"  # job.dedupe_key: again:<local date>:<n>


@dataclass(frozen=True)
class Refusal:
    code: str  # not_ready · running · window · limit
    text: str


def _hhmm(t: dt.time) -> str:
    """The refusal's times, 12-hour as every time the owner reads (16 Nov 2026)."""
    from othor.core import fmt

    return fmt.time_hm(t)


def day_key(day: dt.date) -> str:
    return f"{KEY}{day.isoformat()}:"


def used_today(s: Session, day: dt.date) -> int:
    """Run again requests the company made on its local `day` (queued, run or failed)."""
    return int(
        s.scalar(sa.select(sa.func.count(Job.id)).where(Job.dedupe_key.like(day_key(day) + "%")))
        or 0
    )


def active_job(s: Session, now: dt.datetime) -> Job | None:
    """A run job waiting or running (the nightly run, Read now, the first run or a Run again)."""
    return s.scalars(
        sa.select(Job)
        .where(
            Job.kind == "run",
            sa.or_(
                sa.and_(Job.status == "queued", Job.attempts < MAX_ATTEMPTS),
                # running: a live lease, or one a worker will take over
                sa.and_(
                    Job.status == "running",
                    sa.or_(Job.attempts < MAX_ATTEMPTS, Job.lease_until > now),
                ),
            ),
        )
        .order_by(Job.created_at)
        .limit(1)
    ).first()


def refusal(s: Session, company: Company, now: dt.datetime) -> Refusal | None:
    """Why Run again now can't start now, in the owner's words; None when it can."""
    if last_sealed(s) is None:
        return Refusal("not_ready", "Run again is ready once your first run has finished.")
    if active_job(s, now) is not None:
        return Refusal("running", "A run is running now. Run again once it has finished.")
    local = local_now(company, now)
    if QUIET_FROM <= local.time() < QUIET_UNTIL:
        return Refusal(
            "window",
            f"Run again opens at {_hhmm(QUIET_UNTIL)}. From {_hhmm(QUIET_FROM)} the nightly run "
            "and the morning brief have the sources to themselves.",
        )
    if used_today(s, local.date()) >= DAILY:
        return Refusal(
            "limit",
            f"You’ve run again {DAILY} times today, the most in a day. The next run is tonight "
            f"at {company.run_time or '02:00'}.",
        )
    return None


def request(
    s: Session, company: Company, actor: str | uuid.UUID, now: dt.datetime
) -> Job | Refusal:
    """The owner pressed Run again now: queue the run, or say why not. Either way an event.
    Inside the company's tenant scope and the caller's transaction."""
    day = local_now(company, now).date()
    why = refusal(s, company, now)
    if why is not None:
        record_event(
            s,
            actor=actor,
            action="run.again_refused",
            object_type="company",
            object_id=company.id,
            after={"reason": why.code, "date": day.isoformat()},
            at=now,
        )
        return why
    n = used_today(s, day) + 1
    job = enqueue(
        s, "run", {"kind": KIND, "run_date": day.isoformat()}, dedupe_key=f"{day_key(day)}{n}"
    )
    record_event(
        s,
        actor=actor,
        action="run.again_requested",
        object_type="job",
        object_id=job.id,
        after={"date": day.isoformat(), "n": n},
        at=now,
    )
    return job


def latest_job(s: Session, company: Company, now: dt.datetime) -> Job | None:
    """Today's latest Run again request, if any."""
    day = local_now(company, now).date()
    return s.scalars(
        sa.select(Job)
        .where(Job.dedupe_key.like(day_key(day) + "%"))
        .order_by(Job.created_at.desc())
        .limit(1)
    ).first()
