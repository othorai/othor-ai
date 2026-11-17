# SPDX-License-Identifier: Apache-2.0
"""Claim and run jobs (docs/02): one at a time per worker, leased, idempotent handlers.

`run_pending` runs everything queued, in-process; tests and `othor worker` both use it.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.core.config import Settings
from othor.core.crypto import InstanceKey
from othor.core.models import Company, utcnow
from othor.core.tenancy import system_scope, tenant_scope
from othor.jobs.models import MAX_ATTEMPTS, Job
from othor.jobs.queue import enqueue

log = logging.getLogger("othor.jobs")
LEASE = timedelta(minutes=10)


@dataclass
class WorkerContext:
    settings: Settings
    sf: sessionmaker[Session]
    key: InstanceKey
    clock: Callable[[], datetime] = utcnow
    gateway: Any = None  # othor.ai.gateway.ModelGateway, set by the worker (never by the engine)
    extra: dict[str, Any] = field(default_factory=dict)
    relay: Any = None  # othor.mail.relay.Relay; from settings when not given

    def __post_init__(self) -> None:
        from othor.connectors.sql import hostguard

        hostguard.configure(self.settings, self.sf)

    def mail(self) -> Any:
        if self.relay is None:
            from othor.mail.relay import get_relay

            self.relay = get_relay(self.settings)
        return self.relay

    def auth(self) -> Any:
        """Sign-in, to end a company's sessions when its trial closes it (open sign-up)."""
        from othor.core.auth import AuthService

        return AuthService(self.sf, self.key, clock=self.clock)


class Reschedule(Exception):
    """A handler that must wait (delivery before the run is sealed): the job goes back to the
    queue for `at`, without counting as a failed attempt."""

    def __init__(self, at: datetime) -> None:
        super().__init__(at.isoformat())
        self.at = at


Handler = Callable[[WorkerContext, Job], None]
__all__ = [
    "WorkerContext",
    "claim",
    "enqueue",
    "renew_lease",
    "run_job",
    "run_pending",
    "worker_loop",
]


def handlers() -> dict[str, Handler]:
    from othor.jobs import (
        alert_job,
        business_job,
        chat_job,
        deliver_job,
        draft_job,
        export_job,
        label_job,
        read_job,
        replay_job,
        run_job,
        scan_job,
        upload_job,
    )

    return {
        "scan": scan_job.run,
        "read": read_job.run,
        "label": label_job.run,
        "upload": upload_job.run,
        "draft": draft_job.run,
        "business": business_job.run,
        "chat": chat_job.run,
        "run": run_job.run,
        "deliver": deliver_job.run,
        "alert": alert_job.run,
        "export": export_job.run,
        "replay": replay_job.run,
    }


def claim(ctx: WorkerContext) -> tuple[uuid.UUID, uuid.UUID] | None:
    now = ctx.clock()
    with ctx.sf() as s, system_scope("worker claims the next job"):
        job = s.scalars(
            sa.select(Job)
            .where(
                sa.or_(
                    Job.status == "queued",
                    sa.and_(Job.status == "running", Job.lease_until < now),
                ),
                sa.or_(Job.run_after.is_(None), Job.run_after <= now),
                Job.attempts < MAX_ATTEMPTS,
                # a closed company's jobs are never handed out (M18.2)
                Job.company_id.in_(sa.select(Company.id).where(Company.deleted_at.is_(None))),
            )
            .order_by(Job.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.lease_until = now + LEASE
        job.started_at = job.started_at or now
        s.commit()
        return job.id, job.company_id


def renew_lease(ctx: WorkerContext, job_id: uuid.UUID) -> None:
    """A long job (a run) keeps its lease after every step, so no other worker takes it over."""
    with ctx.sf() as s:
        row = s.scalars(sa.select(Job).where(Job.id == job_id)).one()
        row.lease_until = ctx.clock() + LEASE
        s.commit()


def run_job(ctx: WorkerContext, job_id: uuid.UUID, company_id: uuid.UUID) -> None:
    with ctx.sf() as s, tenant_scope(company_id):
        job = s.scalars(sa.select(Job).where(Job.id == job_id)).one()
        s.expunge(job)
    handler = handlers().get(job.kind)
    if handler is None:  # a plug-in's job kind (M20.1); the core's kinds can't be replaced
        from othor.core import plugins

        plugins.load(ctx.settings)
        handler = plugins.jobs().get(job.kind)
    status, error = "done", None
    later: datetime | None = None
    try:
        if handler is None:
            raise RuntimeError(f"no handler for {job.kind}")
        with tenant_scope(company_id):
            handler(ctx, job)
    except Reschedule as r:
        later = r.at
    except Exception as exc:
        log.exception("job %s (%s) failed", job.id, job.kind)
        status, error = "failed", type(exc).__name__
    with ctx.sf() as s, tenant_scope(company_id):
        row = s.scalars(sa.select(Job).where(Job.id == job_id)).one()
        if later is not None:
            row.status, row.run_after = "queued", later
            row.attempts = max(row.attempts - 1, 0)
        elif status == "failed" and row.attempts < MAX_ATTEMPTS:
            row.status = "queued"
            row.run_after = ctx.clock() + timedelta(seconds=30 * row.attempts)
        else:
            row.status = status
            row.finished_at = ctx.clock()
        row.error = error
        row.lease_until = None
        s.commit()


def run_pending(ctx: WorkerContext, limit: int | None = None) -> int:
    n = 0
    while limit is None or n < limit:
        got = claim(ctx)
        if got is None:
            break
        run_job(ctx, *got)
        n += 1
    return n


MAX_BACKOFF_S = 30.0


def loop_step(
    ctx: WorkerContext | None,
    *,
    run: Any = None,
    backoff: float = 0.0,
    poll_s: float = 1.0,
) -> tuple[float, float]:
    """One turn of the worker: run what's due; return (how long to wait, the next backoff).

    A database that goes away (a restart, a failover) is waited out with a doubling wait, up to
    30 s, and logged. It never ends the process: the worker stopped for good when Postgres
    restarted, and no nightly run happened after (#16)."""
    import sqlalchemy.exc as sa_exc

    try:
        step: Any = run or run_pending  # tests pass their own
        n = step(ctx, limit=10)
    except (sa_exc.OperationalError, sa_exc.InterfaceError) as e:
        wait = min(max(backoff * 2, 1.0), MAX_BACKOFF_S)
        log.warning(
            "database unavailable; retrying", extra={"wait_s": wait, "error": type(e).__name__}
        )
        return wait, wait
    return (0.0 if n else poll_s), 0.0


def worker_loop(ctx: WorkerContext, poll_s: float = 1.0) -> None:  # pragma: no cover - CLI
    from apscheduler.schedulers.background import BackgroundScheduler

    from othor import exporting, purging, trials
    from othor.core.ratelimit import prune
    from othor.engine.scheduler import catch_up, tick
    from othor.jobs import heartbeat, licence_check, ops_alerts
    from othor.jobs.delivery_tick import tick as delivery_tick

    catch_up(ctx.sf, ctx.clock())  # a night missed while the worker was down (M11b §6)

    def both() -> None:
        heartbeat.beat(ctx.sf, ctx.clock())  # "Nightly runs are paused" when this goes quiet
        heartbeat.tick_ping(ctx.settings)  # the outside check-in, if configured
        tick(ctx.sf, ctx.clock())  # the 02:00 runs
        delivery_tick(ctx.sf, ctx.clock())  # 06:00 briefs, 8d, the Sunday digest
        prune(ctx.sf, ctx.clock())  # rate-limit windows older than a day (M11a)
        ops_alerts.check(ctx.sf, ctx.settings, ctx.mail(), ctx.clock())  # failed or late nights
        exporting.prune(ctx.settings, ctx.clock())  # built exports older than 48 h (M18.1)
        purging.tick(ctx.sf, ctx.settings, ctx.mail(), ctx.clock())  # deleted companies (M18.3)
        trials.tick(ctx.sf, ctx.settings, ctx.mail(), ctx.auth(), ctx.clock())  # free trials
        licence_check.check(ctx.sf, ctx.settings, ctx.clock())  # its state changes (M20.3)

    sched = BackgroundScheduler(timezone="UTC")
    # once a minute: queue what is due (docs/02 "scheduler · inside worker")
    sched.add_job(
        both,
        "interval",
        seconds=60,
        id="nightly",
        max_instances=1,
        coalesce=True,
        next_run_time=ctx.clock(),
    )
    sched.start()
    log.info("worker started")
    backoff = 0.0
    while True:
        wait, backoff = loop_step(ctx, backoff=backoff, poll_s=poll_s)
        if wait:
            time.sleep(wait)
