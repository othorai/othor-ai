# SPDX-License-Identifier: Apache-2.0
"""New brief (v0.11, docs/plans/new-brief-panel.md): a draft's name comes from its question and is
never an existing brief's (the fallback copied "Leadership"); the draft's table names the headline
numbers' count, never empty (#28); the change examples come from the draft's own lists."""

from __future__ import annotations

import uuid
from typing import Any

from othor.ai.gateway import Result
from othor.briefs import drafts
from othor.web import brief_view as bvw

SPEC: dict[str, Any] = {
    "metrics": {
        "net_revenue": {"name": "Net revenue", "status": "active", "kind": "flow"},
        "dso": {"name": "DSO", "status": "active"},
        "receivables_over_60": {"name": "Receivables over 60 days", "status": "active"},
    },
    "order": ["net_revenue", "dso", "receivables_over_60"],
    "breakdowns": {"region": {"name": "Region"}},
    "owners": [{"name": "Leela Tandon", "role": "Head of Finance", "areas": ["receivables"]}],
    "forecast": {"goals": []},
}
OPTIONS = {"region": ["Export", "South", "West"]}
LEADERSHIP = {"name": "Leadership", "headline": ["net_revenue", "dso"], "question": "Old?"}
CID = uuid.uuid4()


class Gateway:
    def __init__(self, result: Result) -> None:
        self.result = result

    def complete(self, *_a: Any, **_k: Any) -> Result:
        return self.result


DOWN = Gateway(Result(None, None, "unavailable"))


def test_a_name_from_the_question() -> None:
    assert drafts.name_from("Which export customers are paying late?") == (
        "Export customers paying late"
    )
    assert drafts.name_from("How do Surat and Coimbatore compare on margin?") == (
        "Surat and Coimbatore compare"
    )
    assert drafts.name_from("Are my top 4 accounts still buying?") == "Top 4 accounts buying"
    assert drafts.name_from("?") == "New brief"


def test_a_name_an_existing_brief_has_gets_a_number() -> None:
    taken = ["Leadership", "Top 4 accounts buying"]
    assert drafts.name_from("Are my top 4 accounts still buying?", taken) == (
        "Top 4 accounts buying 2"
    )
    assert drafts.unique_name("leadership", [*taken, "Leadership 2"]) == "leadership 3"


def test_the_fallback_draft_is_named_from_the_question_not_the_default_brief() -> None:
    d, how = drafts.draft_brief(
        DOWN, CID, "Are my top 4 accounts still buying?", SPEC, OPTIONS, LEADERSHIP, ["Leadership"]
    )
    assert how != "drafted"
    assert d["name"] == "Top 4 accounts buying" and d["name"] != "Leadership"
    assert d["headline"] == ["net_revenue", "dso"]  # the default brief's numbers


def test_a_suggested_name_is_kept_unless_a_brief_has_it() -> None:
    q = "Which export customers are paying late?"
    d, _ = drafts.draft_brief(
        DOWN, CID, q, SPEC, OPTIONS, LEADERSHIP, [], name="Export receivables"
    )
    assert d["name"] == "Export receivables"
    d, _ = drafts.draft_brief(
        DOWN, CID, q, SPEC, OPTIONS, LEADERSHIP, ["Export receivables"], name="Export receivables"
    )
    assert d["name"] == "Export customers paying late"


def test_the_models_name_is_dropped_when_a_brief_has_it() -> None:
    said = Gateway(Result({"name": "Leadership", "headline": ["dso"]}, None, "passed"))
    d, how = drafts.draft_brief(
        said, CID, "Is DSO slipping?", SPEC, OPTIONS, LEADERSHIP, ["Leadership"]
    )
    assert how == "drafted" and d["name"] == "DSO slipping" and d["headline"] == ["dso"]
    said = Gateway(Result({"name": "Collections", "headline": ["dso"]}, None, "passed"))
    d, _ = drafts.draft_brief(
        said, CID, "Is DSO slipping?", SPEC, OPTIONS, LEADERSHIP, ["Leadership"]
    )
    assert d["name"] == "Collections"


def _rows(body: dict[str, Any], issue: dict[str, Any] | None = None) -> dict[str, dict[str, str]]:
    from othor.briefs import edit

    clean = edit.clean(body, SPEC, OPTIONS)
    return {r["k"]: r for r in bvw.draft_rows(SPEC, clean, OPTIONS, issue or {})}


def test_the_draft_table_rows_and_the_numbers_count_is_never_empty() -> None:
    rows = _rows({"question": "Q?", "headline": ["net_revenue", "dso", "receivables_over_60"]})
    assert list(rows) == ["Question", "Scope", "Numbers", "Ask today", "Schedule", "First sent to"]
    assert rows["Numbers"]["v"] == "Net revenue · DSO · Receivables over 60 days"
    assert rows["Numbers"]["n"] == "3 numbers"
    assert rows["Scope"]["v"] == "Whole company"
    assert rows["Schedule"]["v"] == "Every day · 6:00 AM IST"
    assert rows["First sent to"]["fill"] == ""  # recipients are added after it's created
    one = _rows({"question": "Q?", "headline": ["dso"]})
    assert one["Numbers"]["n"] == "1 number"
    none = _rows({"question": "Q?", "headline": []})
    assert none["Numbers"]["n"] == "0 numbers" and none["Numbers"]["v"]


def test_the_scope_and_ask_rows_say_what_they_mean() -> None:
    rows = _rows(
        {
            "question": "Q?",
            "headline": ["dso"],
            "scope": {"metrics": None, "exclude": {"region": ["South", "West"]}},
            "ask_owner": "Leela Tandon",
            "schedule": "weekdays",
        }
    )
    assert rows["Scope"]["v"] == "Export only" and rows["Scope"]["n"] == "leaves out South, West"
    assert rows["Ask today"]["v"] == "Leela Tandon"
    assert rows["Ask today"]["n"] == "Head of Finance, owns receivables"
    assert rows["Schedule"]["v"].startswith("Weekdays · ")
    top = _rows({"question": "Q?", "headline": ["dso"]}, {"ask": {"to": "Leela Tandon"}})
    assert top["Ask today"]["v"] == "The owner of the top item"
    assert top["Ask today"]["n"] == "today: Leela Tandon"


def test_change_examples_come_from_the_drafts_lists() -> None:
    from othor.briefs import edit

    body = edit.clean({"question": "Q?", "headline": ["net_revenue"]}, SPEC, OPTIONS)
    assert bvw.change_examples(SPEC, body, OPTIONS) == ["Only Export", "Add DSO", "Weekdays only"]
