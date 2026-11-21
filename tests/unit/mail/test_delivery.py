# SPDX-License-Identifier: Apache-2.0
"""The 06:00 delivery (docs/09, M9 acceptance): one email per active recipient, idempotent on a
retry; an unsubscribed recipient gets nothing next morning; email off sends nothing; a failed
run sends no brief; a run still going waits; a bounce marks the recipient; the format picks
8b, 8b1 or 10q; the email repeats the stored issue and nothing else."""

from __future__ import annotations

import datetime as dt
import os
import re

import pytest
import sqlalchemy as sa

from othor.briefs.models import Recipient
from othor.core.config import Settings
from othor.core.crypto import InstanceKey
from othor.core.models import Company, Event
from othor.core.tenancy import tenant_scope
from othor.mail import delivery
from othor.mail.models import Delivery, LinkToken
from othor.mail.recipients import unsubscribe
from othor.mail.relay import MemoryRelay
from tests.unit.mail.world import build, quiet_from

DAY = dt.date(2026, 11, 1)
SIX = dt.datetime(2026, 11, 1, 0, 30, tzinfo=dt.UTC)  # 6:00 AM IST
SETTINGS = Settings(_env_file=None, base_domain="othor.ai", mail_from_domain="othor.ai")  # type: ignore[call-arg]


@pytest.fixture
def world(session_factory, two_companies):
    w = build(session_factory, two_companies[0])
    w.recipient("priya@indigothread.example", "Priya Menon", "Head of Sales")
    return w


def _deliver(w, relay, day=DAY, now=SIX, key=None):
    with w.sf() as s, tenant_scope(w.cid):
        return delivery.deliver_brief(
            s,
            SETTINGS,
            key or InstanceKey(os.urandom(32)),
            relay,
            w.company(s),
            w.brief_id,
            day,
            now,
        )


def test_one_email_per_recipient_with_their_own_links(world):
    world.night(DAY)
    relay = MemoryRelay()
    out = _deliver(world, relay)
    assert (out.status, out.sent, out.failed) == ("sent", 2, 0)
    assert sorted(m.to for m in relay.outbox) == [
        "priya@indigothread.example",
        "uk@indigothread.example",
    ]
    opens = [
        re.search(r"https://indigothread\.othor\.ai/l/([\w-]+)", m.text).group(1)
        for m in relay.outbox
    ]
    assert len(set(opens)) == 2  # each recipient their own token
    m = relay.outbox[0]
    assert (
        m.sender == '"Indigo Thread Co. · Othor AI" <brief@othor.ai>'
        and m.reply_to == "uk@indigothread.example"
    )
    assert m.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert m.headers["List-Unsubscribe"].startswith("<https://indigothread.othor.ai/u/")
    assert m.subject == "South fell ₹1.4 Cr; West’s rise hid it · Leadership · Sun 1 Nov"
    with world.sf() as s, tenant_scope(world.cid):
        rows = s.scalars(sa.select(Delivery)).all()
        assert {r.status for r in rows} == {"sent"} and all(r.provider_id for r in rows)
        assert s.scalar(sa.select(sa.func.count()).select_from(LinkToken)) == 4  # open + unsub each
        evs = s.scalars(sa.select(Event).where(Event.action == "brief.delivered")).all()
        assert len(evs) == 2 and all(
            "priya" not in str(e.after) for e in evs
        )  # no body, no address


def test_a_retry_sends_nothing_twice(world):
    world.night(DAY)
    relay = MemoryRelay()
    _deliver(world, relay)
    again = _deliver(world, relay)
    assert (again.sent, again.skipped) == (0, 2) and len(relay.outbox) == 2


def test_a_failed_send_is_retried_alone(world):
    world.night(DAY)
    relay = MemoryRelay()
    relay.fail = True
    out = _deliver(world, relay)
    assert (out.sent, out.failed) == (0, 2)
    relay.fail = False
    out = _deliver(world, relay)
    assert (out.sent, out.skipped) == (2, 0) and len(relay.outbox) == 2


def test_unsubscribed_recipients_get_nothing_next_morning(world):
    """M9 acceptance: Priya unsubscribes from Saturday's email; Sunday's goes to the owner only."""
    sat = DAY - dt.timedelta(days=1)
    world.night(sat)
    relay = MemoryRelay()
    _deliver(world, relay, sat, SIX - dt.timedelta(days=1))
    assert {m.to for m in relay.outbox} == {"uk@indigothread.example", "priya@indigothread.example"}
    with world.sf() as s, tenant_scope(world.cid):
        priya = s.scalars(
            sa.select(Recipient).where(Recipient.email == "priya@indigothread.example")
        ).one()
        assert unsubscribe(s, priya, SIX - dt.timedelta(hours=12), via="link")
        s.commit()
    relay.outbox.clear()
    world.night(DAY)
    out = _deliver(world, relay)
    assert [m.to for m in relay.outbox] == ["uk@indigothread.example"] and out.sent == 1


def test_email_off_sends_nothing(world):
    world.night(DAY)
    with world.sf() as s, tenant_scope(world.cid):
        world.company(s).email_delivery = False
        s.commit()
    relay = MemoryRelay()
    assert _deliver(world, relay).status == "off" and not relay.outbox


@pytest.mark.parametrize(
    ("night", "status"),
    [
        ({"status": "failed", "fail_step": "read:sales_dw"}, "failed_run"),
        ({"with_issue": False}, "no_issue"),  # a brief made after the run has no issue yet
    ],
    ids=["failed_run", "no_issue_yet"],
)
def test_no_brief_is_sent_without_an_issue_to_send(world, night, status):
    world.night(DAY, **night)
    relay = MemoryRelay()
    assert _deliver(world, relay).status == status and not relay.outbox


def test_a_run_still_going_waits(world):
    relay = MemoryRelay()
    assert _deliver(world, relay).status == "wait"  # no run yet
    world.night(DAY, status="running")
    assert _deliver(world, relay).status == "wait" and not relay.outbox


def test_the_first_brief_goes_to_the_owner_only(session_factory, two_companies):
    w = build(session_factory, two_companies[0])  # as the first run makes it: owner only
    w.night(DAY)
    relay = MemoryRelay()
    _deliver(w, relay)
    assert [m.to for m in relay.outbox] == ["uk@indigothread.example"]


def test_a_bounce_marks_the_recipient_and_skips_them_after(world):
    world.night(DAY)
    relay = MemoryRelay()
    relay.refuse.add("priya@indigothread.example")
    out = _deliver(world, relay)
    assert (out.sent, out.bounced) == (1, 1)
    with world.sf() as s, tenant_scope(world.cid):
        p = s.scalars(
            sa.select(Recipient).where(Recipient.email == "priya@indigothread.example")
        ).one()
        assert p.status == "bounced" and p.bounced_at == SIX
        assert s.scalars(sa.select(Event).where(Event.action == "recipient.bounced")).one()
    world.night(DAY + dt.timedelta(days=1))
    relay.outbox.clear()
    _deliver(world, relay, DAY + dt.timedelta(days=1), SIX + dt.timedelta(days=1))
    assert [m.to for m in relay.outbox] == ["uk@indigothread.example"]


@pytest.mark.parametrize(
    ("fmt", "quiet", "marker"),
    [
        ("simple", False, "What needs"),  # 8b
        ("full", False, "Needs attention · 4"),  # 8b1
        ("full", True, "Here are some updates from your numbers"),  # 10q whatever the format
    ],
)
def test_the_format_picks_the_email(world, fmt, quiet, marker):
    from tests.unit.mail.world import issue_1nov

    body = quiet_from(issue_1nov()) if quiet else None
    world.set_format(fmt)
    world.night(DAY, body)
    relay = MemoryRelay()
    _deliver(world, relay)
    m = relay.outbox[0]
    if marker == "What needs":
        assert "How is this" not in m.html and len(m.images) > 1  # 8b: item charts as PNG
        assert "Needs attention" not in m.html
    else:
        assert marker in m.html
    if quiet:
        assert m.subject == "Quiet day · Leadership · Sun 1 Nov" and "Net revenue · Oct" in m.html


def test_the_full_email_names_the_sources_the_run_read(world):
    world.set_format("full")
    world.night(DAY)
    relay = MemoryRelay()
    _deliver(world, relay)
    assert "Sources fresh · erp_db 2:03 AM · sales_dw 2:04 AM" in relay.outbox[0].text
    assert "run #0001 · spec v2 · replayable" in relay.outbox[0].text


def test_the_email_uses_the_rewritten_words_only_when_they_passed(world):
    world.night(DAY)
    with world.sf() as s, tenant_scope(world.cid):
        from othor.briefs.models import BriefIssue

        iss = s.scalars(sa.select(BriefIssue)).one()
        iss.prose = {"source": "model", "model": {"title": "South slipped; West covered it."}}
        s.commit()
    relay = MemoryRelay()
    _deliver(world, relay)
    assert "South slipped; West covered it." in relay.outbox[0].html


def test_another_company_is_untouched(world, two_companies):
    world.night(DAY)
    _deliver(world, MemoryRelay())
    with world.sf() as s, tenant_scope(two_companies[1]):
        assert not s.scalars(sa.select(Delivery)).all()
        assert s.scalars(sa.select(Company)).all() is not None
