# SPDX-License-Identifier: Apache-2.0
"""The nightly replay (owner, 16 Nov 2026): once the morning's briefs are due, last night's run
is replayed by a low-priority job and the result kept as a `run.replayed` event, so Home's
"Checked" light can say the numbers come out the same."""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa

from othor.core.events import SYSTEM_ACTOR
from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.jobs.delivery_tick import tick
from othor.jobs.models import Job
from othor.jobs.runner import WorkerContext, run_pending
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

UTC = dt.UTC


def test_last_nights_run_is_replayed_once_after_the_brief_time(night):  # noqa: F811
    env, _h, cid, rid, svc = night
    # 5:00 AM IST: before the brief time, nothing is queued
    tick(svc.sf, dt.datetime(2026, 10, 31, 23, 30, tzinfo=UTC))
    with svc.sf() as s, tenant_scope(cid):
        assert not s.scalars(sa.select(Job).where(Job.kind == "replay")).all()
    # 6:30 AM IST: one replay job for the night's run, queued once however often the tick runs
    at = dt.datetime(2026, 11, 1, 1, 0, tzinfo=UTC)
    tick(svc.sf, at)
    tick(svc.sf, at)
    with svc.sf() as s, tenant_scope(cid):
        jobs = s.scalars(sa.select(Job).where(Job.kind == "replay")).all()
        assert [j.dedupe_key for j in jobs] == [f"replay:{rid}"]
    env.clock.t = at
    run_pending(WorkerContext(svc.settings, svc.sf, svc.key, clock=svc.clock, relay=svc.relay))
    with svc.sf() as s, tenant_scope(cid):
        ev = s.scalars(
            sa.select(Event).where(Event.action == "run.replayed", Event.object_id == str(rid))
        ).all()
        assert len(ev) == 1 and ev[0].actor == SYSTEM_ACTOR and ev[0].after["ok"] is True
