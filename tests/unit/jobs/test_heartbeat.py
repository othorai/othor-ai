# SPDX-License-Identifier: Apache-2.0
"""The worker is watched from outside it (M11b §6, after #16): a heartbeat row written every
minute, the missed-night catch-up on start, and the optional check-in pings."""

from __future__ import annotations

import datetime as dt

import httpx
import sqlalchemy as sa

from othor.core.config import Settings
from othor.core.models import Company
from othor.core.tenancy import system_scope, tenant_scope
from othor.engine.scheduler import catch_up, nightly_key
from othor.jobs import heartbeat
from othor.jobs.models import Job, WorkerHeartbeat
from othor.jobs.queue import enqueue
from othor.ontology.models import OntologyVersion

UTC = dt.UTC
T0 = dt.datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


def test_one_row_however_many_beats(session_factory) -> None:
    assert heartbeat.last_beat(session_factory) is None
    heartbeat.beat(session_factory, T0)
    heartbeat.beat(session_factory, T0 + dt.timedelta(minutes=1))
    with session_factory() as s:
        assert s.scalar(sa.select(sa.func.count()).select_from(WorkerHeartbeat)) == 1
    assert heartbeat.last_beat(session_factory) == T0 + dt.timedelta(minutes=1)


def test_paused_only_after_ten_quiet_minutes(session_factory) -> None:
    # no beat ever (a fresh instance, or tests): not called paused, there's nothing to compare
    assert heartbeat.paused_since(session_factory, T0) is None
    heartbeat.beat(session_factory, T0)
    assert heartbeat.paused_since(session_factory, T0 + dt.timedelta(minutes=10)) is None
    assert heartbeat.paused_since(session_factory, T0 + dt.timedelta(minutes=11)) == T0


# --- the missed night ---------------------------------------------------------------------------


def _confirm(sf, cid) -> None:
    with sf() as s, tenant_scope(cid):
        s.add(OntologyVersion(number=1, created_by="owner", reason="Setup confirmed", diff={}))
        s.commit()


def _nightly(sf, cid, day: dt.date) -> None:
    with sf() as s, tenant_scope(cid):
        enqueue(
            s, "run", {"kind": "nightly", "run_date": day.isoformat()}, dedupe_key=nightly_key(day)
        )
        s.commit()


def _keys(sf, cid) -> list[str]:
    with sf() as s, tenant_scope(cid):
        return sorted(k for k in s.scalars(sa.select(Job.dedupe_key)) if k)


# back at 1:00 AM IST on Tue 3 Nov after a worker that stopped on Sun 1 Nov: Mon 2 Nov was missed
BACK = dt.datetime(2026, 11, 2, 19, 30, tzinfo=UTC)


def test_a_missed_night_is_queued_once_on_start(session_factory, two_companies) -> None:
    a, _ = two_companies
    _confirm(session_factory, a)
    _nightly(session_factory, a, dt.date(2026, 11, 1))
    assert catch_up(session_factory, BACK) == [(a, dt.date(2026, 11, 2))]
    assert catch_up(session_factory, BACK) == []  # a second start doesn't queue it again
    assert _keys(session_factory, a) == ["nightly:2026-11-01", "nightly:2026-11-02"]


def test_no_catch_up_without_a_missed_night(session_factory, two_companies) -> None:
    a, b = two_companies
    for c in (a, b):
        _confirm(session_factory, c)
    _nightly(session_factory, a, dt.date(2026, 11, 2))  # a ran last night
    # b never ran a night (set up today: its first run is the dry run, not a catch-up)
    assert catch_up(session_factory, BACK) == []
    with session_factory() as s, system_scope("test"):
        s.get(Company, a).paused = True
        s.commit()
    _nightly(session_factory, b, dt.date(2026, 10, 30))
    assert catch_up(session_factory, BACK) == [(b, dt.date(2026, 11, 2))]


# --- the pings ----------------------------------------------------------------------------------


def _recorder(status: int = 200) -> tuple[list[httpx.Request], httpx.MockTransport]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status)

    return seen, httpx.MockTransport(handle)


def test_a_ping_is_one_empty_get_to_the_configured_url() -> None:
    seen, transport = _recorder()
    assert heartbeat.ping("https://hc-ping.com/abc-123", transport=transport)
    assert len(seen) == 1
    r = seen[0]
    assert r.method == "GET" and str(r.url) == "https://hc-ping.com/abc-123" and r.content == b""


def test_a_ping_never_raises_and_off_means_off() -> None:
    seen, transport = _recorder(500)
    assert heartbeat.ping("https://hc-ping.com/x", transport=transport) is False

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert heartbeat.ping("https://hc-ping.com/x", transport=httpx.MockTransport(boom)) is False
    assert heartbeat.ping("", transport=transport) is False
    assert heartbeat.ping("http://hc-ping.com/x", transport=transport) is False  # https only
    assert len(seen) == 1


def test_the_nightly_ping_fires_only_for_a_sealed_nightly_run() -> None:
    settings = Settings(_env_file=None, nightly_ping_url="https://hc-ping.com/night")  # type: ignore[call-arg]
    seen, transport = _recorder()
    for kind, status in (("dry", "done"), ("nightly", "failed"), ("manual", "partial")):
        assert heartbeat.after_run(settings, kind, status, transport=transport) is False
    assert heartbeat.after_run(settings, "nightly", "partial", transport=transport) is True
    assert [str(r.url) for r in seen] == ["https://hc-ping.com/night"]
