# SPDX-License-Identifier: Apache-2.0
"""The scheduler (docs/02): once a minute the worker calls `tick(now)`. For each company that
isn't paused and has a confirmed Ontology, it queues one nightly run when the company's local
time reaches its run time (02:00). One job per company and local day (`job.dedupe_key`), so a
second tick, a second worker or a restart never queues it twice; a worker that was down at
02:00 catches up later the same day. Earlier missed days are not run (the ledger shows the gap).

Jobs are queued through `othor.jobs.queue` only, so the engine never imports the job handlers
(one of them uses the model). The 06:00 delivery comes with M9.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.core.localtime import local_now
from othor.core.models import Company
from othor.core.tenancy import system_scope, tenant_scope
from othor.jobs.models import Job
from othor.jobs.queue import enqueue
from othor.ontology.models import OntologyVersion

log = logging.getLogger("othor.scheduler")


def nightly_key(local_date: dt.date) -> str:
    return f"nightly:{local_date.isoformat()}"


def due(company: Company, now: dt.datetime) -> dt.date | None:
    """The local run date if the company's run time has passed today, else None."""
    local = local_now(company, now)
    hh, mm = (int(x) for x in (company.run_time or "02:00").split(":"))
    if (local.hour, local.minute) < (hh, mm):
        return None
    return local.date()


def tick(sf: sessionmaker[Session], now: dt.datetime) -> list[tuple[uuid.UUID, dt.date]]:
    queued: list[tuple[uuid.UUID, dt.date]] = []
    with sf() as s, system_scope("scheduler: which companies run tonight"):
        companies = s.scalars(
            sa.select(Company).where(Company.deleted_at.is_(None), Company.paused.is_(False))
        ).all()
        confirmed = set(s.scalars(sa.select(OntologyVersion.company_id).distinct()).all())
        s.expunge_all()
    for c in companies:
        if c.id not in confirmed:
            continue
        run_date = due(c, now)
        if run_date is None:
            continue
        key = nightly_key(run_date)
        with sf() as s, tenant_scope(c.id):
            if s.scalars(sa.select(Job.id).where(Job.dedupe_key == key)).first():
                continue
            enqueue(
                s,
                "run",
                {"kind": "nightly", "run_date": run_date.isoformat()},
                dedupe_key=key,
            )
            try:
                s.commit()
            except sa.exc.IntegrityError:  # another worker queued it first
                s.rollback()
                continue
        log.info("queued nightly run for %s (%s)", c.slug, run_date)
        queued.append((c.id, run_date))
    return queued


def catch_up(sf: sessionmaker[Session], now: dt.datetime) -> list[tuple[uuid.UUID, dt.date]]:
    """On worker start (M11b §6): a company that has been running nightly and has no run for
    yesterday (its local date) missed a night while the worker was down. Queue that night once
    (the same dedupe key the tick uses), so the brief still comes without a manual run. A
    company that has never run a night isn't caught up: its first run is the dry run. Nights
    before yesterday stay gaps in the ledger, as before."""
    queued: list[tuple[uuid.UUID, dt.date]] = []
    with sf() as s, system_scope("scheduler: missed nights on start"):
        companies = s.scalars(
            sa.select(Company).where(Company.deleted_at.is_(None), Company.paused.is_(False))
        ).all()
        confirmed = set(s.scalars(sa.select(OntologyVersion.company_id).distinct()).all())
        s.expunge_all()
    for c in companies:
        if c.id not in confirmed:
            continue
        yesterday = local_now(c, now).date() - dt.timedelta(days=1)
        key = nightly_key(yesterday)
        with sf() as s, tenant_scope(c.id):
            keys = set(
                s.scalars(sa.select(Job.dedupe_key).where(Job.dedupe_key.like("nightly:%"))).all()
            )
            if not keys or key in keys or max(k for k in keys if k) > key:
                continue
            enqueue(
                s,
                "run",
                {"kind": "nightly", "run_date": yesterday.isoformat()},
                dedupe_key=key,
            )
            try:
                s.commit()
            except sa.exc.IntegrityError:
                s.rollback()
                continue
        log.info("queued missed nightly run for %s (%s)", c.slug, yesterday)
        queued.append((c.id, yesterday))
    return queued
