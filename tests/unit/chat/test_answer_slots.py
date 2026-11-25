# SPDX-License-Identifier: Apache-2.0
"""#122: a right number under the wrong label must not reach the owner. The card of 1 Nov said
Clean 3 · Could not run 3 · Suppressed 2; qwen3 answered "3 were suppressed", and the numbers
guard passed it (3 is in the card). From 0.37 (ask_answer v3) the card's figures reach the model
as labelled slots; a figure written outside a slot drops its sentence, and code fills the slots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from othor import tools
from othor.ai.slots import Slots, keep_clean, problems
from othor.chat.ask import ANSWER, _words, slotted

CARD = tools.Card(
    tool="checks",
    title="Checks in run of 2026-11-01",
    facts=[("Clean", "3"), ("Could not run", "3"), ("Suppressed", "2")],
    lines=["Dispatch time spread at Coimbatore doubled from 27 Jul."],
)
Q = "How many of the checks ran last night?"


@dataclass
class _Got:
    output: dict[str, Any] | None
    ok: bool = True
    guard: str = "passed"


class _Gateway:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    def complete(self, company_id: Any, prompt: str, version: str, inputs: dict, **_k: Any):
        self.sent.append((prompt, version, inputs))
        return _Got({"answer": self.answer})


def test_the_model_sees_labelled_slots_and_no_bare_figure() -> None:
    gw = _Gateway("{n1} checks ran clean.")
    _words(gw, 1, Q, CARD)
    prompt, version, inputs = gw.sent[0]
    assert (prompt, version) == ANSWER == ("ask_answer", "v3")
    assert inputs["slots"] == {
        "{n1}": "Clean: 3",
        "{n2}": "Could not run: 3",
        "{n3}": "Suppressed: 2",
        "{n4}": "2026-11-01",
        "{n5}": "27 Jul",
    }
    assert [f["slot"] for f in inputs["card"]["facts"]] == ["{n1}", "{n2}", "{n3}"]
    # outside the slot table, the card the model reads carries no digit
    import json
    import re

    assert not re.search(r"\d", re.sub(r"\{n\d+\}", "", json.dumps(inputs["card"])))


def test_the_qwen3_case_three_suppressed_never_reaches_the_owner() -> None:
    said = _words(_Gateway("Last night **{n1}** checks ran clean. 3 were suppressed."), 1, Q, CARD)
    assert "3 were suppressed" not in said
    assert said == "Last night **3** checks ran clean."


def test_a_number_in_words_outside_a_slot_drops_its_sentence() -> None:
    said = _words(_Gateway("Three were suppressed. {n3} were suppressed."), 1, Q, CARD)
    assert said == "2 were suppressed."


def test_slots_are_filled_from_the_card_under_their_own_label() -> None:
    said = _words(_Gateway("{n3} checks were suppressed and {n2} could not run."), 1, Q, CARD)
    assert said == "2 checks were suppressed and 3 could not run."


def test_a_made_up_slot_drops_its_sentence() -> None:
    assert _words(_Gateway("{n9} checks failed."), 1, Q, CARD) == ""


def test_a_figure_the_owner_typed_may_be_said_back() -> None:
    card = tools.Card(tool="top", title="Top customers", facts=[("Skarfab", "₹1.2 Cr")])
    said = _words(_Gateway("Your top 5 start with Skarfab at {n1}."), 1, "my top 5 customers", card)
    assert said == "Your top 5 start with Skarfab at ₹1.2 Cr."


def test_the_numbers_guard_stays_as_a_second_check() -> None:
    slots = Slots()
    slots.new("₹9 Cr", "Made up: ₹9 Cr")  # a slot whose value the card doesn't hold
    kept, dropped = keep_clean("It reached {n1}.", slots, grounded_in={"card": "₹1 Cr"})
    assert (kept, dropped) == ("", 1)


def test_lists_and_paragraphs_keep_their_shape() -> None:
    text = "Most checks ran.\n\n- {n1} clean\n- 3 suppressed\n- {n3} suppressed"
    _inputs, slots = slotted(Q, CARD)
    kept, dropped = keep_clean(text, slots, grounded_in=CARD.as_json())
    assert kept == "Most checks ran.\n\n- 3 clean\n- 2 suppressed" and dropped == 1


def test_problems_names_each_rule() -> None:
    s = Slots()
    s.new("2", "Suppressed: 2")
    assert problems("{n1} suppressed.", s) == []
    assert problems("{n2} suppressed.", s) == ["unknown-slot"]
    assert problems("3 suppressed.", s) == ["digit"]
    assert problems("Three suppressed.", s) == ["number-word"]
    assert problems("One of them was suppressed.", s) == []  # "one" alone isn't a figure
