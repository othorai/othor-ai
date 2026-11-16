# SPDX-License-Identifier: Apache-2.0
"""Re-run now (8c, 4e1; M11a): a failed nightly run resumes from the step that stopped, as the
same run, and replays byte for byte. Needs `make dev && make seed`."""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa

from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from othor.engine import orchestrator
from othor.engine.context import EngineContext
from othor.engine.models import Run
from othor.engine.replay import replay
from othor.jobs.models import Job
from tests.fixtures.sample_company.history import STEADY_RUN
from tests.web.test_chat_web import _jobs, need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import _post, night  # noqa: F401


def _fail_night(env, svc, cid, monkeypatch) -> Run:  # type: ignore[no-untyped-def]
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    real = orchestrator._step

    def broken(c, info, spec, name, status):  # type: ignore[no-untyped-def]
        if name == "derive":
            raise RuntimeError("derive stopped")
        return real(c, info, spec, name, status)

    monkeypatch.setattr(orchestrator, "_step", broken)
    env.clock.t = dt.datetime(2026, 11, 1, 21, 0, tzinfo=dt.UTC)  # 2:30 AM IST, Mon 2 Nov
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        r = orchestrator.start(
            s,
            c,
            kind="nightly",
            run_date=STEADY_RUN + dt.timedelta(days=1),
            now=ctx.clock(),
            image_version="0.1.0",
        )
        s.commit()
        rid = r.id
    with tenant_scope(cid), pytest.raises(RuntimeError):
        orchestrator.execute(ctx, rid)
    monkeypatch.setattr(orchestrator, "_step", real)
    with svc.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        assert run.status == "failed"
        s.expunge(run)
        return run


def test_rerun_resumes_the_same_run_and_replays(night, monkeypatch):  # noqa: F811
    env, h, cid, _rid, svc = night
    run = _fail_night(env, svc, cid, monkeypatch)
    panel = env.client.get(f"{h}/brief/archive?failed={run.number}").text
    assert "Re-run now" in panel

    calls: list[str] = []
    real = orchestrator._step

    def counting(c, info, spec, name, status):  # type: ignore[no-untyped-def]
        calls.append(name)
        return real(c, info, spec, name, status)

    r = _post(env, h, f"/brief/archive/runs/{run.number}/rerun")
    assert r.status_code == 303
    assert "Re-running" in env.client.get(f"{h}/brief/archive?failed={run.number}").text
    ev = env.events("run.rerun_requested")
    assert len(ev) == 1 and ev[0].object_id == str(run.id)

    monkeypatch.setattr(orchestrator, "_step", counting)
    _jobs(env)
    with svc.sf() as s, tenant_scope(cid):
        done = s.scalars(sa.select(Run).where(Run.id == run.id)).one()
        assert done.status in ("done", "partial")
        assert s.scalar(sa.select(sa.func.count(Run.id))) == run.number  # no new run
        assert s.scalars(sa.select(Job).where(Job.id == done.job_id)).one().status == "done"
    assert calls and calls[0] == "derive"  # the reads before it were not re-run
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    with tenant_scope(cid):
        assert replay(ctx, run.id).ok


def test_only_the_latest_failed_run(night, monkeypatch):  # noqa: F811
    env, h, cid, rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        n = s.scalars(sa.select(Run.number).where(Run.id == rid)).one()
    assert _post(env, h, f"/brief/archive/runs/{n}/rerun").status_code == 404  # sealed
    run = _fail_night(env, svc, cid, monkeypatch)
    assert _post(env, h, f"/brief/archive/runs/{run.number + 7}/rerun").status_code == 404
    assert env.events("run.rerun_requested") == []
