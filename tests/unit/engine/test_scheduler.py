# SPDX-License-Identifier: Apache-2.0
"""The scheduler (othor.engine.scheduler): one nightly job per company and local day."""

import datetime as dt

import sqlalchemy as sa

from othor.core.models import Company
from othor.core.tenancy import system_scope, tenant_scope
from othor.engine.scheduler import tick
from othor.jobs.models import Job
from othor.ontology.models import OntologyVersion

UTC = dt.UTC


def _confirm(sf, cid):
    with sf() as s, tenant_scope(cid):
        s.add(OntologyVersion(number=1, created_by="owner", reason="Setup confirmed", diff={}))
        s.commit()


def _jobs(sf):
    with sf() as s, system_scope("test"):
        return [(j.company_id, j.payload, j.dedupe_key) for j in s.scalars(sa.select(Job)).all()]


def test_queues_at_02_00_company_time_once(session_factory, two_companies):
    a, _ = two_companies
    _confirm(session_factory, a)
    # 2:00 AM IST = 8:30 PM UTC the day before
    assert tick(session_factory, dt.datetime(2026, 11, 1, 20, 29, tzinfo=UTC)) == []
    got = tick(session_factory, dt.datetime(2026, 11, 1, 20, 30, tzinfo=UTC))
    assert got == [(a, dt.date(2026, 11, 2))]
    assert tick(session_factory, dt.datetime(2026, 11, 1, 20, 31, tzinfo=UTC)) == []
    assert _jobs(session_factory) == [
        (a, {"kind": "nightly", "run_date": "2026-11-02"}, "nightly:2026-11-02")
    ]


def test_unconfirmed_and_paused_companies_dont_run(session_factory, two_companies):
    _, b = two_companies
    _confirm(session_factory, b)
    with session_factory() as s, system_scope("test"):
        s.get(Company, b).paused = True
        s.commit()
    assert tick(session_factory, dt.datetime(2026, 11, 2, 3, 0, tzinfo=UTC)) == []


def test_catches_up_the_same_day_and_runs_again_the_next(session_factory, two_companies):
    a, _ = two_companies
    _confirm(session_factory, a)
    # worker down at 02:00, back at 5:00 AM IST
    assert tick(session_factory, dt.datetime(2026, 11, 1, 23, 30, tzinfo=UTC)) == [
        (a, dt.date(2026, 11, 2))
    ]
    assert tick(session_factory, dt.datetime(2026, 11, 2, 20, 30, tzinfo=UTC)) == [
        (a, dt.date(2026, 11, 3))
    ]


def test_another_time_zone(session_factory, two_companies):
    a, _ = two_companies
    _confirm(session_factory, a)
    with session_factory() as s, system_scope("test"):
        s.get(Company, a).timezone = "Europe/Berlin"
        s.commit()
    assert tick(session_factory, dt.datetime(2026, 11, 2, 0, 59, tzinfo=UTC)) == []
    assert tick(session_factory, dt.datetime(2026, 11, 2, 1, 0, tzinfo=UTC)) == [
        (a, dt.date(2026, 11, 2))
    ]
