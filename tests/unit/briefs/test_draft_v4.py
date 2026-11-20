# SPDX-License-Identifier: Apache-2.0
"""brief_draft v4 (owner, 20 Nov 2026): only the numbers that answer the question (indigo2's
Coimbatore dispatch question got four unrelated numbers to fill 6), what the question asks that
no number measures (`missing`, never a digit), and the targets the owner stated (`targets`, kept
only with the owner's own figure). Without a model the picks are as before and nothing is noted."""

from __future__ import annotations

from typing import Any

from othor.ai.gateway import Result
from othor.briefs import drafts
from tests.unit.briefs.test_new_brief import CID, DOWN, LEADERSHIP, OPTIONS, SPEC, Gateway

COIMBATORE = "Are we shipping metres out of Coimbatore on time, or are dispatch delays building up?"
DSO_Q = "Are receivables over 60 days growing, and are we on track to keep DSO under 45 days?"


def _said(**out: Any) -> Gateway:
    return Gateway(Result({"name": "Brief", "question": "", **out}, None, "passed"))


def test_v4_keeps_only_the_numbers_that_answer_and_never_pads() -> None:
    said = _said(
        question=COIMBATORE,
        headline=["net_revenue"],
        missing=["dispatch time", "on-time shipments"],
        targets=[],
    )
    d, how = drafts.draft_brief(said, CID, COIMBATORE, SPEC, OPTIONS, LEADERSHIP, ["Leadership"])
    assert how == "drafted"
    assert d["headline"] == ["net_revenue"]  # one is fine: not padded with the default's
    assert d["missing"] == ["dispatch time", "on-time shipments"] and d["targets"] == []


def test_a_missing_phrase_with_a_digit_is_dropped() -> None:
    assert drafts.missing(["dispatch time", "receivables over 60 days", " Dispatch time. "]) == [
        "dispatch time"
    ]
    assert drafts.missing(["a", "b", "c", "d"]) == ["a", "b", "c"]  # up to 3
    assert drafts.missing(None) == []


def test_a_target_is_kept_only_with_the_owners_own_figure() -> None:
    assert drafts.targets(["DSO under 45 days"], DSO_Q) == ["DSO under 45 days"]
    assert drafts.targets(["DSO under 40 days"], DSO_Q) == []  # not the owner's figure
    assert drafts.targets(["keep DSO low"], DSO_Q) == []  # no figure: no goal to draft
    assert drafts.targets(["Net revenue at least 1,20 Cr"], "Can we reach 120 Cr?") == [
        "Net revenue at least 1,20 Cr"
    ]
    said = _said(
        question=DSO_Q,
        headline=["receivables_over_60", "dso"],
        missing=[],
        targets=["DSO under 45 days", "receivables under 2 Cr"],
    )
    d, _ = drafts.draft_brief(said, CID, DSO_Q, SPEC, OPTIONS, LEADERSHIP, ["Leadership"])
    assert d["targets"] == ["DSO under 45 days"]
    assert d["headline"] == ["receivables_over_60", "dso"]


def test_without_a_model_the_picks_are_as_before_and_nothing_is_noted() -> None:
    d, how = drafts.draft_brief(DOWN, CID, DSO_Q, SPEC, OPTIONS, LEADERSHIP, ["Leadership"])
    assert how != "drafted" and d["headline"] == ["net_revenue", "dso"]
    assert d["missing"] == [] and d["targets"] == []


def test_the_notes_never_reach_the_saved_brief() -> None:
    from othor.briefs import edit

    body = edit.clean(
        {"question": "Q?", "headline": ["dso"], "missing": ["x"], "targets": ["DSO under 4"]},
        SPEC,
        OPTIONS,
    )
    assert "missing" not in body and "targets" not in body
