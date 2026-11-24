# SPDX-License-Identifier: Apache-2.0
"""What to do (Morning Brief 2.5, #110) on the sample's 1 Nov night: the brief's section after
its items, the What to do page in the side menu with its tabs, Home's card, Snooze and Mute from
a row (an action holding two findings holds both, and comes back to the page), Watch only where a
watch can be drafted, the read-only view without buttons."""

from __future__ import annotations

import html
import re

import sqlalchemy as sa

from othor.briefs.models import Brief, BriefIssue
from othor.core.tenancy import tenant_scope
from othor.engine import holds
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

DISPATCH = "83f4e966442fc01cd415cf2c"
SOUTH = "2b7b32c5f8a79322b25382aa"


def _text(page: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def _brief(svc, cid):
    with svc.sf() as s, tenant_scope(cid):
        b = s.scalars(sa.select(Brief)).one()
        i = s.scalars(sa.select(BriefIssue).where(BriefIssue.brief_id == b.id)).one()
        return b.id, i.body


def _run(svc, cid) -> int:
    from othor.engine.models import Run

    with svc.sf() as s, tenant_scope(cid):
        b = s.scalars(sa.select(Brief)).one()
        i = s.scalars(sa.select(BriefIssue).where(BriefIssue.brief_id == b.id)).one()
        return int(s.get(Run, i.run_id).number)


def test_the_issue_stores_its_actions_and_the_brief_shows_them(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    bid, body = _brief(svc, cid)
    acts = body["actions"]
    assert [a["kind"] for a in acts] == ["goal", "finding", "finding"]
    assert acts[0]["text"].startswith("Find ₹3.3 Cr more net revenue by 31 Mar")
    assert acts[1]["fingerprints"] == [SOUTH] and acts[1]["who"]["name"] == "Priya Menon"
    assert acts[2]["by"] is None  # "Spread usually comes before complaints" is no deadline
    page = env.client.get(h + f"/brief/{bid}").text
    sec = page[page.index('aria-label="Action plan"') :]
    t = _text(sec)
    for x in (
        "Action plan",
        "Find ₹3.3 Cr more net revenue by 31 Mar",
        "Call Tessora EU and Ravira Exports",
        "Ask Priya Menon, Head of Sales",
        "November close in 29 days",
        "₹4.59 Cr at risk",
    ):
        assert x in t, x
    # holds return to the brief; Watch only on the one whose check can draft a watch
    n = _run(svc, cid)
    assert f"/sweep/finding/{SOUTH}/snooze?run={n}&amp;back=brief-{bid}" in sec
    # 0.33.1: Watch opens the panel in place over the brief
    assert f"/brief/{bid}?panel=watch&amp;fp={DISPATCH}&amp;run={n}" in sec
    assert f"fp={SOUTH}" not in sec
    # the figures in the sentences are bold (0.33.1, the owner's approved design)
    assert "<b>₹3.3 Cr</b>" in sec and "<b>31 Mar</b>" in sec


def test_the_what_to_do_page_and_homes_card(night):  # noqa: F811
    env, h, _cid, _rid, _svc = night
    page = env.client.get(h + "/action-plan").text
    assert 'class="nav-item is-on" href="/action-plan" aria-current="page"' in page
    t = _text(page[page.index('id="main"') :])
    for x in (
        "Action plan",
        "From this morning’s 1 brief · 1 Nov, 06:00",
        "All · 3",
        "Leadership · 3",
        "Start here",
        "Nothing to do",
        "Skarfab paused orders until 1 Dec",
    ):
        assert x in t, x
    home = env.client.get(h + "/home").text
    card = home[home.index('aria-label="Today’s action plan"') :]
    assert "See all 3 →" in _text(card) and "back=home" in card


def test_a_hold_from_the_page_holds_every_finding_of_its_action(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    tok = env.csrf(h + "/action-plan")
    n = _run(svc, cid)
    r = env.client.post(
        h + f"/sweep/finding/{SOUTH}/snooze?run={n}&back=what-to-do&also={DISPATCH}",
        data={"csrf": tok},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/action-plan"
    with svc.sf() as s, tenant_scope(cid):
        held = {x["fingerprint"] for x in holds.active(s, env.clock.t.date())[0]}
    assert {SOUTH, DISPATCH} <= held
    t = _text(env.client.get(h + "/action-plan").text)
    assert "Snoozed until" in t and "2 snoozed" in t and "See them in Sweep →" in t
    r = env.client.post(
        h + f"/sweep/finding/{SOUTH}/undo?run={n}&back=what-to-do&also={DISPATCH}",
        data={"csrf": tok},
        follow_redirects=False,
    )
    with svc.sf() as s, tenant_scope(cid):
        assert not holds.active(s, env.clock.t.date())[0]


def test_back_goes_nowhere_else(night):  # noqa: F811
    from othor.web.routes.sweep import back_to

    assert back_to("what-to-do") == "/action-plan" and back_to("home") == "/home"
    assert back_to("action-plan") == "/action-plan"  # its name since 22 Nov 2026
    assert back_to("https://evil.example") is None and back_to("/admin") is None


def test_the_read_only_view_has_the_actions_without_buttons(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    bid, _ = _brief(svc, cid)
    page = env.client.get(h + f"/brief/{bid}/shared").text
    sec = page[page.index('aria-label="Action plan"') :]
    assert "Call Tessora EU and Ravira Exports" in _text(sec)
    assert "/snooze" not in sec and "/sweep/watch/" not in sec


# --- 0.33.1: Watch this in place, from a row, Home's card and a brief card ------------------


def test_watch_opens_in_place_on_each_page_and_comes_back_there(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    bid, _ = _brief(svc, cid)
    n = _run(svc, cid)
    q = f"?panel=watch&fp={DISPATCH}&run={n}"
    pages = (("/action-plan", "action-plan"), ("/home", "home"), (f"/brief/{bid}", f"brief-{bid}"))
    for path, key in pages:
        page = env.client.get(h + path + q).text
        assert "Watch this" in page, path
        assert f"/sweep/watch/{DISPATCH}/start?run={n}&amp;back={key}" in page, path
    # Start watching returns to the page it was opened from, and the row says it is watched
    tok = env.csrf(h + "/action-plan" + q)
    page = env.client.get(h + "/action-plan" + q).text
    thr = re.search(r'name="threshold"[^>]*value="([^"]+)"', page).group(1)
    r = env.client.post(
        h + f"/sweep/watch/{DISPATCH}/start?run={n}&back=action-plan",
        data={"csrf": tok, "threshold": html.unescape(thr), "owner": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/action-plan"
    assert "Watching · alert above" in _text(env.client.get(h + "/action-plan").text)
    # a gone finding: the page as usual
    assert "Watch this" not in env.client.get(h + "/action-plan?panel=watch&fp=nope").text


def test_a_brief_card_has_watch_beside_snooze_and_mute(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    bid, _ = _brief(svc, cid)
    n = _run(svc, cid)
    page = env.client.get(h + f"/brief/{bid}").text
    cards = page[: page.index('aria-label="Action plan"')]
    assert f"/brief/{bid}?panel=watch&amp;fp={DISPATCH}&amp;run={n}" in cards
    assert f"fp={SOUTH}" not in cards  # its check can't draft a watch


def test_figures_in_a_sentence_are_bold_and_escaped():
    from othor.web.todo_view import bold_figures

    out = str(bold_figures("Find ₹3.3 Cr by 31 Mar: 16 customers <b>x</b>, 12%."))
    assert "<b>₹3.3 Cr</b>" in out and "<b>31 Mar</b>" in out and "<b>12%</b>" in out
    assert "<b>16 customers</b>" in out and "&lt;b&gt;x&lt;/b&gt;" in out


def test_a_run_again_now_issue_counts_as_this_mornings(night):  # noqa: F811
    """22 Nov 2026 on indigodemo: after Run again now, the page read the 06:00 issue and said
    "Nothing needs you today" beside the rerun's three actions. A rerun's issue is shown, as on
    Home and the brief cards."""
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        i = s.scalars(sa.select(BriefIssue)).one()
        i.status = "rerun"
        s.commit()
    try:
        t = _text(env.client.get(h + "/action-plan").text)
        assert "All · 3" in t and "Nothing needs you today" not in t
    finally:
        with svc.sf() as s, tenant_scope(cid):
            i = s.scalars(sa.select(BriefIssue)).one()
            i.status = "ready"
            s.commit()


def test_what_to_do_is_the_action_plan_now(night):  # noqa: F811
    """Owner, 22 Nov 2026: "What to do" is named "Action plan"; the old address still opens it."""
    env, h, _cid, _rid, _svc = night
    r = env.client.get(h + "/what-to-do?panel=watch&fp=x", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/action-plan?panel=watch&fp=x"
    page = env.client.get(h + "/action-plan").text
    assert "<title>Action plan" in page and ">Action plan</a>" in page
    assert "What to do" not in _text(page)
    home = env.client.get(h + "/home").text
    assert "Today’s action plan" in home and 'href="/action-plan"' in home


def _earlier_day(svc, cid, n_muted: int) -> tuple[list, list]:
    """An earlier day's issue (four days before today's) whose actions have since gone: the DSO
    goal (on track in today's issue) and `n_muted` findings the owner muted the day before
    today. Returns the rows to remove afterwards."""
    import datetime as dt

    from othor.engine.models import Mute, Run

    with svc.sf() as s, tenant_scope(cid):
        today_issue = s.scalars(sa.select(BriefIssue)).one()
        run = s.get(Run, today_issue.run_id)
        day = today_issue.date - dt.timedelta(days=4)
        old = Run(
            company_id=cid,
            workspace_id=cid,
            number=900,
            kind="nightly",
            run_date=day,
            spec_id=run.spec_id,
            image_version="test",
            started_at=run.started_at,
            status="done",
        )
        s.add(old)
        s.flush()
        acts = [
            {
                "kind": "goal",
                "key": "goal:dso_under_45",
                "fingerprints": [],
                "text": "Bring DSO down by 5 d by 31 Mar.",
                "why": [{"title": "DSO goal", "goal": "dso_under_45"}],
            }
        ] + [
            {
                "kind": "finding",
                "key": f"finding:gone{k}",
                "fingerprints": [f"gone{k}"],
                "text": f"Call customer {k} about their 3 late invoices.",
                "why": [{"title": f"Customer {k} paid late", "fingerprint": f"gone{k}"}],
            }
            for k in range(n_muted)
        ]
        issue = BriefIssue(
            company_id=cid,
            workspace_id=cid,
            brief_id=today_issue.brief_id,
            brief_version=today_issue.brief_version,
            run_id=old.id,
            date=day,
            status="ready",
            title="Earlier",
            body={"actions": acts},
            issue_hash="x" * 64,
            prose={},
        )
        s.add(issue)
        when = dt.datetime.combine(
            today_issue.date - dt.timedelta(days=1), dt.time(9), tzinfo=dt.UTC
        )
        mutes = [
            Mute(
                company_id=cid,
                workspace_id=cid,
                pattern=f"p{k}",
                fingerprint=f"gone{k}",
                label="",
                actor="owner",
                created_at=when,
            )
            for k in range(n_muted)
        ]
        s.add_all(mutes)
        s.commit()
        return [issue.id, old.id], [m.id for m in mutes]


def _drop(svc, cid, ids: list, mutes: list) -> None:
    from othor.engine.models import Mute, Run

    with svc.sf() as s, tenant_scope(cid):
        s.execute(sa.delete(Mute).where(Mute.id.in_(mutes)))
        s.execute(sa.delete(BriefIssue).where(BriefIssue.id == ids[0]))
        s.execute(sa.delete(Run).where(Run.id == ids[1]))
        s.commit()


def test_recently_resolved_lists_what_left_and_why(night):  # noqa: F811
    """Owner, 22 Nov 2026: the Action plan's history. A goal back on track and findings the
    owner muted are listed, newest first, five then Show all; Home counts this week's."""
    env, h, cid, _rid, svc = night
    ids, mutes = _earlier_day(svc, cid, n_muted=6)
    try:
        page = env.client.get(h + "/action-plan").text
        sec = page[page.index('id="resolved"') :]
        t = _text(sec)
        assert "Recently resolved" in t and "Last 30 days" in t
        # the sample's DSO goal is at risk today, no longer off track
        assert "Bring DSO down by" in t and "No longer off track" in t
        assert "Call customer 0 about their" in t and "You muted it" in t
        assert "Show all 7" in t and "See its history" in t
        assert "Open 4 days · 28 Oct–1 Nov" in t and "Open 3 days · 28 Oct–31 Oct" in t
        assert t.index("Bring DSO down") < t.index("Call customer")  # newest first
        assert "<b>3 late</b>" in sec or "<b>3</b>" in sec  # figures bold, as today's rows
        # today's own actions are not in the history
        assert "Call Tessora EU and Ravira Exports" not in t
        home = env.client.get(h + "/home").text
        card = home[home.index('aria-label="Today’s action plan"') :]
        assert "7 resolved this week" in _text(card) and 'href="/action-plan#resolved"' in card
    finally:
        _drop(svc, cid, ids, mutes)


def test_recently_resolved_says_when_there_is_nothing(night):  # noqa: F811
    env, h, _cid, _rid, _svc = night
    t = _text(env.client.get(h + "/action-plan").text)
    assert "Recently resolved" in t and "Nothing resolved in the last 30 days." in t
    assert "resolved this week" not in _text(env.client.get(h + "/home").text)


def test_homes_action_plan_card_sits_below_the_briefs(night):  # noqa: F811
    """Owner, 22 Nov 2026: Home's action plan below the briefs, above Suggested by Othor AI."""
    env, h, _cid, _rid, _svc = night
    home = env.client.get(h + "/home").text
    briefs, card = home.index('aria-label="Briefs"'), home.index('aria-label="Today’s action plan"')
    assert briefs < card
    if 'id="suggested"' in home:
        assert card < home.index('id="suggested"')


def test_the_briefs_action_plan_is_last_just_above_since_last_week(night):  # noqa: F811
    """Owner, 22 Nov 2026: in each brief the Action plan comes after everything else (What's
    ahead, Watching, the question to ask) and just above Since last week."""
    env, h, cid, _rid, svc = night
    bid, _body = _brief(svc, cid)
    page = env.client.get(h + f"/brief/{bid}").text
    plan = page.index('aria-label="Action plan"')
    items = page.find("What needs you")
    items = items if items >= 0 else page.index("Needs attention")
    assert items < plan
    if "What’s ahead" in page:
        assert page.index("What’s ahead") < plan
    for later in ('aria-label="Since last week"', ">Since last week<"):
        if later in page:
            assert plan < page.index(later)
