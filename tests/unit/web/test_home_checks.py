# SPDX-License-Identifier: Apache-2.0
"""Home's checks strip (owner's design, 11 Nov 2026): how many of the shipped checks ran in the
last run, and why the rest didn't, from the run's coverage. The strip's state picks its line and
its one button."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from othor.web import home_view, sweep_view


def _rv(statuses: dict[str, tuple[str, str | None]], findings: int = 0) -> Any:
    cov = [{"detector": d, "status": st, "needs": needs} for d, (st, needs) in statuses.items()]
    return SimpleNamespace(
        doc={"coverage": cov},
        counts={"findings": findings},
        run=SimpleNamespace(kind="nightly"),
    )


def _all(status: str = "ran", needs: str | None = None) -> dict[str, tuple[str, str | None]]:
    return {d: (status, needs) for d in sweep_view.shipped()}


def test_every_check_ran_on_a_quiet_night() -> None:
    n = len(sweep_view.shipped())
    s = home_view.checks_strip(_rv(_all()), questions=0)
    assert (s["ran"], s["total"], s["state"]) == (n, n, "all_quiet")
    assert s["lead"] == "At 100% Intelligence." and s["pct"] == 100
    assert s["tail"] == "Nothing needs you today."
    assert s["action"] == ("Open brief", "/brief")
    assert s["segments"] == ["ran"] * n


def test_a_first_week_says_what_the_rest_are_waiting_for() -> None:
    names = sweep_view.shipped()
    st = _all("could_not_run", "entity")
    st[names[0]] = ("ran", None)
    st[names[1]] = ("partly", None)
    st[names[2]] = ("could_not_run", "forecast")
    st[names[3]] = ("could_not_run", None)
    s = home_view.checks_strip(_rv(st), questions=5)
    assert (s["ran"], s["history"], s["cannot"]) == (2, 1, 1)
    assert s["waiting"] == len(names) - 4
    pct = round(100 * 2 / len(names))
    assert s["lead"] == f"At {pct}% Intelligence."
    reach = round(100 * (2 + s["waiting"]) / len(names))
    assert s["tail"] == f"Reach {reach}% with 5 quick answers."
    assert s["action"] == ("Answer 5 questions", "/home?panel=othor&subject=questions")
    # the info box: what the percentage means, then the four groups with their checks
    assert s["explain"] == (
        f"{pct}% Intelligence means 2 of the {len(names)} checks Sweep runs every night could "
        "run on your data last night."
    )
    groups = {g["key"]: g for g in s["groups"]}
    assert [g["key"] for g in s["groups"]] == ["ran", "waiting", "history", "cannot"]
    assert (
        groups["waiting"]["n"] == s["waiting"] and len(groups["waiting"]["checks"]) == s["waiting"]
    )
    assert groups["waiting"]["text"] == "Turn on when you answer 5 questions about your data."
    assert all(need for _name, need in groups["waiting"]["checks"])  # each says what it needs
    assert groups["ran"]["checks"][0][1] == ""
    assert s["segments"][:2] == ["ran", "ran"] and s["segments"][-2:] == ["history", "cannot"]


def test_waiting_with_nothing_to_answer_points_to_the_ontology() -> None:
    """Owner, 12 Nov 2026: while checks wait, the button updates the Ontology (questions, then
    proposals, then the Ontology page); Open Sweep only once every check ran."""
    names = sweep_view.shipped()
    st = _all()
    st[names[0]] = ("could_not_run", "pair")
    s = home_view.checks_strip(_rv(st), questions=0)
    assert s["action"] == ("Update the Business Model", "/ontology") and s["state"] == "partial"
    s = home_view.checks_strip(_rv(st), questions=0, proposals=2)
    assert s["action"] == ("Review proposals", "/ontology#proposals") and "2 proposals" in s["tail"]
    s = home_view.checks_strip(_rv(st), questions=3, proposals=2)
    assert s["action"] == ("Answer 3 questions", "/home?panel=othor&subject=questions")


def test_every_check_ran_and_found_something() -> None:
    s = home_view.checks_strip(_rv(_all(), findings=3), questions=0)
    assert s["state"] == "all_found" and s["tail"] == "3 things found."
    assert s["action"] == ("Open Sweep", "/sweep")


def test_set_aside_questions_still_lead_the_strip() -> None:
    """Owner, 12 Nov 2026: with every question set aside (Ask me later), the strip fell back to
    Open Sweep though 7 checks were still waiting on those answers."""
    names = sweep_view.shipped()
    st = _all()
    st[names[0]] = ("could_not_run", "pair")
    s = home_view.checks_strip(_rv(st), questions=0, proposals=2, later=4)
    assert s["action"] == ("Answer questions", "/home?panel=othor&subject=questions")
    assert s["tail"].startswith("You set aside 4 questions")


def test_an_open_split_proposal_leads_the_strip() -> None:
    """v0.11 (12 Nov 2026): a quiet first night came from numbers with no breakdowns; while the
    "Split your numbers by …" proposal is open, the strip points at it before questions."""
    names = sweep_view.shipped()
    st = _all("could_not_run", None)
    st[names[0]] = ("ran", None)
    s = home_view.checks_strip(
        _rv(st), questions=3, proposals=1, splits="Split your numbers by Region and Customer"
    )
    assert s["action"] == ("Review the split", "/ontology#proposals")
    assert s["tail"] == "Split your numbers by Region and Customer so more checks can run."
    s = home_view.checks_strip(_rv(_all()), questions=0, splits="Split your numbers by Region")
    assert s["action"] == ("Open brief", "/brief")  # every check ran: the split isn't pushed


def test_only_history_left_says_it_turns_on_by_itself() -> None:
    names = sweep_view.shipped()
    st = _all()
    st[names[0]] = ("could_not_run", "forecast")
    s = home_view.checks_strip(_rv(st), questions=0)
    assert s["tail"] == "1 more turns on as your history grows."
    assert s["action"] == ("Update the Business Model", "/ontology")


def test_an_answered_question_leaves_nothing_waiting_on_you() -> None:
    """0.27.2 (indigo2, 19 Nov 2026): lead and lag needed a pair; its one question was answered
    and it still couldn't run, yet "Waiting on you · 1" showed with no question to answer."""
    st = _all()
    pair = next(iter(st))
    st[pair] = ("could_not_run", "pair")
    asked = home_view.checks_strip(_rv(st), questions=1, pending={"pair"})
    assert asked["waiting"] == 1
    answered = home_view.checks_strip(_rv(st), questions=0, pending=set())
    assert answered["waiting"] == 0 and answered["cannot"] == 1
