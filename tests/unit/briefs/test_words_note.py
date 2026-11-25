# SPDX-License-Identifier: Apache-2.0
"""#124: the brief's (i) Words row says how many sentences Othor AI reworded, counted by code
from the stored template and model fields."""

from __future__ import annotations

from typing import Any

from othor.briefs.words import reworded, words_note

BODY: dict[str, Any] = {
    "title": "South fell ₹1.4 Cr",
    "line": "West's rise hid it.",
    "situation": "Revenue held at ₹4.6 Cr.",
    "actions": [{"text": "Call the South team."}, {"text": "Check 3 late invoices."}],
}
TEMPLATE = {k: BODY[k] for k in ("title", "line", "situation")}
ACTIONS = [a["text"] for a in BODY["actions"]]


def _model(**over: Any) -> dict[str, Any]:
    model: dict[str, Any] = {**TEMPLATE, "actions": list(ACTIONS), **over}
    return {"source": "model", "template": TEMPLATE, "model": model, "guard": "passed"}


def test_template_source_is_from_templates() -> None:
    prose = {"source": "template", "template": TEMPLATE, "guard": "unavailable"}
    assert reworded(BODY, prose) == (0, 5)
    assert words_note(BODY, prose) == "from templates"
    assert words_note(BODY, None) == "from templates"


def test_a_model_that_changed_nothing_is_from_templates() -> None:
    assert reworded(BODY, _model()) == (0, 5)
    assert words_note(BODY, _model()) == "from templates"


def test_some_sentences_reworded() -> None:
    prose = _model(title="The South fell by ₹1.4 Cr", actions=["Ring the South team.", ACTIONS[1]])
    assert reworded(BODY, prose) == (2, 5)
    assert words_note(BODY, prose) == (
        "2 of 5 sentences reworded by Othor AI, figures checked; the rest from templates"
    )


def test_every_sentence_reworded() -> None:
    prose = _model(
        title="The South fell by ₹1.4 Cr",
        line="A rise in the West hid it.",
        situation="Revenue stayed at ₹4.6 Cr.",
        actions=["Ring the South team.", "Look at the 3 late invoices."],
    )
    assert reworded(BODY, prose) == (5, 5)
    assert words_note(BODY, prose) == "rewritten by Othor AI, figures checked"


def test_actions_dropped_back_to_the_template_are_not_counted() -> None:
    # the actions' guard failed as a whole: the model's list is the templates
    prose = _model(title="The South fell by ₹1.4 Cr", line="A rise in the West hid it.")
    prose["model"]["situation"] = "Revenue stayed at ₹4.6 Cr."
    prose["actions_guard"] = "dropped:2"
    assert reworded(BODY, prose) == (3, 5)
    assert words_note(BODY, prose).startswith("3 of 5 sentences reworded")


def test_an_empty_field_is_not_a_sentence() -> None:
    body = {**BODY, "line": "", "actions": []}
    prose = _model(line="", title="The South fell by ₹1.4 Cr", actions=[])
    assert reworded(body, prose) == (1, 2)
