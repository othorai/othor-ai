# SPDX-License-Identifier: Apache-2.0
"""Ask v0.1 (0.26, M26.6) on the sample night: a question is routed to one read-only tool, the
tool runs through the one gate (schema, role, audit event), the card's figures are code's, and a
worded answer with a figure the card doesn't hold is not shown. Without a model, the question's
words find the tool. A change request points to Tell Othor AI."""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from othor import tools
from othor.ai.gateway import ProviderReply
from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_in_place import _post
from tests.web.test_sweep_web import night  # noqa: F401

BACK = "/home?panel=othor&subject=ask"


def _fake(monkeypatch, outs):
    from othor.web.routes import othor_chat as routes

    it = iter(outs)

    class Fake:
        name, configured = "fake", True

        def call(self, system, messages, schema, max_tokens=0):
            return ProviderReply(json.dumps(next(it)), "fake", 1, 1)

    monkeypatch.setattr(routes, "provider_for", lambda settings: Fake())


def _events(svc, cid):
    with svc.sf() as s, tenant_scope(cid):
        return list(s.scalars(sa.select(Event).where(Event.action == "tool.called")))


def test_a_question_is_answered_from_a_tools_card(night, monkeypatch):  # noqa: F811
    env, h, cid, _rid, svc = night
    page = env.client.get(h + BACK).text
    assert "Ask me about your numbers" in page
    _fake(
        monkeypatch,
        [
            {"tool": "findings", "args_json": '{"limit": 3}', "change": False, "reply": ""},
            {"answer": "Sweep found 999 problems."},  # a figure the card doesn't hold
        ],
    )
    _post(env, h, "/othor/reply", {"subject": "ask", "text": "what's wrong?", "back": BACK})
    page = env.client.get(h + BACK).text.split('id="othor-chat"', 1)[1]
    assert "What Sweep found in run #" in page and "How this was worked out" in page
    assert "999 problems" not in page and "what I found." in page  # not "999": hashes vary
    (ev,) = _events(svc, cid)
    assert ev.after["tool"] == "findings" and ev.after["args"] == {"limit": 3}
    assert ev.after["via"] == "chat" and ev.after["run"]


def test_without_a_model_the_words_find_the_tool(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    _post(env, h, "/othor/reply", {"subject": "ask", "text": "show my numbers", "back": BACK})
    page = env.client.get(h + BACK).text
    assert "Your numbers, as of the last run" in page
    assert [e.after["tool"] for e in _events(svc, cid)] == ["numbers"]


def test_a_change_request_is_drafted_in_the_business_model_chat(night, monkeypatch):  # noqa: F811
    """Context-aware (owner, 18 Nov 2026): a change asked for in Ask is drafted at once in the
    Business Model chat; nothing is applied; no read tool is called."""
    env, h, cid, _rid, svc = night
    change = {
        "op": "put",
        "type": "business_fact",
        "key": "kochi_invoicing",
        "body_json": '{"text": "Kochi plant starts invoicing in December", "kind": "other"}',
    }
    _fake(
        monkeypatch,
        [
            {"tool": None, "args_json": "{}", "change": True, "reply": ""},
            {"changes": [change], "reply": "Noted.", "clarify": None},
        ],
    )
    _post(
        env,
        h,
        "/othor/reply",
        {"subject": "ask", "text": "Kochi invoices from December", "back": BACK},
    )
    page = env.client.get(h + BACK).text
    assert "drafted that as a change" in page and "Review the change" in page
    assert not _events(svc, cid)
    draft = env.client.get(h + "/ontology?panel=othor&subject=ontology").text
    assert "Kochi plant starts invoicing in December" in draft and "Apply 1 change" in draft


def test_the_gate_refuses_before_anything_runs(night):  # noqa: F811
    _env, _h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        from othor.core.models import Company

        ctx = tools.Ctx(s, svc.settings, s.get(Company, cid), None)
        for name, args, role in (
            ("nope", {}, "admin"),
            ("numbers", {"limit": "all"}, "admin"),
            ("numbers", {"limit": 500}, "admin"),
            ("numbers", {"sql": "select 1"}, "admin"),
            ("metric", {}, "admin"),
            ("numbers", {}, "viewer"),
        ):
            with pytest.raises(tools.Refused):
                tools.call(ctx, name, args, actor="t", role=role)
        s.commit()
    assert not _events(svc, cid)
    names = [t["name"] for t in tools.listing()]
    assert names == sorted(names) and {"numbers", "metric", "findings"} <= set(names)
    for t in tools.listing():  # every schema closes its arguments (nothing else passes)
        assert t["args_schema"].get("additionalProperties") is False, t["name"]


def test_the_four_home_tools_answer_from_homes_own_view(night):  # noqa: F811
    """0.26.2 (items 1–4): today's brief, System Health, Intelligence and what's ahead, each read
    from the view Home shows (so they never disagree), picked by the question's words."""
    env, h, cid, _rid, svc = night
    home = env.client.get(h + "/home").text
    asks = {
        "what does today's brief say?": ("brief", "Today's briefs"),
        "how healthy is everything?": ("system_health", "System Health"),
        "how much intelligence do we have?": ("intelligence", "How much Sweep can check"),
        "what is coming up next week?": ("whats_ahead", "The next weeks"),
    }
    for q, (_tool, title) in asks.items():
        _post(env, h, "/othor/reply", {"subject": "ask", "text": q, "back": BACK})
        import html

        page = html.unescape(env.client.get(h + BACK).text)
        assert title in page, q
    assert [e.after["tool"] for e in _events(svc, cid)] == [t for t, _ in asks.values()]
    page = env.client.get(h + BACK).text
    # the Intelligence card says the same share as the top bar's ring
    import re

    pct = re.search(r'aria-label="Intelligence at (\d+)%"', home).group(1)
    assert f"<dd>{pct}%</dd>" in page


def test_big_picture_and_numbers_tools_say_what_each_number_is(night):  # noqa: F811
    """0.27.3: the Big Picture report as stored (no model call), and numbers that say whether
    they're a total for a period or a balance on a day."""
    import datetime as dt

    from othor.briefs.models import BigPicture
    from othor.core.models import Company

    _env, _h, cid, rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        company = s.get(Company, cid)
        ctx = tools.Ctx(s, svc.settings, company, dt.date(2026, 11, 1))
        empty = tools.call(ctx, "big_picture", {}, actor="t")
        assert "No Big Picture report yet" in empty.empty
        s.add(
            BigPicture(
                company_id=cid,
                workspace_id=cid,
                run_id=rid,
                language="en",
                body={
                    "one_line": [{"t": "Sales held up; cash is slower."}],
                    "summary": [[{"t": "Net revenue rose "}, {"t": "3%", "href": "#k"}]],
                    "kpis": [{"label": "Net revenue", "value": "₹4.77 Cr"}],
                    "findings": [],
                    "stakes": [],
                    "look_first": [{"text": [{"t": "Overdue in the South"}]}],
                },
                prompt="big_picture@v1",
                input_hash="x",
                guard="passed",
            )
        )
        s.flush()
        card = tools.call(ctx, "big_picture", {}, actor="t")
        assert card.title.startswith("Big Picture for night run #")
        assert card.facts == [("Net revenue", "₹4.77 Cr")]
        assert card.lines[:2] == ["Sales held up; cash is slower.", "Net revenue rose 3%"]
        assert "Look first: Overdue in the South" in card.lines
        nums = tools.call(ctx, "numbers", {"limit": 40}, actor="t")
        words = " ".join(v for _k, v in nums.facts)
        assert "total for" in words and ("balance on" in words or "balance at" in words)
        s.rollback()
