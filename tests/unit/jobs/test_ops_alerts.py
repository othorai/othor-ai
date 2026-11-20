# SPDX-License-Identifier: Apache-2.0
"""Ops alerts (M11b §6): a nightly run that failed, or isn't sealed by 05:00 company time, emails
`OTHOR_OPS_EMAIL` once. Separate from 8c to the owner; one line per company with the run number,
and no data."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa

from othor.core.config import Settings
from othor.core.models import PlatformEvent
from othor.core.tenancy import system_scope, tenant_scope
from othor.engine.models import Run
from othor.engine.scheduler import nightly_key
from othor.jobs import ops_alerts
from othor.jobs.queue import enqueue
from othor.mail.relay import MemoryRelay
from othor.ontology.models import OntologyVersion, Spec

UTC = dt.UTC
DAY = dt.date(2026, 11, 2)
# Mon 2 Nov, company time (IST = UTC+5:30)
AT_0300 = dt.datetime(2026, 11, 1, 21, 30, tzinfo=UTC)
AT_0459 = dt.datetime(2026, 11, 1, 23, 29, tzinfo=UTC)
AT_0500 = dt.datetime(2026, 11, 1, 23, 30, tzinfo=UTC)


def _settings(ops: str = "ops@othor.example") -> Settings:
    return Settings(_env_file=None, ops_email=ops, mail_from_domain="othor.ai")  # type: ignore[call-arg]


def _running(sf, cid, *, before: dt.date = dt.date(2026, 11, 1)) -> None:
    """A company that has been running nightly (confirmed, with an earlier night)."""
    with sf() as s, tenant_scope(cid):
        s.add(OntologyVersion(number=1, created_by="owner", reason="Setup confirmed", diff={}))
        enqueue(
            s,
            "run",
            {"kind": "nightly", "run_date": before.isoformat()},
            dedupe_key=nightly_key(before),
        )
        s.commit()


def _run(sf, cid, status: str, number: int = 42, day: dt.date = DAY) -> uuid.UUID:
    with sf() as s, tenant_scope(cid):
        spec = s.scalars(sa.select(Spec)).first()
        if spec is None:
            spec = Spec(ontology_version=1, compiler_version="t", compiled={}, hash="x" * 64)
            s.add(spec)
            s.flush()
        run = Run(
            number=number,
            kind="nightly",
            run_date=day,
            spec_id=spec.id,
            image_version="test",
            started_at=AT_0300,
            status=status,
        )
        s.add(run)
        s.commit()
        return run.id


def _check(sf, relay, now, settings=None) -> list[str]:
    return ops_alerts.check(sf, settings or _settings(), relay, now)


def test_a_failed_run_alerts_once(session_factory, two_companies) -> None:
    a, _ = two_companies
    _running(session_factory, a)
    _run(session_factory, a, "failed")
    relay = MemoryRelay()
    assert _check(session_factory, relay, AT_0300) == [
        "Indigo Thread Co. (indigothread): run #0042 failed · Mon 2 Nov"
    ]
    assert _check(session_factory, relay, AT_0300 + dt.timedelta(minutes=1)) == []
    assert _check(session_factory, relay, AT_0500) == []  # failed already said; not "late" too
    assert len(relay.outbox) == 1
    m = relay.outbox[0]
    assert m.to == "ops@othor.example" and m.subject == "Othor AI · 1 night needs a look"
    with session_factory() as s, system_scope("test"):
        assert s.scalars(
            sa.select(PlatformEvent.object_id).where(PlatformEvent.action == "ops.alerted")
        ).all() == [f"ops:failed:{a}:2026-11-02"]


def test_not_sealed_by_05_00_alerts_once(session_factory, two_companies) -> None:
    a, b = two_companies
    for c in (a, b):
        _running(session_factory, c)
    _run(session_factory, a, "running")  # a started and hangs; b never started
    relay = MemoryRelay()
    assert _check(session_factory, relay, AT_0459) == []
    got = _check(session_factory, relay, AT_0500)
    assert got == [
        "Indigo Thread Co. (indigothread): run #0042 not sealed by 5:00 AM IST · Mon 2 Nov",
        "Other Co (otherco): no run by 5:00 AM IST · Mon 2 Nov",
    ]
    assert len(relay.outbox) == 1 and relay.outbox[0].subject == "Othor AI · 2 nights need a look"
    assert _check(session_factory, relay, AT_0500 + dt.timedelta(hours=1)) == []


def test_a_sealed_run_is_quiet(session_factory, two_companies) -> None:
    a, b = two_companies
    _running(session_factory, a)
    _run(session_factory, a, "done")
    _run(session_factory, a, "failed", number=43, day=dt.date(2026, 10, 1))  # an old night
    # b was never set up to run: no expectation, no alert
    relay = MemoryRelay()
    assert _check(session_factory, relay, AT_0500) == []
    assert relay.outbox == []
    _ = b


def test_a_later_run_by_hand_doesnt_make_a_sealed_night_late(
    session_factory, two_companies
) -> None:
    """19 Nov 2026: the morning's nightly sealed; an operator's re-read ran another nightly of
    the same day in the evening, and the 05:00 check said that night needed a look."""
    a, _ = two_companies
    _running(session_factory, a)
    _run(session_factory, a, "done")
    _run(session_factory, a, "running", number=43)
    relay = MemoryRelay()
    assert _check(session_factory, relay, AT_0500 + dt.timedelta(hours=15)) == []
    assert relay.outbox == []


def test_off_without_an_address_and_nothing_but_names_and_numbers(
    session_factory, two_companies
) -> None:
    a, _ = two_companies
    _running(session_factory, a)
    _run(session_factory, a, "failed")
    relay = MemoryRelay()
    assert _check(session_factory, relay, AT_0300, _settings(ops="")) == []
    assert relay.outbox == []
    _check(session_factory, relay, AT_0300)
    m = relay.outbox[0]
    body = m.text
    # the company, the run number, the date and a pointer to the runbook: no row values,
    # no metric names, no error text
    lines = [line for line in body.splitlines() if line.strip()]
    assert lines[1] == "Indigo Thread Co. (indigothread): run #0042 failed · Mon 2 Nov"
    assert "docs/runbook.md" in body
    assert "₹" not in body and "Traceback" not in body


def test_checking_leaves_no_company_currency_or_zone_behind(session_factory, two_companies) -> None:
    """14 Nov 2026 (the full test run): each company's currency and time zone apply to its own
    lines only; after the check the thread formats as it did before."""
    from othor.core import fmt

    a, _ = two_companies
    with session_factory() as s, system_scope("test"):
        from othor.core.models import Company

        s.scalars(sa.select(Company).where(Company.id == a)).one().currency = "USD"
        s.commit()
    _running(session_factory, a)
    _run(session_factory, a, "failed")
    before = (fmt.currency(), fmt.clock("06:00"))
    ops_alerts.cases(session_factory, AT_0300)
    assert (fmt.currency(), fmt.clock("06:00")) == before
