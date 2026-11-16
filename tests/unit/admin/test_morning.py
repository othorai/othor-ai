# SPDX-License-Identifier: Apache-2.0
"""The morning check (M15.9): last night's run sealed, replay, the ledger, the briefs due this
morning delivered with message ids, the backup fresh; one line per check, a failure for each
thing that went wrong, and a non-zero exit when any failed."""

from __future__ import annotations

import datetime as dt
import json

import sqlalchemy as sa

from othor.admin import morning
from othor.core.tenancy import tenant_scope
from othor.mail.models import Delivery
from tests.unit.mail import world

DAY = dt.date(2026, 11, 2)
AT = dt.datetime(2026, 11, 2, 1, 30, tzinfo=dt.UTC)  # 7:00 AM IST: the 06:00 brief is due


def _ok_replay(run) -> bool:  # type: ignore[no-untyped-def]
    return True


def _ok_ledger(s, cid):  # type: ignore[no-untyped-def]
    return True, "3 runs, chain intact"


def _checks(sf, cid, now=AT, replay=_ok_replay):  # type: ignore[no-untyped-def]
    from othor.core.models import Company

    with sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        return {
            x.name: x for x in morning.company_checks(s, c, now, replay=replay, ledger=_ok_ledger)
        }


def _sent(sf, cid, w, status="sent", provider="pm-1"):  # type: ignore[no-untyped-def]
    with sf() as s, tenant_scope(cid):
        s.add(
            Delivery(
                kind="brief",
                ref="brief:1",
                status=status,
                provider_id=provider,
                brief_issue_id=w.issues[DAY],
                sent_at=AT,
            )
        )
        s.commit()


def test_a_good_morning_passes_every_check(session_factory, two_companies):  # type: ignore[no-untyped-def]
    a, _b = two_companies
    w = world.build(session_factory, a)
    w.night(DAY)
    _sent(session_factory, a, w)
    got = _checks(session_factory, a)
    assert [got[k].ok for k in ("Run sealed", "Replay", "Ledger", "Briefs delivered")] == [
        True,
        True,
        True,
        True,
    ]
    assert got["Run sealed"].detail.startswith("run #0001 done at")
    assert "1 brief(s), 1 email(s) with message ids" in got["Briefs delivered"].detail


def test_each_thing_that_went_wrong_is_a_failure(session_factory, two_companies):  # type: ignore[no-untyped-def]
    a, _b = two_companies
    w = world.build(session_factory, a)
    # no run at all for today
    assert _checks(session_factory, a)["Run sealed"].ok is False
    # a failed run
    w.night(DAY, status="failed", with_issue=False, fail_step="read:erp_db")
    assert _checks(session_factory, a)["Run sealed"].detail == "run #0001 is failed"


def test_a_bounce_or_a_missing_email_fails_delivery(session_factory, two_companies):  # type: ignore[no-untyped-def]
    a, _b = two_companies
    w = world.build(session_factory, a)
    w.night(DAY)
    assert _checks(session_factory, a)["Briefs delivered"].detail == "Leadership: 0 of 1 sent"
    _sent(session_factory, a, w, status="bounced", provider=None)
    got = _checks(session_factory, a)["Briefs delivered"]
    assert got.ok is False and "1 failed or bounced" in got.detail


def test_before_the_brief_time_it_is_a_note(session_factory, two_companies):  # type: ignore[no-untyped-def]
    a, _b = two_companies
    w = world.build(session_factory, a)
    w.night(DAY)
    early = dt.datetime(2026, 11, 1, 23, 0, tzinfo=dt.UTC)  # 4:30 AM IST
    got = _checks(session_factory, a, now=early)["Briefs delivered"]
    assert got.ok is None and got.detail == "1 not due yet"


def test_a_replay_that_differs_fails(session_factory, two_companies):  # type: ignore[no-untyped-def]
    a, _b = two_companies
    w = world.build(session_factory, a)
    w.night(DAY)
    got = _checks(session_factory, a, replay=lambda run: False)["Replay"]
    assert got.ok is False and got.detail == "replay differs"


def test_the_backup_marker(tmp_path):  # type: ignore[no-untyped-def]
    assert morning.backup_check(tmp_path, AT).ok is None  # no marker here: a note
    (tmp_path / "ops").mkdir()
    marker = tmp_path / "ops" / "backup.json"
    marker.write_text(json.dumps({"at": "2026-11-01T23:00:00Z", "key": "s3://b/k.sql.gz"}))
    fresh = morning.backup_check(tmp_path, AT)
    assert fresh.ok is True and fresh.detail == "2 hours old · s3://b/k.sql.gz"
    marker.write_text(json.dumps({"at": "2026-10-30T23:00:00Z"}))
    assert morning.backup_check(tmp_path, AT).ok is False


def test_any_failure_fails_the_morning():
    good = [("indigothread", [morning.Check("Run sealed", True, "ok")])]
    assert not morning.failed(good, [morning.Check("Alarms", None, "look")])
    bad = [("indigothread", [morning.Check("Run sealed", False, "no run")])]
    assert morning.failed(bad, [])
