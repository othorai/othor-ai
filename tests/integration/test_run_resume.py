# SPDX-License-Identifier: Apache-2.0
"""Kill the worker mid-run and restart: the run resumes (docs/10 M5 acceptance). The first worker
is a real process that exits as `materialise:sales_dw` starts; the second claims the job once its
lease has expired and finishes the run without redoing the steps already done."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

import sqlalchemy as sa

from othor.connectors.models import RunQuery
from othor.core.tenancy import tenant_scope
from othor.engine.models import Run, RunStep
from othor.engine.replay import replay
from othor.jobs.models import Job
from othor.jobs.queue import enqueue
from othor.jobs.run_job import KILL_AT
from othor.jobs.runner import LEASE, WorkerContext, run_pending
from tests.integration._kill_worker import NOW
from tests.integration.run_world import confirm_sample, engine_ctx, require_sources


def test_killed_worker_resumes(
    db_url,
    session_factory,
    two_companies,
    sample,
    tmp_path,
    sqlite_only,
    sample_sources_seeded,
):
    require_sources()
    cid, _ = two_companies
    ctx = engine_ctx(session_factory, tmp_path, clock=lambda: NOW)
    ids = confirm_sample(ctx, cid, sample)
    with ctx.sf() as s, tenant_scope(cid):
        job = enqueue(s, "run", {"kind": "dry", "run_date": "2026-11-02"}, dedupe_key="dry:first")
        s.commit()
        job_id = job.id
    env = {**os.environ, KILL_AT: "materialise:sales_dw"}
    p = subprocess.run(
        [sys.executable, "-m", "tests.integration._kill_worker", db_url, str(tmp_path)],
        env=env,
        capture_output=True,
        timeout=600,
    )
    assert p.returncode == 137, p.stderr.decode()[-2000:]
    with ctx.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.job_id == job_id)).one()
        assert run.status == "running"
        st = {
            x.name: x.status for x in s.scalars(sa.select(RunStep).where(RunStep.run_id == run.id))
        }
        assert st["materialise:erp_db"] == "done" and st["materialise:sales_dw"] == "queued"
        erp_before = _erp_queries(s, run.id, ids["erp_db"])
        assert erp_before
        assert s.scalars(sa.select(Job.status).where(Job.id == job_id)).one() == "running"
    # a new worker before the lease expires leaves it alone; after, it resumes
    later = NOW + LEASE + dt.timedelta(seconds=1)
    wctx = WorkerContext(ctx.settings, ctx.sf, ctx.key, clock=lambda: NOW + dt.timedelta(minutes=1))
    assert run_pending(wctx) == 0
    wctx = WorkerContext(ctx.settings, ctx.sf, ctx.key, clock=lambda: later)
    assert run_pending(wctx) == 1
    with ctx.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.job_id == job_id)).one()
        assert run.status == "done"
        assert s.scalar(sa.select(sa.func.count()).select_from(Run)) == 1  # resumed, not restarted
        # erp_db's steps done before the kill sent none of their queries again (its Sweep
        # probes run after the reads, so they are new)
        after = _erp_queries(s, run.id, ids["erp_db"])
        assert set(erp_before) <= set(after)
        assert len(after) == len(set(after))
        assert s.scalars(sa.select(Job.status).where(Job.id == job_id)).one() == "done"
    with tenant_scope(cid):
        assert replay(ctx, run.id).ok


def _erp_queries(s, run_id, erp_id) -> list[tuple[str, str]]:
    """erp_db's queries in this run, each as its sql and parameters."""
    rows = s.scalars(
        sa.select(RunQuery).where(RunQuery.run_id == run_id, RunQuery.source_id == erp_id)
    )
    return [(q.sql, json.dumps(q.params, sort_keys=True)) for q in rows]
