# SPDX-License-Identifier: Apache-2.0
"""Signed links (docs/02 "Security", 10d–10f): only the HMAC is stored; 7 days to open, 60 to
unsubscribe; a token is bound to its company, recipient, issue and purpose; a removed recipient's
links stop; a new link is limited to 3 a day for issues at most 30 days old."""

from __future__ import annotations

import datetime as dt
import os

import sqlalchemy as sa

from othor.briefs.models import BriefIssue, Recipient
from othor.core.crypto import InstanceKey
from othor.core.tenancy import tenant_scope
from othor.mail import links
from othor.mail.models import OPEN, UNSUBSCRIBE, Delivery, LinkToken
from tests.unit.mail.world import build

NOW = dt.datetime(2026, 11, 1, 0, 30, tzinfo=dt.UTC)  # 6:00 AM IST, Sun 1 Nov
DAY = dt.date(2026, 11, 1)


def _setup(session_factory, two_companies):
    a, b = two_companies
    w = build(session_factory, a)
    w.night(DAY)
    rid = w.recipient("priya@indigothread.example", "Priya Menon", "Head of Sales")
    return w, a, b, rid


def _issue(s, key, cid, rid, iid, purpose=OPEN, now=NOW):
    tok, _ = links.issue(
        s, key, cid, recipient_id=rid, brief_issue_id=iid, purpose=purpose, now=now
    )
    s.commit()
    return tok


def test_only_the_hmac_is_stored_and_the_token_resolves(session_factory, two_companies):
    w, a, _b, rid = _setup(session_factory, two_companies)
    key = InstanceKey(os.urandom(32))
    with session_factory() as s, tenant_scope(a):
        tok = _issue(s, key, a, rid, w.issues[DAY])
        row = s.scalars(sa.select(LinkToken)).one()
        assert tok not in row.hash and len(row.hash) == 64
        assert row.hash == links.token_hash(key, a, tok)
        assert row.expires_at == NOW + dt.timedelta(days=7)
        r = links.resolve(s, key, a, tok, OPEN, NOW + dt.timedelta(days=6, hours=23))
        assert r is not None and r.ok and r.issue.id == w.issues[DAY]
        assert r.recipient.email == "priya@indigothread.example" and r.brief.name == "Leadership"


def test_expiry_7_days_open_60_days_unsubscribe(session_factory, two_companies):
    w, a, _b, rid = _setup(session_factory, two_companies)
    key = InstanceKey(os.urandom(32))
    with session_factory() as s, tenant_scope(a):
        o = _issue(s, key, a, rid, w.issues[DAY])
        u = _issue(s, key, a, rid, w.issues[DAY], UNSUBSCRIBE)
        later = NOW + dt.timedelta(days=7)
        assert links.resolve(s, key, a, o, OPEN, later).state == "expired"
        assert links.resolve(s, key, a, u, UNSUBSCRIBE, later).ok
        assert links.resolve(s, key, a, u, UNSUBSCRIBE, NOW + dt.timedelta(days=60)).state == (
            "expired"
        )


def test_a_token_is_bound_to_its_purpose_company_and_key(session_factory, two_companies):
    w, a, b, rid = _setup(session_factory, two_companies)
    key = InstanceKey(os.urandom(32))
    with session_factory() as s, tenant_scope(a):
        o = _issue(s, key, a, rid, w.issues[DAY])
        assert links.resolve(s, key, a, o, UNSUBSCRIBE, NOW) is None  # wrong purpose
        assert links.resolve(s, key, a, o + "x", OPEN, NOW) is None
        assert links.resolve(s, key, a, "", OPEN, NOW) is None
        other_key = InstanceKey(os.urandom(32))
        assert links.resolve(s, other_key, a, o, OPEN, NOW) is None  # another instance
    with session_factory() as s, tenant_scope(b):  # another company's host
        assert links.resolve(s, key, b, o, OPEN, NOW) is None


def test_a_link_reads_its_own_issue_never_another_day(session_factory, two_companies):
    w, a, _b, rid = _setup(session_factory, two_companies)
    w.night(DAY + dt.timedelta(days=1))
    key = InstanceKey(os.urandom(32))
    with session_factory() as s, tenant_scope(a):
        o = _issue(s, key, a, rid, w.issues[DAY])
        r = links.resolve(s, key, a, o, OPEN, NOW + dt.timedelta(days=1))
        assert r.issue.id == w.issues[DAY] and r.issue.date == DAY


def test_a_removed_recipient_loses_every_link(session_factory, two_companies):
    w, a, _b, rid = _setup(session_factory, two_companies)
    key = InstanceKey(os.urandom(32))
    with session_factory() as s, tenant_scope(a):
        o = _issue(s, key, a, rid, w.issues[DAY])
        u = _issue(s, key, a, rid, w.issues[DAY], UNSUBSCRIBE)
        rec = s.scalars(sa.select(Recipient).where(Recipient.id == rid)).one()
        rec.status = "removed"
        assert links.revoke_for(s, rid, NOW) == 2
        s.commit()
        assert links.resolve(s, key, a, o, OPEN, NOW).state == "removed"
        assert links.resolve(s, key, a, u, UNSUBSCRIBE, NOW).state == "removed"


def test_an_unsubscribed_recipient_still_opens_until_expiry(session_factory, two_companies):
    w, a, _b, rid = _setup(session_factory, two_companies)
    key = InstanceKey(os.urandom(32))
    with session_factory() as s, tenant_scope(a):
        o = _issue(s, key, a, rid, w.issues[DAY])
        s.scalars(sa.select(Recipient).where(Recipient.id == rid)).one().status = "unsubscribed"
        s.commit()
        assert links.resolve(s, key, a, o, OPEN, NOW).ok


def test_a_new_link_same_address_3_a_day_30_days(session_factory, two_companies):
    w, a, _b, rid = _setup(session_factory, two_companies)
    key = InstanceKey(os.urandom(32))
    with session_factory() as s, tenant_scope(a):
        o = _issue(s, key, a, rid, w.issues[DAY])
        later = NOW + dt.timedelta(days=8)
        r = links.resolve(s, key, a, o, OPEN, later)
        assert links.can_reissue(s, r, DAY + dt.timedelta(days=8), later) is None
        for n in range(3):
            s.add(
                Delivery(
                    kind="link",
                    brief_issue_id=w.issues[DAY],
                    recipient_id=rid,
                    ref=f"link:{n}",
                    created_at=later - dt.timedelta(hours=n),
                )
            )
        s.commit()
        assert links.can_reissue(s, r, DAY + dt.timedelta(days=8), later) == "limit"
        assert links.can_reissue(
            s, r, DAY + dt.timedelta(days=31), later + dt.timedelta(days=2)
        ) == ("too_old")
        r.recipient.status = "unsubscribed"
        assert links.can_reissue(s, r, DAY + dt.timedelta(days=8), later) == "inactive"


def test_an_archived_brief_is_not_found(session_factory, two_companies):
    w, a, _b, rid = _setup(session_factory, two_companies)
    key = InstanceKey(os.urandom(32))
    with session_factory() as s, tenant_scope(a):
        o = _issue(s, key, a, rid, w.issues[DAY])
        iss = s.scalars(sa.select(BriefIssue)).one()
        from othor.briefs.models import Brief

        s.scalars(sa.select(Brief).where(Brief.id == iss.brief_id)).one().status = "archived"
        s.commit()
        assert links.resolve(s, key, a, o, OPEN, NOW) is None
