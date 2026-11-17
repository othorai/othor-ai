# SPDX-License-Identifier: Apache-2.0
"""A run as a job: nightly (the scheduler), dry run #0001 (Confirm), a source-scoped manual run
(Read now) or Run again now (`rerun`, othor.engine.again). The job creates its run once and
resumes it after a killed worker: the lease expires, the next claim runs this again, and the
orchestrator skips the steps already done.

payload: {"kind": "nightly" | "dry" | "manual" | "rerun", "run_date": "YYYY-MM-DD",
          "scope": {...} | None}
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

import sqlalchemy as sa

from othor.core.models import Company
from othor.engine import orchestrator
from othor.engine.context import EngineContext
from othor.engine.models import Run
from othor.jobs.models import Job
from othor.jobs.runner import WorkerContext, renew_lease

KILL_AT = "OTHOR_TEST_KILL_AT"  # tests only: the worker process exits as this step starts


def run(ctx: WorkerContext, job: Job) -> None:
    with ctx.sf() as s:
        existing = s.scalars(sa.select(Run).where(Run.job_id == job.id)).first()
        if existing is None:
            company = s.scalars(sa.select(Company).where(Company.id == job.company_id)).one()
            try:
                existing = orchestrator.start(
                    s,
                    company,
                    kind=job.payload["kind"],
                    run_date=dt.date.fromisoformat(job.payload["run_date"]),
                    scope=job.payload.get("scope"),
                    now=ctx.clock(),
                    image_version=ctx.settings.image_version,
                    job_id=job.id,
                )
            except orchestrator.NoSpec:
                return  # nothing confirmed (or the source feeds no metric): nothing to run
            s.commit()
        run_id = existing.id
    ectx = EngineContext(
        ctx.settings,
        ctx.sf,
        ctx.key,
        clock=ctx.clock,
        renew=lambda: renew_lease(ctx, job.id),
        fault=_kill_hook(ctx),
    )
    status = orchestrator.execute(ectx, run_id)
    if job.payload["kind"] == "nightly":
        queue_alerts(ctx, job.company_id, run_id, status)
    from othor.jobs import heartbeat

    heartbeat.after_run(ctx.settings, job.payload["kind"], status)  # the nightly check-in
    if status in ("done", "partial"):
        from othor.core import plugins

        plugins.after_seal(ctx, job.company_id, run_id)  # needs no model (rule 4)
    if status in ("done", "partial") and ctx.gateway is not None:
        rewrite_prose(ctx, job.company_id, run_id)


def queue_alerts(ctx: WorkerContext, company_id: uuid.UUID, run_id: uuid.UUID, status: str) -> None:
    """8c to the owner as the nightly run ends (docs/09): it failed (no brief this morning), or
    a source couldn't be read (the brief goes out with that source's numbers marked). Once per
    run, and once per source per day."""
    from othor.core.models import Event
    from othor.core.tenancy import tenant_scope
    from othor.jobs.queue import enqueue

    with ctx.sf() as s, tenant_scope(company_id):
        run = s.scalars(sa.select(Run).where(Run.id == run_id)).one()
        todo: list[tuple[dict[str, str], str]] = []
        if status == "failed":
            todo.append(
                ({"kind": "run_failed", "run_id": str(run.id)}, f"alert:run_failed:{run.id}")
            )
        elif status == "partial":
            reasons = {
                (e.after or {}).get("source"): (e.after or {}).get("reason") or ""
                for e in s.scalars(
                    sa.select(Event).where(
                        Event.action == "source.stale", Event.object_id == str(run.id)
                    )
                )
            }
            for src in (run.summary or {}).get("stale_sources") or []:
                todo.append(
                    (
                        {
                            "kind": "source_stale",
                            "run_id": str(run.id),
                            "source": src,
                            "reason": str(reasons.get(src) or ""),
                        },
                        f"alert:stale:{src}:{run.run_date.isoformat()}",
                    )
                )
        for payload, key in todo:
            if s.scalars(sa.select(Job.id).where(Job.dedupe_key == key)).first():
                continue
            enqueue(s, "alert", payload, dedupe_key=key)
        s.commit()


def rewrite_prose(ctx: WorkerContext, company_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """After seal, outside the run (docs/04 §9): the optional rewrite of each brief issue's
    prose, behind the numbers guard. A failure leaves the template; the run is untouched."""
    from othor.briefs.rewrite import rewrite_run
    from othor.core.tenancy import tenant_scope

    try:
        with ctx.sf() as s, tenant_scope(company_id):
            rewrite_run(s, ctx.gateway, company_id, run_id)
            s.commit()
    except Exception as exc:  # the brief stays in template words
        import logging

        logging.getLogger(__name__).warning("brief rewrite failed: %s", type(exc).__name__)
    try:
        suggest_briefs(ctx, company_id, run_id)
    except Exception as exc:  # no suggestions tonight; nothing else depends on them
        import logging

        logging.getLogger(__name__).warning("brief suggestions failed: %s", type(exc).__name__)
    try:
        rank_pair_suggestions(ctx, company_id, run_id)
    except Exception as exc:  # code's ranking stays; nothing else depends on it
        import logging

        logging.getLogger(__name__).warning("pair ranking failed: %s", type(exc).__name__)
    try:
        draft_areas(ctx, company_id)
    except Exception as exc:  # no proposal tonight; nothing else depends on it
        import logging

        logging.getLogger(__name__).warning("area draft failed: %s", type(exc).__name__)


def rank_pair_suggestions(ctx: WorkerContext, company_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Helpful questions (v0.12): Othor AI ranks the suggested pairs from their names, after
    seal; code's computed evidence still orders the cards and says why."""
    import sqlalchemy as sa

    from othor.core.tenancy import tenant_scope
    from othor.home.suggest_ai import rank_pairs
    from othor.ontology.models import Spec

    with ctx.sf() as s, tenant_scope(company_id):
        run = s.scalars(sa.select(Run).where(Run.id == run_id)).one()
        spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one()
        rank_pairs(s, ctx.gateway, company_id, dict(spec.compiled))
        s.commit()


def draft_areas(ctx: WorkerContext, company_id: uuid.UUID) -> None:
    """Areas (M16.1): Othor AI groups the numbers that have no area yet, from their names, after
    seal; one Home proposal that changes nothing until the owner accepts it."""
    from othor.core.tenancy import tenant_scope
    from othor.home.areas_ai import propose_areas

    with ctx.sf() as s, tenant_scope(company_id):
        propose_areas(s, ctx.gateway, company_id)
        s.commit()


def suggest_briefs(ctx: WorkerContext, company_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Up to 3 suggested briefs (4b) from the business message, the Ontology and the night's
    findings (their computed titles), drafted by Othor AI after seal."""
    import sqlalchemy as sa

    from othor.briefs import service
    from othor.briefs.drafts import suggest
    from othor.core.tenancy import tenant_scope
    from othor.engine.store import RunFiles, read_json
    from othor.ontology.models import Spec
    from othor.setup.models import BusinessMessage

    with ctx.sf() as s, tenant_scope(company_id):
        run = s.scalars(sa.select(Run).where(Run.id == run_id)).one()
        spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one()
        msg = s.scalars(sa.select(BusinessMessage)).first()
        files = RunFiles(ctx.settings.runs_dir(company_id), run_id)
        titles = [
            (f.get("text") or {}).get("title", "")
            for f in read_json(files.outputs / "findings.json") or []
            if f.get("status") == "open"
        ]
        existing = []
        for b in service.active(s):
            v = service.current(s, b)
            if v is not None:
                existing.append(str(v.body.get("question", "")))
        suggest(
            s,
            ctx.gateway,
            company_id,
            dict(spec.compiled),
            msg.text if msg else "",
            [t for t in titles if t],
            existing,
        )
        s.commit()


def _kill_hook(ctx: WorkerContext):  # type: ignore[no-untyped-def]
    target = os.environ.get(KILL_AT)
    if ctx.settings.env != "test" or not target:
        return None

    def hook(step: str) -> None:
        if step == target:
            os._exit(137)

    return hook
