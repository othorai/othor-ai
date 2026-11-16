# SPDX-License-Identifier: Apache-2.0
"""The nightly replay (owner, 16 Nov 2026): last night's run replayed from its stored inputs,
after the morning's briefs, and the result kept as a `run.replayed` event (the same event the
Sweep's "Replay" button writes), for Home's "Checked" light. Low priority by when it's queued;
no model; idempotent (a second job for the same run finds the event and stops).

payload: {"run_id"}
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from othor.core.events import SYSTEM_ACTOR, record_event
from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.engine.context import EngineContext
from othor.engine.replay import replay
from othor.jobs.models import Job
from othor.jobs.runner import WorkerContext


def run(ctx: WorkerContext, job: Job) -> None:
    company_id = job.company_id
    rid = uuid.UUID(str((job.payload or {})["run_id"]))
    with ctx.sf() as s, tenant_scope(company_id):
        done = s.scalars(
            sa.select(Event.id).where(
                Event.action == "run.replayed",
                Event.object_id == str(rid),
                Event.actor == SYSTEM_ACTOR,
            )
        ).first()
    if done is not None:
        return
    ectx = EngineContext(ctx.settings, ctx.sf, ctx.key, clock=ctx.clock)
    with tenant_scope(company_id):
        r = replay(ectx, rid)
    with ctx.sf() as s, tenant_scope(company_id):
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="run.replayed",
            object_type="run",
            object_id=rid,
            after={"ok": r.ok, "output_hash": r.replayed_hash, "differences": len(r.diff)},
        )
        s.commit()
