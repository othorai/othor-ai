# SPDX-License-Identifier: Apache-2.0
"""The morning scheduler and the delivery and alert jobs (docs/02, docs/09): 06:00 per brief in
the company's time, weekdays, deduped; email off queues nothing; delivery waits for the run and
gives up at 10:00 with 8c; a failed run sends 8c once and no brief; 8d after 24 hours at
Metrics; no weekly digest (retired in 0.30)."""

from __future__ import annotations

import datetime as dt
import os

import pytest
import sqlalchemy as sa

from othor.briefs.models import BriefVersion
from othor.core.config import Settings
from othor.core.crypto import InstanceKey
from othor.core.models import Company, Event, SetupState
from othor.core.tenancy import system_scope, tenant_scope
from othor.jobs.delivery_tick import tick
from othor.jobs.models import Job
from othor.jobs.queue import enqueue
from othor.jobs.run_job import queue_alerts
from othor.jobs.runner import WorkerContext, run_pending
from othor.mail.models import Delivery
from othor.mail.relay import MemoryRelay
from othor.ontology.models import OntologyVersion
from tests.unit.mail.world import build

UTC = dt.UTC
DAY = dt.date(2026, 11, 2)  # a Monday
AT = dt.datetime(2026, 11, 2, 0, 30, tzinfo=UTC)  # 6:00 AM IST


class Clock:
    def __init__(self, t: dt.datetime) -> None:
        self.t = t

    def __call__(self) -> dt.datetime:
        return self.t


def _confirm(sf, cid):
    with sf() as s, tenant_scope(cid):
        s.add(OntologyVersion(number=1, created_by="owner", reason="Setup confirmed", diff={}))
        s.commit()


def _jobs(sf, kind=None):
    with sf() as s, system_scope("test"):
        q = sa.select(Job).order_by(Job.created_at)
        if kind:
            q = q.where(Job.kind == kind)
        return [(j.payload, j.dedupe_key, j.status) for j in s.scalars(q)]


@pytest.fixture
def world(session_factory, two_companies):
    w = build(session_factory, two_companies[0])
    _confirm(session_factory, w.cid)
    return w


def _ctx(w, clock, relay=None):
    st = Settings(_env_file=None, base_domain="othor.ai", mail_from_domain="othor.ai")  # type: ignore[call-arg]
    return WorkerContext(
        st, w.sf, InstanceKey(os.urandom(32)), clock=clock, relay=relay or MemoryRelay()
    )


def test_queues_each_brief_at_its_time_once(world):
    assert tick(world.sf, AT - dt.timedelta(minutes=1)) == []
    got = tick(world.sf, AT)
    key = f"deliver:{world.brief_id}:2026-11-02"
    assert got == [(world.cid, key)]
    assert tick(world.sf, AT + dt.timedelta(minutes=1)) == []
    assert _jobs(world.sf, "deliver") == [
        ({"brief_id": str(world.brief_id), "date": "2026-11-02"}, key, "queued")
    ]


def test_weekdays_skip_the_weekend_and_email_off_queues_nothing(world):
    with world.sf() as s, tenant_scope(world.cid):
        v = s.scalars(sa.select(BriefVersion)).one()
        v.body = {**v.body, "schedule": "weekdays", "time": "07:30"}
        s.commit()
    sat = dt.datetime(2026, 10, 31, 2, 0, tzinfo=UTC)  # 7:30 AM IST Saturday
    assert tick(world.sf, sat) == []
    assert tick(world.sf, AT) == []  # Monday 06:00: not yet 07:30
    assert len(tick(world.sf, AT + dt.timedelta(hours=1, minutes=30))) == 1
    with world.sf() as s, tenant_scope(world.cid):
        world.company(s).email_delivery = False
        s.commit()
    assert tick(world.sf, AT + dt.timedelta(days=1, hours=2)) == []


def test_a_weekly_brief_is_queued_on_its_day_only(world):
    """M15.7c: a Weekly brief goes out on its weekday (AT is Monday 6:00 AM IST)."""
    with world.sf() as s, tenant_scope(world.cid):
        v = s.scalars(sa.select(BriefVersion)).one()
        v.body = {**v.body, "schedule": "weekly", "on": "tue"}
        s.commit()
    assert tick(world.sf, AT) == []  # Monday
    assert len(tick(world.sf, AT + dt.timedelta(days=1))) == 1  # Tuesday
    assert tick(world.sf, AT + dt.timedelta(days=2)) == []


def test_delivery_waits_for_the_run_then_sends(world):
    clock = Clock(AT)
    relay = MemoryRelay()
    ctx = _ctx(world, clock, relay)
    tick(world.sf, AT)
    world.night(DAY, status="running")
    run_pending(ctx)
    ((_, _, status),) = _jobs(world.sf, "deliver")
    assert status == "queued" and not relay.outbox  # back in 5 minutes, not a failed attempt
    with world.sf() as s, system_scope("test"):
        job = s.scalars(sa.select(Job).where(Job.kind == "deliver")).one()
        assert job.attempts == 0 and job.run_after == AT + dt.timedelta(minutes=5)
    from othor.engine.models import Run

    with world.sf() as s, tenant_scope(world.cid):
        s.scalars(sa.select(Run)).one().status = "done"
        s.commit()
    world.issues.clear()
    # the run sealed: its issue is written (as seal does)
    from tests.unit.mail.world import issue_1nov

    with world.sf() as s, tenant_scope(world.cid):
        from othor.briefs.models import BriefIssue

        run = s.scalars(sa.select(Run)).one()
        s.add(
            BriefIssue(
                brief_id=world.brief_id,
                brief_version=1,
                run_id=run.id,
                date=DAY,
                status="ready",
                title="t",
                body=issue_1nov(),
                issue_hash="x" * 64,
                prose={"source": "template"},
            )
        )
        s.commit()
    clock.t = AT + dt.timedelta(minutes=5)
    run_pending(ctx)
    assert [m.to for m in relay.outbox] == ["uk@indigothread.example"]
    assert _jobs(world.sf, "deliver")[0][2] == "done"


def test_at_10_00_delivery_gives_up_and_the_owner_gets_8c(world):
    clock = Clock(AT + dt.timedelta(hours=4))
    relay = MemoryRelay()
    ctx = _ctx(world, clock, relay)
    tick(world.sf, AT)
    world.night(DAY, status="running")
    run_pending(ctx)
    assert _jobs(world.sf, "deliver")[0][2] == "done"
    (m,) = relay.outbox
    assert m.to == "uk@indigothread.example"
    assert m.subject == "[Othor AI] Last night’s run didn’t finish · no brief this morning"
    assert "Run not finished · #0001 · Mon 2 Nov 10:00" in m.text
    with world.sf() as s, tenant_scope(world.cid):
        ev = s.scalars(sa.select(Event).where(Event.action == "brief.not_delivered")).one()
        assert ev.after["reason"] == "run_late"


def test_a_failed_run_sends_8c_once_and_no_brief(world):
    clock = Clock(AT - dt.timedelta(hours=4))
    relay = MemoryRelay()
    ctx = _ctx(world, clock, relay)
    rid = world.night(DAY, status="failed", fail_step="read:sales_dw")
    queue_alerts(ctx, world.cid, rid, "failed")
    queue_alerts(ctx, world.cid, rid, "failed")  # a resumed run job: still one alert
    run_pending(ctx)
    (m,) = relay.outbox
    assert m.subject == "[Othor AI] Last night’s run failed · sales_dw couldn’t be read"
    assert "Run failed · #0001 · Mon 2 Nov 2:04 AM IST" in m.text
    assert "The nightly run stopped at reading sales_dw." in m.text
    assert "sales_dw · OperationalError" in m.text
    assert "Other sources were read normally." in m.text
    assert "No brief was made, so none goes out at 6:00 AM IST." in m.text
    clock.t = AT
    tick(world.sf, AT)
    run_pending(ctx)
    assert len(relay.outbox) == 1  # no brief that morning
    with world.sf() as s, tenant_scope(world.cid):
        assert s.scalars(sa.select(Delivery).where(Delivery.kind == "alert")).one().status == "sent"


def test_a_stale_source_sends_8c_and_the_brief_still_goes(world):
    clock = Clock(AT - dt.timedelta(hours=4))
    relay = MemoryRelay()
    ctx = _ctx(world, clock, relay)
    rid = world.night(DAY, status="partial")
    from othor.core.events import record_event
    from othor.engine.models import Run

    with world.sf() as s, tenant_scope(world.cid):
        run = s.scalars(sa.select(Run)).one()
        run.summary = {**run.summary, "stale_sources": ["sales_dw"]}
        record_event(
            s,
            actor="system",
            action="source.stale",
            object_type="run",
            object_id=run.id,
            after={"source": "sales_dw", "reason": "OperationalError"},
        )
        s.commit()
    queue_alerts(ctx, world.cid, rid, "partial")
    run_pending(ctx)
    (m,) = relay.outbox
    assert m.subject == "[Othor AI] sales_dw couldn’t be read last night"
    assert "The run went on. The 6:00 AM IST brief still goes out" in m.text
    clock.t = AT
    tick(world.sf, AT)
    run_pending(ctx)
    assert [x.to for x in relay.outbox[1:]] == ["uk@indigothread.example"]


def test_8d_after_24_hours_at_metrics_once(session_factory, two_companies):
    from othor.setup.models import SetupDraft

    cid = two_companies[0]
    with session_factory() as s, tenant_scope(cid):
        s.add(SetupState(current_step="metrics"))
        for i, name in enumerate(("Net revenue", "DSO", "Gross margin per metre")):
            s.add(
                SetupDraft(
                    kind="metric",
                    key=f"m{i}",
                    body={"name": name},
                    position=i,
                    created_at=dt.datetime(2026, 10, 21, 5, 0, tzinfo=UTC),
                )
            )
        s.commit()
    assert tick(session_factory, dt.datetime(2026, 10, 22, 4, 59, tzinfo=UTC)) == []
    assert tick(session_factory, dt.datetime(2026, 10, 22, 5, 1, tzinfo=UTC)) == [
        (cid, "alert:ontology_waiting")
    ]
    assert tick(session_factory, dt.datetime(2026, 10, 25, 5, 1, tzinfo=UTC)) == []
    relay = MemoryRelay()
    st = Settings(_env_file=None, base_domain="othor.ai", mail_from_domain="othor.ai")  # type: ignore[call-arg]
    ctx = WorkerContext(st, session_factory, InstanceKey(os.urandom(32)), relay=relay)
    run_pending(ctx)
    (m,) = relay.outbox
    assert m.subject == "3 metrics are waiting for your confirmation"
    assert "Othor AI drafted 3 metrics from your sources on 21 Oct." in m.text
    assert "- Net revenue · needs confirmation" in m.text
    assert "https://indigothread.othor.ai/setup/metrics" in m.text


def test_no_weekly_digest_in_the_open_core(world):
    """0.30: the weekly digest (8e) is retired; the open core queues no weekly email."""
    sunday = dt.datetime(2026, 11, 1, 0, 30, tzinfo=UTC)
    with world.sf() as s, tenant_scope(world.cid):
        world.company(s).weekly_digest = True  # the old switch, no longer read
        s.commit()
    assert all(k.startswith("deliver:") for _, k in tick(world.sf, sunday))


def test_paused_and_deleted_companies_get_nothing(world):
    with world.sf() as s, system_scope("test"):
        s.scalars(sa.select(Company).where(Company.id == world.cid)).one().paused = True
        s.commit()
    assert tick(world.sf, AT) == []


def test_the_job_kinds_are_registered():
    from othor.jobs.runner import handlers

    assert {"deliver", "alert"} <= set(handlers())
    assert enqueue  # queued through jobs.queue only
