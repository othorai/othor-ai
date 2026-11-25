# SPDX-License-Identifier: Apache-2.0
"""#120: a model sentence can state a wrong conclusion with correct figures. Code checks its
direction and comparison words against the computed result it was written from: rises vs falls,
up vs down, higher vs lower, unchanged or offset claims, "total N" and "N ran". Only clear
contradictions are flagged, so good sentences stay."""

from __future__ import annotations

import pytest

from othor.ai.guards import Computed, card_result, contradiction, drop_contradictions

# the 24 Nov What's ahead card (gpt-oss): South short of plan, West over, nothing about the close
AHEAD = {
    "tool": "whats_ahead",
    "title": "The next weeks",
    "facts": [
        ["Week 1 · 1 Dec – 7 Dec", "At risk · Net revenue in South likely ₹1.4 Cr short of plan"],
        ["Week 2 · 8 Dec – 14 Dec", "Clear · Net revenue in West likely ₹0.6 Cr over plan"],
    ],
    "lines": [],
    "provenance": {},
    "empty": "",
}

# 25 Nov: "Three checks ran" when 3 were clean, 2 suppressed and 3 couldn't run
COVERAGE = {
    "tool": "sweep_coverage",
    "title": "Which checks ran in run #0041",
    "facts": [["Clean", "3"], ["Could not run", "3"], ["Suppressed", "2"]],
    "lines": [],
    "provenance": {},
    "empty": "",
}

NUMBERS = {
    "tool": "numbers",
    "title": "Your numbers, as of the last run",
    "facts": [
        ["Net revenue", "₹4.62 Cr · total for Oct 2026 · down 4% on Sep 2026"],
        ["Collections", "₹3.1 Cr · total for Oct 2026 · up 2% on Sep 2026"],
        ["Active customers", "212 · balance on 31 Oct · same as 30 Sep"],
    ],
    "lines": [],
    "provenance": {},
    "empty": "",
}


def test_the_offset_and_unchanged_conclusion_is_dropped() -> None:
    said = (
        "South is projected to lose ₹1.4 Cr while the West's gain will offset it, leaving the "
        "overall November close unchanged."
    )
    assert contradiction(said, card_result(AHEAD)) in ("offset", "steady")
    kept, why = drop_contradictions(
        "South looks **₹1.4 Cr** short of plan in week 1. " + said, card_result(AHEAD)
    )
    assert kept == "South looks **₹1.4 Cr** short of plan in week 1."
    assert why and why[0] in ("offset", "steady")


@pytest.mark.parametrize(
    "said",
    [
        "Three checks ran.",
        "Eight checks ran last night.",
        "A total of 5 checks were looked at.",
        "Sweep had 3 checks in total.",
    ],
)
def test_a_wrong_count_of_what_ran_or_the_total_is_dropped(said: str) -> None:
    assert contradiction(said, card_result(COVERAGE)) in ("ran", "total")


@pytest.mark.parametrize(
    "said",
    [
        "Five checks ran: three came back clean and two were held back by known patterns.",
        "Three checks ran clean, two were suppressed and three could not run.",
        "Eight checks in total; three could not run.",
        "Three checks couldn't run because a number needs a source.",
    ],
)
def test_a_right_count_stays(said: str) -> None:
    assert contradiction(said, card_result(COVERAGE)) is None


@pytest.mark.parametrize(
    ("said", "why"),
    [
        ("Net revenue rose 4% on September.", "direction"),
        ("Collections fell 2% on September.", "direction"),
        ("Net revenue was higher than last month.", "direction"),
        ("Active customers held steady, but net revenue grew.", "direction"),
    ],
)
def test_a_direction_against_the_computed_change_is_dropped(said: str, why: str) -> None:
    assert contradiction(said, card_result(NUMBERS)) == why


@pytest.mark.parametrize(
    "said",
    [
        "Net revenue fell 4% on September while collections rose 2%.",
        "Net revenue is down **4%** on September; collections are up 2%.",
        "Active customers held steady at 212.",
        "Collections did not fall: they rose 2%.",  # negation: not checked
        "Net revenue could rise again in November.",  # what may happen: not checked
        "Net revenue fell 4%, but it is still above ₹4 Cr.",  # a level, not a direction
        "Set up a goal for collections to follow up the fall in net revenue.",
        "Revenue for the month is up to you to watch; the table below has every number.",
    ],
)
def test_good_sentences_stay(said: str) -> None:
    assert contradiction(said, card_result(NUMBERS)) is None


def test_a_subject_named_inside_a_metric_name_is_not_its_direction() -> None:
    result = Computed(signs=(("Customers lost", 1),))
    assert contradiction("Customers lost went up in October.", result) is None
    assert contradiction("Customers lost dropped in October.", result) == "direction"


def test_brief_items_by_their_direction() -> None:
    """A brief's items: South fell, West rose, the net fell, and the parts offset each other."""
    result = Computed(signs=(("Net revenue", -1), ("South", -1), ("West", 1)), offset=True)
    assert contradiction("South fell ₹1.4 Cr while West's growth hid it.", result) is None
    assert contradiction("West's rise offset most of South's fall.", result) is None
    assert contradiction("South grew ₹1.4 Cr.", result) == "direction"
    assert contradiction("Net revenue was unchanged.", result) == "steady"


def test_nothing_known_nothing_flagged() -> None:
    assert contradiction("Net revenue rose 4%.", Computed()) is None
    kept, why = drop_contradictions("Net revenue rose 4%.\n\n- Collections fell.", Computed())
    assert kept == "Net revenue rose 4%.\n\n- Collections fell." and why == []
