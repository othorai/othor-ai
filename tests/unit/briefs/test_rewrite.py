# SPDX-License-Identifier: Apache-2.0
"""The prose rewrite's guard (M8 acceptance, docs/06 guard 2): no number from a model reaches the
page. v3 (0.37, #122): numbers reach the model as slots and code fills them; a rewrite with a
figure outside a slot keeps the template and its call is logged "failed:slots"; the provider off
keeps the template, so the page is the same apart from the prose source; a clean rewrite is shown
with its numbers unchanged."""

from __future__ import annotations

import json

import sqlalchemy as sa

from othor.ai.gateway import ModelGateway, ProviderReply, TemplateProvider
from othor.ai.guards import Computed
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


def _said() -> dict:
    """The template's own fields as the model sees them (numbers as slots): returned unchanged."""
    sent, _ = rewrite.slotted(rewrite.inputs_of(BODY))
    return {k: sent[k] for k in rewrite.FIELDS}


class Scripted:
    name = "fake"
    configured = True

    def __init__(self, out: dict) -> None:
        self.out = out

    def call(self, system, messages, schema, max_tokens=0):
        return ProviderReply(json.dumps(self.out), "fake-model", 10, 10)


def test_a_number_the_input_does_not_have_keeps_the_template(session_factory, two_companies):
    cid, _ = two_companies
    bad = {**_said(), "title": "South fell ₹1.5 Cr; 3 accounts."}
    got = rewrite.rewrite(ModelGateway(session_factory, Scripted(bad)), cid, BODY)
    assert got["source"] == "template" and got["guard"] == "failed:slots"
    assert rewrite.shown(BODY, got)["title"] == BODY["title"]
    with session_factory() as s, tenant_scope(cid):
        (row,) = s.scalars(sa.select(ModelCall).where(ModelCall.prompt_id == "brief_rewrite"))
        assert row.guard_result == "failed:slots"


def test_a_clean_rewrite_is_shown_with_its_numbers_unchanged(session_factory, two_companies):
    cid, _ = two_companies
    sent, _ = rewrite.slotted(rewrite.inputs_of(BODY))
    assert sent["slots"] == {
        "{n1}": "₹60 Cr",
        "{n2}": "₹1.4 Cr",
        "{n3}": "72%",
        "{n4}": "−₹0.19 Cr",
    }
    good = {
        "title": "South fell {n2} and West’s growth hid it: three accounts carry {n3} of the drop.",
        "line": BODY["line"],
        "situation": sent["situation"],
    }
    got = rewrite.rewrite(ModelGateway(session_factory, Scripted(good)), cid, BODY)
    assert got["source"] == "model" and got["prompt"] == "brief_rewrite@v3"
    assert rewrite.shown(BODY, got)["title"] == (
        "South fell ₹1.4 Cr and West’s growth hid it: three accounts carry 72% of the drop."
    )
    assert rewrite.shown(BODY, got)["situation"] == BODY["situation"]


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
        **_said(),
        "actions": [
            "Call Tessora EU and Ravira Exports this week: they carry most of South’s fall.",
            "Have sales call the 15 customers marked active who went quiet.",  # 15: not its own
        ],
    }
    got = rewrite.rewrite(ModelGateway(session_factory, Scripted(out)), cid, WITH_ACTIONS)
    assert got["source"] == "model" and got["prompt"] == "brief_rewrite@v3"
    assert got["actions_guard"] == "dropped:1"
    said = [a["said"] for a in words.actions(WITH_ACTIONS, got)]
    assert said == [out["actions"][0], WITH_ACTIONS["actions"][1]["text"]]


def test_a_wrong_count_of_action_sentences_keeps_every_template(session_factory, two_companies):
    from othor.briefs import words

    cid, _ = two_companies
    out = {**_said(), "actions": ["Only one."]}
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


def test_a_slot_from_another_sentence_drops_the_action(session_factory, two_companies):
    """#122 in the brief: an action may use only its own figures ({n5} = 14 customers is the
    second action's; the first can't borrow it)."""
    from othor.briefs import words

    cid, _ = two_companies
    out = {
        **_said(),
        "actions": [
            "Call Tessora EU and Ravira Exports: {n5} accounts carry South’s fall.",
            "Have someone call the {n5} customers marked active who went quiet for {n6} days.",
        ],
    }
    got = rewrite.rewrite(ModelGateway(session_factory, Scripted(out)), cid, WITH_ACTIONS)
    assert got["actions_guard"] == "dropped:1"
    said = [a["said"] for a in words.actions(WITH_ACTIONS, got)]
    assert said == [
        WITH_ACTIONS["actions"][0]["text"],
        "Have someone call the 14 customers marked active who went quiet for 90+ days.",
    ]


def test_a_number_in_words_keeps_the_template(session_factory, two_companies):
    cid, _ = two_companies
    bad = {**_said(), "line": "Four measures are on plan."}
    got = rewrite.rewrite(ModelGateway(session_factory, Scripted(bad)), cid, BODY)
    assert got["guard"] == "failed:slots"


# --- #120: a sentence whose direction contradicts the items keeps its template --------------------

SOUTH_FELL = Computed(signs=(("Net revenue", -1), ("South", -1), ("West", 1)), offset=True)


def test_a_wrong_direction_keeps_that_fields_template(session_factory, two_companies):
    cid, _ = two_companies
    out = {
        "title": "South grew {n2} while West’s growth hid it; three accounts carry {n3} of it.",
        "line": BODY["line"],
        "situation": (
            "Net revenue moved {n4} from September to October, as West's rise offset "
            "most of South's fall."
        ),
    }
    got = rewrite.rewrite(ModelGateway(session_factory, Scripted(out)), cid, BODY, SOUTH_FELL)
    assert got["source"] == "model" and got["meaning_guard"] == "dropped:title"
    shown = rewrite.shown(BODY, got)
    assert shown["title"] == BODY["title"]  # the template's
    # the right way, and the offset is computed; its slot filled by code
    assert shown["situation"] == (
        "Net revenue moved −₹0.19 Cr from September to October, as West's rise offset "
        "most of South's fall."
    )
    with session_factory() as s, tenant_scope(cid):
        (row,) = s.scalars(sa.select(ModelCall).where(ModelCall.prompt_id == "brief_rewrite"))
        assert row.guard_result == "dropped:direction"


def test_an_unchanged_claim_about_a_number_that_moved_keeps_the_template(
    session_factory, two_companies
):
    cid, _ = two_companies
    out = {
        **_said(),
        "line": "Net revenue was unchanged; DSO and gross margin per metre are on plan.",
    }
    got = rewrite.rewrite(ModelGateway(session_factory, Scripted(out)), cid, BODY, SOUTH_FELL)
    assert got["meaning_guard"] == "dropped:line"
    assert rewrite.shown(BODY, got)["line"] == BODY["line"]


def test_an_action_with_a_wrong_direction_keeps_its_template(session_factory, two_companies):
    from othor.briefs import words

    cid, _ = two_companies
    out = {
        **_said(),
        "actions": [
            "Call Tessora EU and Ravira Exports: South rose and they carry most of it.",
            "Have someone call the {n5} customers marked active who went quiet for {n6} days.",
        ],
    }
    got = rewrite.rewrite(
        ModelGateway(session_factory, Scripted(out)), cid, WITH_ACTIONS, SOUTH_FELL
    )
    assert got["meaning_guard"] == "dropped:action1" and got["actions_guard"] == "passed"
    said = [a["said"] for a in words.actions(WITH_ACTIONS, got)]
    assert said == [
        WITH_ACTIONS["actions"][0]["text"],
        "Have someone call the 14 customers marked active who went quiet for 90+ days.",
    ]


def test_a_right_rewrite_keeps_its_prose_bytes(session_factory, two_companies):
    cid, _ = two_companies
    good = {
        "title": "South fell {n2} and West’s growth hid it: three accounts carry {n3} of the drop.",
        "line": BODY["line"],
        "situation": _said()["situation"],
    }
    got = rewrite.rewrite(ModelGateway(session_factory, Scripted(good)), cid, BODY, SOUTH_FELL)
    assert got["source"] == "model" and "meaning_guard" not in got


def test_the_result_is_the_items_findings(session_factory, two_companies):
    """result_of: a net change hiding its parts gives the number, each part and the offset; a
    concentration's "up" is not its number's movement."""
    import datetime as dt

    from othor.engine.models import Finding, Run
    from othor.ontology.models import Spec

    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        spec = Spec(
            company_id=cid,
            workspace_id=cid,
            ontology_version=1,
            compiler_version="t",
            compiled={"metrics": {"net_revenue": {"name": "Net revenue"}}},
            hash="h" * 64,
        )
        s.add(spec)
        s.flush()
        run = Run(
            company_id=cid,
            workspace_id=cid,
            number=1,
            kind="nightly",
            run_date=dt.date(2026, 11, 1),
            spec_id=spec.id,
            image_version="t",
            started_at=dt.datetime(2026, 11, 1, 1, 0, tzinfo=dt.UTC),
            status="done",
        )
        s.add(run)
        s.flush()
        for fp, det, direction, evidence in (
            (
                "nhg",
                "net_hiding_gross",
                "down",
                [
                    {"breakdown_value": "South", "change": "-14000000"},
                    {"breakdown_value": "West", "change": "12100000"},
                ],
            ),
            ("conc", "concentration", "up", []),
        ):
            s.add(
                Finding(
                    company_id=cid,
                    workspace_id=cid,
                    run_id=run.id,
                    fingerprint=fp,
                    detector=det,
                    metric="net_revenue",
                    breakdown="region",
                    state="measured",
                    status="open",
                    change="New",
                    first_seen_run=1,
                    days_open=1,
                    body={
                        "detector": det,
                        "metric": "net_revenue",
                        "direction": direction,
                        "evidence": evidence,
                    },
                )
            )
        s.flush()
        body = {**BODY, "items": [{"fingerprint": "nhg"}, {"fingerprint": "conc"}]}
        got = rewrite.result_of(s, run.id, body)
    assert set(got.signs) == {("Net revenue", -1), ("South", -1), ("West", 1)}
    assert got.offset and not got.steady
