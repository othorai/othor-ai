# SPDX-License-Identifier: Apache-2.0
"""The prose rewrite's guard (M8 acceptance, docs/06 guard 2): no number from a model reaches the
page. A rewrite with a numeral the input doesn't have keeps the template and its call is logged
"failed:numbers"; the provider off keeps the template, so the page is the same apart from the
prose source; a clean rewrite is shown with its numbers unchanged."""

from __future__ import annotations

import json

import sqlalchemy as sa

from othor.ai.gateway import ModelGateway, ProviderReply, TemplateProvider
from othor.ai.models import ModelCall
from othor.briefs import rewrite
from othor.core.tenancy import tenant_scope

BODY = {
    "question": "Are we on track for ₹60 Cr this year, and where do I need to step in?",
    "title": "South fell ₹1.4 Cr while West’s growth hid it; three accounts carry 72% of the drop.",
    "line": "DSO and gross margin per metre are on plan.",
    "situation": "Net revenue moved −₹0.19 Cr from September to October.",
    "quiet": False,
    "items": [{"title": "South fell ₹1.4 Cr; West’s rise hid it", "consequence": {"text": "x"}}],
}


class Scripted:
    name = "fake"
    configured = True

    def __init__(self, out: dict) -> None:
        self.out = out

    def call(self, system, messages, schema, max_tokens=0):
        return ProviderReply(json.dumps(self.out), "fake-model", 10, 10)


def test_a_number_the_input_does_not_have_keeps_the_template(session_factory, two_companies):
    cid, _ = two_companies
    bad = {**{k: BODY[k] for k in rewrite.FIELDS}, "title": "South fell ₹1.5 Cr; 3 accounts."}
    got = rewrite.rewrite(ModelGateway(session_factory, Scripted(bad)), cid, BODY)
    assert got["source"] == "template" and got["guard"] == "failed:numbers"
    assert rewrite.shown(BODY, got)["title"] == BODY["title"]
    with session_factory() as s, tenant_scope(cid):
        (row,) = s.scalars(sa.select(ModelCall).where(ModelCall.prompt_id == "brief_rewrite"))
        assert row.guard_result == "failed:numbers"


def test_a_clean_rewrite_is_shown_with_its_numbers_unchanged(session_factory, two_companies):
    cid, _ = two_companies
    good = {
        "title": (
            "South fell ₹1.4 Cr and West’s growth hid it: three accounts carry 72% of the drop."
        ),
        "line": BODY["line"],
        "situation": BODY["situation"],
    }
    got = rewrite.rewrite(ModelGateway(session_factory, Scripted(good)), cid, BODY)
    assert got["source"] == "model" and got["prompt"] == "brief_rewrite@v2"
    assert rewrite.shown(BODY, got)["title"] == good["title"]


def test_the_provider_off_keeps_the_template(session_factory, two_companies):
    cid, _ = two_companies
    got = rewrite.rewrite(ModelGateway(session_factory, TemplateProvider()), cid, BODY)
    assert got == {
        "source": "template",
        "template": {k: BODY[k] for k in rewrite.FIELDS},
        "guard": "template",
    }
    assert rewrite.shown(BODY, got) == {k: BODY[k] for k in rewrite.FIELDS}
    # a quiet day has nothing to rewrite
    quiet = rewrite.rewrite(
        ModelGateway(session_factory, Scripted({})), cid, {**BODY, "quiet": True}
    )
    assert quiet["guard"] == "not-needed"


# --- What to do (0.33, #110): each action sentence checked on its own ----------------------------

WITH_ACTIONS = {
    **BODY,
    "actions": [
        {"text": "Call Tessora EU and Ravira Exports: they carry most of South’s fall."},
        {"text": "Have someone call them: 14 customers marked active went quiet for 90+ days."},
    ],
}


def test_each_action_sentence_is_kept_only_with_its_own_numbers(session_factory, two_companies):
    from othor.briefs import words

    cid, _ = two_companies
    out = {
        **{k: BODY[k] for k in rewrite.FIELDS},
        "actions": [
            "Call Tessora EU and Ravira Exports this week: they carry most of South’s fall.",
            "Have sales call the 15 customers marked active who went quiet.",  # 15: not its own
        ],
    }
    got = rewrite.rewrite(ModelGateway(session_factory, Scripted(out)), cid, WITH_ACTIONS)
    assert got["source"] == "model" and got["prompt"] == "brief_rewrite@v2"
    assert got["actions_guard"] == "dropped:1"
    said = [a["said"] for a in words.actions(WITH_ACTIONS, got)]
    assert said == [out["actions"][0], WITH_ACTIONS["actions"][1]["text"]]


def test_a_wrong_count_of_action_sentences_keeps_every_template(session_factory, two_companies):
    from othor.briefs import words

    cid, _ = two_companies
    out = {**{k: BODY[k] for k in rewrite.FIELDS}, "actions": ["Only one."]}
    got = rewrite.rewrite(ModelGateway(session_factory, Scripted(out)), cid, WITH_ACTIONS)
    assert got["actions_guard"] == "dropped:2"
    assert [a["said"] for a in words.actions(WITH_ACTIONS, got)] == [
        a["text"] for a in WITH_ACTIONS["actions"]
    ]


def test_without_a_model_the_actions_are_the_templates(session_factory, two_companies):
    from othor.briefs import words

    cid, _ = two_companies
    got = rewrite.rewrite(ModelGateway(session_factory, TemplateProvider()), cid, WITH_ACTIONS)
    assert [a["said"] for a in words.actions(WITH_ACTIONS, got)] == [
        a["text"] for a in WITH_ACTIONS["actions"]
    ]
