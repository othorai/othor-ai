# SPDX-License-Identifier: Apache-2.0
"""New brief, honest picks (brief_draft v4, owner 20 Nov 2026), on the steady 1 Nov fixture: the
panel says what the question asks that no number watches yet, with the way to Discover; a target
the owner typed is one click to draft as a goal through Add a goal, applied only by the owner;
Create then opens that goal beside the new brief. The model is a fake here."""

from __future__ import annotations

import html
import json
import re
from typing import Any

from othor.core.tenancy import tenant_scope
from tests.web.test_brief_web import _panel, _text
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

COIMBATORE = "Are we shipping metres out of Coimbatore on time, or are dispatch delays building up?"
DSO_Q = "Are receivables over 60 days growing, and are we on track to keep DSO under 45 days?"


def _fake(monkeypatch, draft: dict[str, Any]) -> list[str]:
    from othor.ai.gateway import ProviderReply
    from othor.web.routes import brief as brief_routes

    asked: list[str] = []

    class Fake:
        name, configured = "fake", True

        def call(self, system, messages, schema, max_tokens=0):
            props = (schema or {}).get("properties") or {}
            if "clarify" in props:  # goal_parse: the owner's words, read
                asked.append("goal_parse")
                out = {
                    "metric": "dso",
                    "target": "45 days",
                    "by": None,
                    "direction": "at_most",
                    "aggregate": "value",
                    "reply": "A goal for DSO.",
                    "clarify": None,
                }
            else:
                asked.append("brief_draft")
                out = {"name": "Brief", **draft}
            return ProviderReply(json.dumps(out), "fake", 1, 1)

    monkeypatch.setattr(brief_routes, "provider_for", lambda settings: Fake())
    return asked


def _form(page: str) -> dict[str, Any]:
    panel = _panel(page)
    data: dict[str, Any] = {}
    for name, value in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', panel):
        data.setdefault(name, []).append(html.unescape(value))
    return data


def test_not_watched_yet_names_what_no_number_measures(night, monkeypatch):  # noqa: F811
    env, h, _cid, _rid, _svc = night
    _fake(
        monkeypatch,
        {
            "question": COIMBATORE,
            "headline": ["order_book_value"],
            "missing": ["dispatch time", "late by 3 days"],
            "targets": [],
        },
    )
    tok = env.csrf(h + "/brief")
    page = env.client.post(h + "/brief/new", data={"csrf": tok, "question": COIMBATORE}).text
    text = _text(_panel(page))
    assert "Not watched yet: dispatch time" in text and "late by" not in text  # no digits
    assert "Add goal" not in text
    # the way to Discover only where Discover is (a licensed plug-in)
    from othor.web.routes import brief as brief_routes

    link = '<a class="link" href="/discover">Find it in Discover</a>'
    assert (link in page) == brief_routes._discover()
    monkeypatch.setattr(brief_routes, "_discover", lambda: True)
    page = env.client.post(h + "/brief/new", data={"csrf": tok, "question": COIMBATORE}).text
    assert link in page
    # the phrases go with the form, so a one-line change keeps them
    form = _form(page)
    assert form["missing"] == ["dispatch time"]
    again = env.client.post(h + "/brief/new", data={"csrf": tok, **form, "name": "Dispatch"}).text
    assert "Not watched yet: dispatch time" in _text(_panel(again))


def test_add_goal_drafts_it_and_create_opens_it(night, monkeypatch):  # noqa: F811
    from othor.chat import service as chat
    from othor.ontology import service

    env, h, cid, _rid, svc = night
    asked = _fake(
        monkeypatch,
        {
            "question": DSO_Q,
            "headline": ["receivables_over_60", "dso"],
            "missing": [],
            "targets": ["DSO under 45 days", "DSO under 30 days"],
        },
    )
    tok = env.csrf(h + "/brief")
    page = env.client.post(h + "/brief/new", data={"csrf": tok, "question": DSO_Q}).text
    panel = _panel(page)
    text = _text(panel)
    # only the owner's own figure: "30 days" isn't in the question
    assert "Add goal: DSO under 45 days" in text and "30 days" not in text
    assert (
        'formaction="/brief/new/goal"' in panel and 'class="btn soft"' in panel
    )  # owner, 20 Nov: soft, beside Create brief
    assert "Not watched yet" not in text
    form = _form(page)
    with svc.sf() as s, tenant_scope(cid):
        before = sorted(r.key for r in service.active(s, ["goal"]))
    r = env.client.post(
        h + "/brief/new/goal", data={"csrf": tok, **form, "target": "DSO under 45 days"}
    )
    assert asked == ["brief_draft", "goal_parse"]
    text = _text(_panel(r.text))
    assert "Goal drafted:" in text and "ready to apply after Create" in text
    assert "Add goal: DSO under 45 days" not in text
    # the brief's draft is the same draft, still on step 2
    assert "Step 2 of 2 · Review the draft" in text and DSO_Q in text
    # drafted, never applied: a goal card in the goal chat, no goal in the Business Model yet
    with svc.sf() as s, tenant_scope(cid):
        t = chat.thread(s, "ontology:goal")
        card = chat.last_set(s, t)
        assert card is not None and not card.applied
        assert sorted(r.key for r in service.active(s, ["goal"])) == before
    # a target the question doesn't hold is refused: nothing drafted
    form = _form(r.text)
    env.client.post(
        h + "/brief/new/goal", data={"csrf": tok, **form, "target": "DSO under 30 days"}
    )
    assert asked == ["brief_draft", "goal_parse"]
    # Create opens the drafted goal beside the new brief
    done = env.client.post(
        h + "/brief/new/create", data={"csrf": tok, **form}, follow_redirects=False
    )
    assert done.status_code == 303
    assert done.headers["location"].endswith("&panel=othor&subject=ontology:goal")
    listing = env.client.get(h + done.headers["location"]).text
    assert "created · first brief tomorrow" in listing and "Add a goal" in listing


def test_without_a_model_nothing_is_noted(night):  # noqa: F811
    env, h, _cid, _rid, _svc = night
    tok = env.csrf(h + "/brief")
    page = env.client.post(h + "/brief/new", data={"csrf": tok, "question": DSO_Q}).text
    text = _text(_panel(page))
    assert "Not watched yet" not in text and "Add goal" not in text
