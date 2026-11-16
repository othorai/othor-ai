# SPDX-License-Identifier: Apache-2.0
"""The questions panel (3aa; v0.11, docs/plans/questions-panel.md part 1) on the steady-state night
of Sun 1 Nov 2026: one question per screen; "Question 2 of 4 · 1 answered" and a segment per
question (answered filled, current outlined); the Othor AI mark, never the "AI" letters; replies
as radio cards; Back · Next as links in a fixed order, Done on the last; Ask me later sets the
question aside for 7 days and moves on; Back to an answered question shows what it did with Undo
and Change answer; Home's Questions box says what's left."""

from __future__ import annotations

import datetime as dt
import html
import re
import uuid
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from othor.home import answers
from othor.ontology import service
from othor.ontology.models import Question
from othor.web import home_view as hv
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_in_place import _post as _post_in_place
from tests.web.test_sweep_web import night  # noqa: F401

TODAY = dt.date(2026, 11, 1)
NOTE = "Drafts only. Numbers are computed, not written. Nothing changes until you apply."


def _post(env, h, path, data=None, **kw):
    kw.setdefault("follow_redirects", False)
    return _post_in_place(env, h, path, data, **kw)


NAV = re.compile(r'<nav class="qp-nav".*?</nav>', re.S)


def _order(svc, cid) -> list[str]:
    with svc.sf() as s, tenant_scope(cid):
        return [str(q.id) for q in answers.todays_order(s, TODAY, ZoneInfo("Asia/Kolkata"))]


def _nav(page: str) -> str:
    m = NAV.search(page)
    assert m, "the panel has Back · Next"
    return m.group(0)


def _href(html: str, cls: str) -> str | None:
    m = re.search(rf'<a class="{cls}" href="([^"]+)"', html)
    return m.group(1).replace("&amp;", "&") if m else None


def _segs(page: str) -> list[str]:
    return re.findall(r'<li class="seg( [a-z ]+)?"', page)


def test_one_question_per_screen_with_the_step_line_and_segments(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    order = _order(svc, cid)
    n = len(order)
    assert n >= 2
    page = env.client.get(h + "/home?panel=questions").text
    assert "Questions for you" in page
    # #33 option C (owner, 12 Nov 2026): the answers here are the owner's, not drafts by Othor AI
    assert NOTE not in page.split('id="chat-panel"', 1)[1]
    assert f"<b>Question 1 of {n}</b> · 0 answered" in page
    segs = _segs(page)
    assert len(segs) == n and segs[0] == " now" and all(s == "" for s in segs[1:])
    # the brand mark with the source word, not the "AI" letters
    panel = page.split('id="chat-panel"', 1)[1]
    assert 'class="nb-ai"' in panel and 'aria-label="Othor AI"' in panel
    assert "avatar-ai" not in panel
    # quick replies are a real radio group; Ask me later and the main button name the action
    assert 'type="radio" name="pick"' in panel and "Use this answer" in panel
    assert "Ask me later" in panel and "Skip" not in panel
    # Back is not a link on the first; Next goes to the second
    nav = _nav(page)
    assert _href(nav, "back") is None and _href(nav, "next") == hv.panel_url_id(order[1])


def test_back_and_next_walk_a_fixed_order_and_done_closes_on_the_last(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    order = _order(svc, cid)
    url = "/home?panel=questions"
    for k in range(len(order)):
        page = env.client.get(h + url).text
        assert f"<b>Question {k + 1} of {len(order)}</b>" in page
        nav = _nav(page)
        if k:
            assert _href(nav, "back") == hv.panel_url_id(order[k - 1])
        nxt = _href(nav, "next")
        if k < len(order) - 1:
            assert nxt == hv.panel_url_id(order[k + 1]) and ">Next<" in nav
            url = nxt
        else:
            assert nxt == "/home" and ">Done<" in nav  # Done closes the panel
    # Next only moves: nothing was answered
    with svc.sf() as s, tenant_scope(cid):
        assert not s.scalars(sa.select(Question).where(Question.status == "answered")).all()


def test_an_answer_keeps_its_place_and_back_shows_undo_and_change_answer(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    order = _order(svc, cid)
    with svc.sf() as s, tenant_scope(cid):
        q = next(
            s.get(Question, uuid.UUID(i))
            for i in order
            if s.get(Question, uuid.UUID(i)).kind == "needs_confirmation"
        )
        qid, side, at = str(q.id), q.subject["sides"][1], order.index(str(q.id))
        before = service.current_number(s)
    r = _post(env, h, f"/home/questions/{qid}/answer", {"pick": f"{side} is right"})
    assert r.status_code == 303 and r.headers["location"] == hv.panel_url_id(qid)
    page = env.client.get(h + r.headers["location"]).text
    assert f"<b>Question {at + 1} of {len(order)}</b> · 1 answered" in page
    assert _segs(page)[at] == " done now"
    assert "You answered" in page and f"applied in Ontology v{before + 1}" in page
    assert f'action="/home/questions/{qid}/undo"' in page and "Change answer" in page
    with svc.sf() as s, tenant_scope(cid):
        assert s.get(Question, uuid.UUID(qid)).answer_text == side
    assert env.events("question.answered")  # every answer is an event (moved from test_home_web)
    # another question: the answered one is filled, the numbering hasn't moved
    other = next(i for i in order if i != qid)
    page = env.client.get(h + hv.panel_url_id(other)).text
    assert f"of {len(order)}</b> · 1 answered" in page and _segs(page)[at] == " done"
    # Change answer: undone, the question asks again
    r = _post(env, h, f"/home/questions/{qid}/undo", {"change": "1"})
    assert r.headers["location"].startswith(hv.panel_url_id(qid))
    with svc.sf() as s, tenant_scope(cid):
        assert s.get(Question, uuid.UUID(qid)).status == "open"
    page = env.client.get(h + r.headers["location"]).text
    assert "Use this answer" in page and "You answered" not in page
    assert env.events("question.undone")


def test_we_dont_have_this_replies_through_the_reply_route(night):  # noqa: F811
    """The "We don't have this" button posts to /reply (moved from test_home_web)."""
    env, h, cid, _rid, svc = night
    order = _order(svc, cid)
    with svc.sf() as s, tenant_scope(cid):
        q = next(
            s.get(Question, uuid.UUID(i))
            for i in order
            if s.get(Question, uuid.UUID(i)).kind == "needs_confirmation"
        )
        qid, side = str(q.id), q.subject["sides"][1]
    r = _post(env, h, f"/home/questions/{qid}/reply", {"choice": f"{side} is right"})
    assert r.headers["location"] == f"/home?panel=questions&q={qid}"
    with svc.sf() as s, tenant_scope(cid):
        q = s.get(Question, uuid.UUID(qid))
        assert q.status == "answered" and q.answer_text == side


def test_ask_me_later_sets_it_aside_for_7_days_and_moves_on(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    order = _order(svc, cid)
    r = _post(env, h, f"/home/questions/{order[0]}/skip")
    assert r.headers["location"] == hv.panel_url_id(order[1])
    with svc.sf() as s, tenant_scope(cid):
        q = s.get(Question, uuid.UUID(order[0]))
        assert q.status == "skipped" and q.skipped_until == TODAY + dt.timedelta(days=7)
    assert _order(svc, cid) == order  # it keeps its place today
    page = env.client.get(h + hv.panel_url_id(order[0])).text
    assert f"<b>Question 1 of {len(order)}</b>" in page and "Set aside until 8 Nov" in page
    # Home's Questions box says what's left; the last one set aside closes the panel
    for qid in order[1:]:
        r = _post(env, h, f"/home/questions/{qid}/skip")
    assert r.headers["location"] == "/home"
    home = env.client.get(h + "/home").text
    assert f"{len(order)} for later" in home


def test_home_says_whats_left_after_the_panel(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    order = _order(svc, cid)
    with svc.sf() as s, tenant_scope(cid):
        q = next(
            s.get(Question, uuid.UUID(i))
            for i in order
            if s.get(Question, uuid.UUID(i)).kind == "needs_confirmation"
        )
        qid, side = str(q.id), q.subject["sides"][0]
    _post(env, h, f"/home/questions/{qid}/answer", {"pick": f"{side} is right"})
    later = next(i for i in order if i != qid)
    _post(env, h, f"/home/questions/{later}/skip")
    home = env.client.get(h + "/home").text
    box = home.split('aria-label="Questions for you"', 1)[1]
    assert "1 answered · 1 for later" in box


def test_the_why_line_links_its_count_to_sweeps_detectors_table():
    w = hv.why_parts("Without it, 2 Sweep tests could not run last night.")
    assert w == {
        "pre": "Without it, ",
        "link": "2 Sweep tests",
        "href": "/sweep#detectors",
        "post": " could not run last night.",
    }
    assert hv.why_parts("Without it, a Sweep test could not run last night.")["link"] == (
        "a Sweep test"
    )
    assert hv.why_parts("Until you confirm it, Sweep still flags these dates.")["link"] == ""
    assert hv.why_parts("") is None


def test_opening_the_panel_never_lands_on_an_answered_question(night):  # noqa: F811
    """#54 (13 Nov 2026): with the rest answered or set aside, "Answer questions →" opens the
    first question set aside (still answerable), not the one just answered; with every question
    answered, the panel says there is nothing left."""
    env, h, cid, _rid, svc = night
    order = _order(svc, cid)
    assert len(order) >= 3
    now = dt.datetime(2026, 11, 1, 3, 0, tzinfo=dt.UTC)
    with svc.sf() as s, tenant_scope(cid):
        qs = {str(q.id): q for q in s.scalars(sa.select(Question)).all()}
        for qid in order[:-1]:  # set aside, their week not up
            qs[qid].status, qs[qid].skipped_until = "skipped", TODAY + dt.timedelta(days=7)
        last = qs[order[-1]]  # answered just now
        last.status, last.answered_at, last.answer_text = "answered", now, "Yes"
        first_text = qs[order[0]].text
        s.commit()
    page = env.client.get(h + "/home?panel=questions").text
    assert "You answered" not in page
    assert html.escape(first_text, quote=False) in page or first_text in page
    with svc.sf() as s, tenant_scope(cid):
        for q in s.scalars(
            sa.select(Question).where(Question.id.in_([uuid.UUID(x) for x in order]))
        ).all():
            q.status, q.answered_at, q.answer_text = "answered", now, "Yes"
        s.commit()
    done = env.client.get(h + "/home?panel=questions").text
    assert "That’s every question for now." in done and "You answered" not in done
