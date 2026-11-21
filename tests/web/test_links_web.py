# SPDX-License-Identifier: Apache-2.0
"""Recipients' pages (10d–10f) and the Postmark webhook, with no sign-in:

- 10d: the link opens the issue it names, read-only, with the recipient's footer; another
  brief's id, another date, another company host or a made-up token is the same 404;
- 10e: an expired link offers a new one, to the same address only, three a day; a removed
  recipient's link says it no longer works;
- 10f: one click off the brief, Undo, the other briefs this address gets; the mail client's
  one-click POST; the owner sees it on Edit brief · Delivery; events for each;
- the webhook: a hard bounce marks the recipient, a complaint unsubscribes, each once.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
import sqlalchemy as sa

from othor.briefs.models import Recipient
from othor.core.models import Company
from othor.core.tenancy import system_scope, tenant_scope
from othor.mail import delivery
from othor.mail.models import Delivery
from tests.unit.mail.world import build
from tests.web.conftest import Env, signup

DAY = dt.date(2026, 11, 1)
SIX = dt.datetime(2026, 11, 1, 0, 30, tzinfo=dt.UTC)


@pytest.fixture
def sent(env: Env):
    """Indigo Thread with the Leadership brief of 1 Nov sent at 06:00 to the owner and Priya."""
    env.clock.t = SIX - dt.timedelta(hours=1)
    signup(env)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, system_scope("test"):
        cid = s.scalars(sa.select(Company.id).where(Company.slug == "indigothread")).one()
    w = build(svc.sf, cid)
    w.recipient("priya@indigothread.example", "Priya Menon", "Head of Sales")
    w.night(DAY)
    env.clock.t = SIX
    with svc.sf() as s, tenant_scope(cid):
        out = delivery.deliver_brief(
            s, svc.settings, svc.key, svc.relay, w.company(s), w.brief_id, DAY, SIX
        )
    assert out.sent == 2
    env.client.cookies.clear()  # recipients have no session
    return env, w, svc


def _paths(env: Env, to: str) -> tuple[str, str]:
    m = next(m for m in reversed(env.relay.outbox) if m.to == to)
    o = re.search(r"https://indigothread\.othor\.localhost(/l/[\w-]+)", m.text).group(1)
    u = re.search(r"https://indigothread\.othor\.localhost(/u/[\w-]+)", m.text).group(1)
    return o, u


def _csrf(env: Env, url: str) -> str:
    return env.csrf(url)


def test_10d_the_link_opens_its_issue_read_only(sent):
    env, w, _svc = sent
    o, _u = _paths(env, "priya@indigothread.example")
    h = env.host("indigothread")
    r = env.client.get(h + o)
    assert r.status_code == 200
    page = r.text
    for text in (
        "Leadership brief · Sun 1 Nov 2026",
        "Shared by Uday Kamath · read-only",
        "‹ Previous days are not shared",
        "Priya Menon · priya@indigothread.example",
        "South fell ₹1.4 Cr while West’s growth hid it",
        "Question to ask today",
        "Every number is computed · details open read-only, without actions",
        "Link for priya@indigothread.example · valid until Sun 8 Nov",
        "Unsubscribe",
    ):
        assert text in page, text
    for action in ("Edit brief", "Add to a brief", "Snooze", "Mute", "Reconnect →"):
        assert action not in page, action
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "noindex" in r.headers["x-robots-tag"]
    assert "no-store" in r.headers["cache-control"]
    with w.sf() as s, tenant_scope(w.cid):
        from othor.core.models import Event
        from othor.mail.models import LinkToken

        assert s.scalars(sa.select(Event).where(Event.action == "brief.link_opened")).all()
        assert (
            s.scalars(sa.select(LinkToken).where(LinkToken.used_at.is_not(None))).one().purpose
            == "open"
        )


def test_a_link_cant_be_reused_for_another_brief_day_or_company(sent):
    env, _w, _svc = sent
    o, _u = _paths(env, "priya@indigothread.example")
    h = env.host("indigothread")
    nf = env.client.get(h + "/l/not-a-token")
    assert nf.status_code == 404
    shape = nf.text.split("<main")[1][:200]
    # the URL names nothing but the token: no brief, no date param is read
    assert env.client.get(h + o + "?date=2026-10-31").text == env.client.get(h + o).text
    # another company's host: its tenant has no such token
    signup(env, email="owner@otherco.in", company="Other Co", slug="otherco")
    env.client.cookies.clear()
    other = env.client.get(env.host("otherco") + o)
    assert other.status_code == 404 and other.text.split("<main")[1][:200] == shape
    # a finding that isn't one of the issue's items
    assert env.client.get(h + o + "/finding/notinthisissue").status_code == 404
    # unknown host
    assert env.client.get(env.host("nobody") + o).status_code == 404


def test_10e_expired_offers_a_new_link_to_the_same_address(sent):
    env, _w, _svc = sent
    o, _u = _paths(env, "priya@indigothread.example")
    h = env.host("indigothread")
    env.clock.t = SIX + dt.timedelta(days=8)
    page = env.client.get(h + o).text
    for text in (
        "Leadership brief · Sun 1 Nov",
        "This link has expired",
        "Brief links work for 7 days.",
        "Email me a new link",
        "The new link goes only to priya@indigothread.example. Briefs older than 30 days are not "
        "shared by link; ask Uday Kamath.",
    ):
        assert text in page, text
    before = len(env.relay.outbox)
    for n in range(3):
        r = env.client.post(h + o + "/renew", data={"csrf": _csrf(env, h + o)})
        assert "A new link is on its way to priya@indigothread.example." in r.text, n
    assert [m.to for m in env.relay.outbox[before:]] == ["priya@indigothread.example"] * 3
    r = env.client.post(h + o + "/renew", data={"csrf": _csrf(env, h + o)})
    assert "You’ve asked for 3 new links today" in r.text and len(env.relay.outbox) == before + 3
    new_o, _ = _paths(env, "priya@indigothread.example")
    assert new_o != o and env.client.get(h + new_o).status_code == 200


def test_a_removed_recipient_link_no_longer_works(sent):
    env, w, _svc = sent
    o, _u = _paths(env, "priya@indigothread.example")
    h = env.host("indigothread")
    with w.sf() as s, tenant_scope(w.cid):
        from othor.mail.links import revoke_for

        r = s.scalars(
            sa.select(Recipient).where(Recipient.email == "priya@indigothread.example")
        ).one()
        r.status = "removed"
        revoke_for(s, r.id, SIX)
        s.commit()
    page = env.client.get(h + o).text
    assert "This link no longer works" in page and "Ask Uday Kamath to add you back." in page
    assert "Email me a new link" not in page


def test_10f_one_click_undo_and_the_owner_sees_it(sent):
    env, w, _svc = sent
    w.recipient("priya@indigothread.example", "Priya Menon", brief_id=_second_brief(w))
    _o, u = _paths(env, "priya@indigothread.example")
    h = env.host("indigothread")
    page = env.client.get(h + u).text
    for text in (
        "Unsubscribed",
        "You won’t get the Leadership brief",
        "priya@indigothread.example is off the Leadership brief from tomorrow. "
        "Uday Kamath, who owns it, is told.",
        "Undo",
        "Other briefs you get",
        "Sales brief · daily 06:00",
    ):
        assert text in page, text
    assert _status(w, "priya@indigothread.example", w.brief_id) == "unsubscribed"
    assert env.client.get(h + u).status_code == 200  # again: still off, no second event
    assert len(env.events("recipient.unsubscribed")) == 1
    r = env.client.post(h + u + "/undo", data={"csrf": _csrf(env, h + u)})
    assert "You’ll keep getting the Leadership brief" in r.text
    assert _status(w, "priya@indigothread.example", w.brief_id) == "active"
    assert env.events("recipient.resubscribed")
    # the other brief, from the same page
    form = re.search(r'action="(/u/[\w-]+/other/[\w-]+)"', r.text).group(1)
    env.client.post(h + form, data={"csrf": _csrf(env, h + u)})
    assert _status(w, "priya@indigothread.example", _second_brief(w)) == "unsubscribed"


def test_the_mail_clients_one_click_post(sent):
    env, w, _svc = sent
    _o, u = _paths(env, "priya@indigothread.example")
    h = env.host("indigothread")
    r = env.client.post(h + u, data={"List-Unsubscribe": "One-Click"})
    assert r.status_code == 200 and r.text == "Unsubscribed."
    assert _status(w, "priya@indigothread.example", w.brief_id) == "unsubscribed"
    (ev,) = env.events("recipient.unsubscribed")
    assert ev.after["via"] == "one_click" and ev.actor.startswith("recipient:")


def test_unsubscribe_from_the_link_page(sent):
    env, w, _svc = sent
    o, _u = _paths(env, "priya@indigothread.example")
    h = env.host("indigothread")
    r = env.client.post(h + o + "/unsubscribe", data={"csrf": _csrf(env, h + o)})
    assert "You won’t get the Leadership brief" in r.text
    assert _status(w, "priya@indigothread.example", w.brief_id) == "unsubscribed"


def test_the_owner_sees_unsubscribed_on_the_delivery_tab_and_can_add_back(sent):
    env, w, _svc = sent
    _o, u = _paths(env, "priya@indigothread.example")
    h = env.host("indigothread")
    env.client.get(h + u)
    _sign_in(env)
    tab = env.client.get(h + f"/brief/{w.brief_id}/edit?tab=delivery").text
    assert "priya@indigothread.example · Head of Sales · Unsubscribed · Sun 1 Nov" in tab
    assert "Add back" in tab and "Recipients · 1" in tab
    tok = env.csrf(h + f"/brief/{w.brief_id}/edit?tab=delivery")
    env.client.post(
        h + f"/brief/{w.brief_id}/recipients",
        data={"csrf": tok, "r_email": "priya@indigothread.example", "r_name": "Priya Menon"},
    )
    assert _status(w, "priya@indigothread.example", w.brief_id) == "active"


def test_postmark_bounce_and_complaint(sent, monkeypatch):
    env, w, svc = sent
    monkeypatch.setattr(svc.settings, "postmark_webhook_secret", _secret("hook-secret"))
    with w.sf() as s, tenant_scope(w.cid):
        rows = {
            r.recipient_id: r
            for r in s.scalars(sa.select(Delivery).where(Delivery.kind == "brief"))
        }
        recs = {r.email: r.id for r in s.scalars(sa.select(Recipient))}
        priya_mid = rows[recs["priya@indigothread.example"]].provider_id
        owner_mid = rows[recs["uk@indigothread.example"]].provider_id
    root = "http://othor.localhost"
    assert env.client.post(root + "/hooks/postmark/wrong", json={}).status_code == 404
    bounce = {"RecordType": "Bounce", "Type": "HardBounce", "ID": 42, "MessageID": priya_mid}
    assert env.client.post(root + "/hooks/postmark/hook-secret", json=bounce).json() == {
        "result": "bounced"
    }
    assert env.client.post(root + "/hooks/postmark/hook-secret", json=bounce).json() == {
        "result": "duplicate"
    }
    assert _status(w, "priya@indigothread.example", w.brief_id) == "bounced"
    complaint = {"RecordType": "SpamComplaint", "ID": 7, "MessageID": owner_mid}
    env.client.post(root + "/hooks/postmark/hook-secret", json=complaint)
    assert _status(w, "uk@indigothread.example", w.brief_id) == "unsubscribed"
    unknown = {"RecordType": "Bounce", "Type": "HardBounce", "ID": 9, "MessageID": "nope"}
    assert env.client.post(root + "/hooks/postmark/hook-secret", json=unknown).json() == {
        "result": "ignored"
    }


def test_settings_have_no_weekly_digest(env: Env):
    """0.30: the weekly digest is retired; its switch is gone from Settings."""
    signup(env)
    h = env.host("indigothread")
    assert "Weekly digest" not in env.client.get(h + "/settings").text


# --- helpers -------------------------------------------------------------------------------------


def _secret(v: str):
    from pydantic import SecretStr

    return SecretStr(v)


def _status(w, email: str, brief_id) -> str:
    with w.sf() as s, tenant_scope(w.cid):
        return s.scalars(
            sa.select(Recipient.status).where(
                Recipient.email == email, Recipient.brief_id == brief_id
            )
        ).one()


def _second_brief(w):
    from othor.briefs.models import Brief, BriefVersion

    with w.sf() as s, tenant_scope(w.cid):
        got = s.scalars(sa.select(Brief).where(Brief.name == "Sales")).first()
        if got:
            return got.id
        b = Brief(name="Sales", position=1, current_version=1)
        s.add(b)
        s.flush()
        s.add(
            BriefVersion(
                brief_id=b.id,
                number=1,
                body={"name": "Sales", "schedule": "daily", "time": "06:00"},
                created_by="test",
            )
        )
        s.commit()
        return b.id


def _sign_in(env: Env) -> None:
    h = env.host("indigothread")
    tok = env.csrf(h + "/login")
    env.client.post(h + "/login", data={"csrf": tok, "email": "uk@indigothread.example"})
    code = env.last_code("uk@indigothread.example")
    env.client.post(h + "/login/code", data={"csrf": tok, "code": code})


# --- the real 1 Nov night (needs `make dev`) -----------------------------------------------------

from tests.web.test_chat_web import need_sources, sample  # noqa: E402, F401
from tests.web.test_sweep_web import _fp, night  # noqa: E402, F401


def test_the_real_night_delivered_and_opened_from_the_link(night):  # noqa: F811
    """The Leadership brief of 1 Nov from the nightly run, delivered at 06:00 and opened by its
    link: the email passes the client lint, its numbers are the issue's, the finding panel opens
    read-only, and the stored fixture the email snapshots use is this issue."""
    import json
    import os
    from pathlib import Path

    from othor.briefs.models import Brief, BriefIssue
    from tests.ui.mail import test_emails as snap

    env, h, cid, rid, svc = night
    env.clock.t = SIX
    with svc.sf() as s, tenant_scope(cid):
        bid = s.scalars(sa.select(Brief.id)).one()
        issue = s.scalars(sa.select(BriefIssue).where(BriefIssue.run_id == rid)).one()
        body = issue.body
        out = delivery.deliver_brief(
            s, svc.settings, svc.key, svc.relay, s.get(Company, cid), bid, DAY, SIX
        )
    assert out.sent == 1  # the first brief: the owner only
    (m,) = [x for x in env.relay.outbox if "/l/" in x.text]
    snap.EMAILS["_real"] = m
    try:
        snap.test_renders_in_gmail_and_outlook("_real")
    finally:
        del snap.EMAILS["_real"]
    # the brief's own weather (v0.12 part 1c), as the run stored it: the email's header
    wx = delivery.brief_weather(svc.settings, issue_run(svc, cid, rid), bid)
    # #62 (14 Nov 2026): stored by the run, but hidden from the email until v0.24
    assert wx is not None and wx["line"] not in m.text
    assert f"Today · {wx['label']}".upper() not in m.text
    allowed = (
        snap._numerals(json.dumps(body, ensure_ascii=False))
        | snap._numerals(json.dumps(wx, ensure_ascii=False))
        | {"0002", "2", "1", "2026", "06", "00"}
    )
    assert snap._numerals(m.text) <= allowed
    fixture = Path(__file__).parents[1] / "fixtures" / "mail" / "issue_1nov.json"
    wfixture = fixture.with_name("weather_1nov.json")
    if os.environ.get("OTHOR_UPDATE_GOLDEN") == "1":
        fixture.write_text(json.dumps(body, ensure_ascii=False, indent=1, sort_keys=True))
        wfixture.write_text(json.dumps(wx, ensure_ascii=False, indent=1, sort_keys=True))
    # the emails' snapshots use this brief's weather: the same scene and weeks (their numbers
    # follow the forecast bands, which land slightly apart on macOS and Linux)
    want_wx = json.loads(wfixture.read_text())
    assert (want_wx["scene"], want_wx["line"]) == (wx["scene"], wx["line"])
    assert [k["scene"] for k in want_wx["outlook"]["weeks"]] == [
        k["scene"] for k in wx["outlook"]["weeks"]
    ]

    def same(b):  # watch ids are made per test world; their order follows them
        w = sorted(({k: v for k, v in x.items() if k != "id"} for x in b["watching"]), key=str)
        tiles, bands = [], []
        for t in b["tiles"]:
            chart = json.loads(t["chart"])
            bands.append(chart.pop("band", None))
            tiles.append({**t, "chart": chart})
        # What's ahead (M19.4/5): its chart's points and its card's positions are raw forecast
        # values, which land slightly apart on macOS and Linux like the bands; compared within
        # 1% below. Everything a reader sees (sentences, words, formatted figures) stays exact.
        ahead, loose = [], []
        for r in b.get("ahead") or []:
            r = dict(r)
            loose.append({"chart": json.loads(r.pop("chart")) if r.get("chart") else None})
            if r.get("card"):
                card = dict(r["card"])
                loose[-1]["at"] = card.pop("at")
                r["card"] = card
            ahead.append(r)
        return {**b, "watching": w, "tiles": tiles, "ahead": ahead}, (bands, loose)

    want, want_bands = same(json.loads(fixture.read_text()))
    got, got_bands = same(body)
    assert want == got, "the 1 Nov issue changed; review it, then OTHOR_UPDATE_GOLDEN=1"
    # forecast bands within 1%: the ETS optimiser lands slightly apart on macOS and Linux
    # (decided 6 Nov 2026, as P13); each platform still replays byte for byte
    assert _bands_close(want_bands[0], got_bands[0]), "a tile's forecast band moved by more than 1%"
    assert _close(want_bands[1], got_bands[1]), "a What's ahead chart or card moved by more than 1%"
    o = re.search(r"https://indigothread\.othor\.localhost(/l/[\w-]+)", m.text).group(1)
    env.client.cookies.clear()
    p1 = _fp(svc, cid, rid, "net_hiding_gross")
    panel = env.client.get(h + o + f"/finding/{p1}?tab=test")
    assert panel.status_code == 200
    assert "Leadership brief · Sun 1 Nov · read-only" in panel.text
    assert "Benjamini–Hochberg" in panel.text
    assert "Snooze" not in panel.text and "Mute" not in panel.text


def _close(a, b, rel: float = 0.01) -> bool:  # type: ignore[no-untyped-def]
    """The same JSON, numbers (and numeric strings) within `rel` of each other."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k], rel) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y, rel) for x, y in zip(a, b, strict=True))
    try:
        x, y = float(a), float(b)
    except (TypeError, ValueError):
        return bool(a == b)
    return abs(x - y) <= rel * max(abs(x), abs(y), 1e-9)


def issue_run(svc, cid, rid):  # type: ignore[no-untyped-def]
    from othor.engine.models import Run

    with svc.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        s.expunge_all()
    return run


def _bands_close(a, b) -> bool:
    from decimal import Decimal

    if len(a) != len(b):
        return False
    for x, y in zip(a, b, strict=True):
        if (x is None) != (y is None):
            return False
        if x is None:
            continue
        if {k: v for k, v in x.items() if k not in ("lower", "upper")} != {
            k: v for k, v in y.items() if k not in ("lower", "upper")
        }:
            return False
        for side in ("lower", "upper"):
            if [p for p, _ in x[side]] != [p for p, _ in y[side]]:
                return False
            for (_, u), (_, v) in zip(x[side], y[side], strict=True):
                u, v = Decimal(u), Decimal(v)
                if abs(u - v) > abs(u) * Decimal("0.01"):
                    return False
    return True
