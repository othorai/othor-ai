# SPDX-License-Identifier: Apache-2.0
"""Questions for you in the one Othor AI chat (0.26, design/questions-chat/) on the steady-state
night of Sun 1 Nov 2026: every question in one message, numbered, in Home's order; skipping one
sets it aside for 7 days on Apply; the old panel's link opens the chat; with nothing left, the
chat says so. Home's Questions box still says what's left."""

from __future__ import annotations

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from othor.home import answers
from othor.ontology.models import Question
from othor.web import home_view as hv
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_in_place import _post as _post_in_place
from tests.web.test_sweep_web import night  # noqa: F401

TODAY = dt.date(2026, 11, 1)
CHAT = "/home?panel=othor&subject=questions"


def _post(env, h, path, data=None, **kw):
    kw.setdefault("follow_redirects", False)
    return _post_in_place(env, h, path, data, **kw)


def _order(svc, cid) -> list[str]:
    with svc.sf() as s, tenant_scope(cid):
        return [str(q.id) for q in answers.todays_order(s, TODAY, ZoneInfo("Asia/Kolkata"))]


def _chat(env, h) -> str:
    return env.client.get(h + CHAT).text.split('id="othor-chat"', 1)[1]


def test_every_question_in_one_message_in_homes_order(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    order = _order(svc, cid)
    assert len(order) >= 3
    with svc.sf() as s, tenant_scope(cid):
        texts = [s.get(Question, uuid.UUID(i)).text for i in order]
    page = _chat(env, h)
    import html

    at = [page.index(html.escape(t, quote=False)) for t in texts]
    assert at == sorted(at)  # Home's order
    assert f"I have {len(order)} questions" in page
    # the old panel's link (emails, history) opens the same chat
    old = env.client.get(h + "/home?panel=questions").text
    assert 'id="othor-chat"' in old and "Questions for you" in old


def test_a_skipped_question_comes_back_in_a_week(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    qid = _order(svc, cid)[0]
    _chat(env, h)
    back = {"subject": "questions", "back": CHAT}
    _post(env, h, "/othor/choose", {**back, "item": f"q:{qid}", "option": "__skip"})
    assert "Skipped" in _chat(env, h)
    _post(env, h, "/othor/apply", back)
    with svc.sf() as s, tenant_scope(cid):
        q = s.get(Question, uuid.UUID(qid))
        assert q.status == "skipped" and q.skipped_until == TODAY + dt.timedelta(days=7)
    assert "next week" in _chat(env, h)


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


def test_with_every_question_answered_the_chat_says_so(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    now = dt.datetime(2026, 11, 1, 3, 0, tzinfo=dt.UTC)
    with svc.sf() as s, tenant_scope(cid):
        for q in s.scalars(sa.select(Question)).all():
            q.status, q.answered_at, q.answer_text = "answered", now, "Yes"
        s.commit()
    page = _chat(env, h)
    assert "No questions for you right now." in page and "/othor/apply" not in page
    # #89: nothing pending, so no drafts note and no reply box
    assert "Drafts only" not in page and 'id="oc-reply"' not in page


def test_one_question_on_its_own_and_applied_alone(night):  # noqa: F811
    """0.26.2 (item 7): "More" opens one question alone, with "← Back to all questions", up to
    8 rows and its own Apply; applying it answers that one and leaves the rest as they were."""
    env, h, cid, _rid, svc = night
    page = _chat(env, h)
    import re

    m = re.search(r'href="(/home\?panel=othor&amp;subject=questions&amp;item=q:[^"]+)"', page)
    assert m, "each question has More"
    url = m.group(1).replace("&amp;", "&")
    qid = url.split("item=q:", 1)[1]
    one = env.client.get(h + url).text.split('id="othor-chat"', 1)[1]
    assert "← Back to all questions" in one and one.count('class="oc-item"') == 1
    # Apply only when this question has an answer to apply (a Needs you one has none yet)
    assert (f'name="only" value="q:{qid}"' in one) == ("My suggestion" in one)
    with svc.sf() as s, tenant_scope(cid):
        others = {str(q.id) for q in s.scalars(sa.select(Question)) if q.status == "open"} - {qid}
    _post(env, h, "/othor/apply", {"subject": "questions", "only": f"q:{qid}", "back": CHAT})
    with svc.sf() as s, tenant_scope(cid):
        q = s.get(Question, uuid.UUID(qid))
        still = {str(x.id) for x in s.scalars(sa.select(Question)) if x.status == "open"}
    if (
        q.status == "answered"
    ):  # a question with a suggestion applies; one that needs the owner doesn't
        assert others <= still
    assert "Here are the rest." in _chat(env, h) or not others
