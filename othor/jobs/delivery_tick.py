# SPDX-License-Identifier: Apache-2.0
"""The morning side of the scheduler (docs/02 "scheduler · inside worker"), called once a minute
beside `othor.engine.scheduler.tick` (the 02:00 runs). For each company that isn't paused:

- each active brief, at its time (06:00 local by default) on its days (every day, or weekdays):
  one `deliver` job per brief and local day (`deliver:<brief>:<date>`), only with email on;
- 8d: setup left at Metrics with drafts unconfirmed for over 24 hours, once
  (`alert:ontology_waiting`);
- 8e: the weekly digest on Sundays at the brief time, when the owner turned it on (`digest:<date>`).

It lives in othor.jobs, not the engine: 8d reads setup drafts, which the engine never imports.
Jobs are deduped per company, so a second tick, a second worker or a restart never queues one
twice; a worker that was down at 06:00 catches up later the same day.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.briefs import service
from othor.core.localtime import local_now
from othor.core.models import Company
from othor.core.tenancy import system_scope, tenant_scope
from othor.engine.models import Run
from othor.jobs.models import Job
from othor.jobs.queue import enqueue
from othor.ontology.models import OntologyVersion

log = logging.getLogger("othor.scheduler")
WAITING = dt.timedelta(hours=24)


def _reached(local: dt.datetime, hhmm: str) -> bool:
    hh, mm = (int(x) for x in (hhmm or "06:00").split(":"))
    return (local.hour, local.minute) >= (hh, mm)


def scheduled(body: dict[str, Any], day: dt.date) -> bool:
    """Whether a brief's email goes out on `day` (Daily, Weekdays, Weekly, Monthly; M15.7c)."""
    from othor.briefs.schedule import sends_on

    return sends_on(body, day)


def _queue(s: Session, kind: str, payload: dict[str, str], key: str) -> bool:
    if s.scalars(sa.select(Job.id).where(Job.dedupe_key == key)).first():
        return False
    enqueue(s, kind, payload, dedupe_key=key)
    try:
        s.commit()
    except sa.exc.IntegrityError:  # another worker queued it first
        s.rollback()
        return False
    return True


def _waiting_since(s: Session) -> dt.datetime | None:
    from othor.core.models import SetupState
    from othor.setup.models import SetupDraft

    state = s.scalars(sa.select(SetupState)).first()
    if state is None or state.current_step != "metrics":
        return None
    return s.scalar(
        sa.select(sa.func.min(SetupDraft.created_at)).where(
            SetupDraft.kind == "metric", SetupDraft.state == "needs_confirmation"
        )
    )


def tick(sf: sessionmaker[Session], now: dt.datetime) -> list[tuple[uuid.UUID, str]]:
    queued: list[tuple[uuid.UUID, str]] = []
    with sf() as s, system_scope("scheduler: which companies deliver this morning"):
        companies = s.scalars(
            sa.select(Company).where(Company.deleted_at.is_(None), Company.paused.is_(False))
        ).all()
        confirmed = set(s.scalars(sa.select(OntologyVersion.company_id).distinct()).all())
        s.expunge_all()
    for c in companies:
        local = local_now(c, now)
        today = local.date()
        with sf() as s, tenant_scope(c.id):
            if c.id not in confirmed:
                since = _waiting_since(s)
                if since is not None and now - since > WAITING:
                    key = "alert:ontology_waiting"
                    if _queue(s, "alert", {"kind": "ontology_waiting"}, key):
                        queued.append((c.id, key))
                continue
            if c.email_delivery:
                for b in service.active(s):
                    v = service.current(s, b)
                    body = v.body if v else {}
                    if not scheduled(body, today):
                        continue
                    if not _reached(local, str(body.get("time") or c.brief_time)):
                        continue
                    key = f"deliver:{b.id}:{today.isoformat()}"
                    payload = {"brief_id": str(b.id), "date": today.isoformat()}
                    if _queue(s, "deliver", payload, key):
                        queued.append((c.id, key))
            # the nightly replay (owner, 16 Nov 2026): once the morning's briefs are due,
            # last night's run, replayed for Home's "Checked" light
            if _reached(local, c.brief_time):
                night = s.scalars(
                    sa.select(Run.id).where(
                        Run.kind == "nightly",
                        Run.run_date == today,
                        Run.status.in_(("done", "partial")),
                    )
                ).first()
                if night is not None:
                    key = f"replay:{night}"
                    if _queue(s, "replay", {"run_id": str(night)}, key):
                        queued.append((c.id, key))
            if c.weekly_digest and today.weekday() == 6 and _reached(local, c.brief_time):
                key = f"digest:{today.isoformat()}"
                if _queue(s, "alert", {"kind": "digest", "date": today.isoformat()}, key):
                    queued.append((c.id, key))
    for cid, key in queued:
        log.info("queued %s", key, extra={"company": str(cid)})
    return queued
