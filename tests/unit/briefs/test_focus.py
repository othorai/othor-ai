# SPDX-License-Identifier: Apache-2.0
"""A brief with picked numbers shows items on its own numbers first (Option A, owner 20 Nov
2026; indigo2's briefs all showed the same items): the picked numbers, the numbers they're
computed from and the numbers computed from them. The default brief keeps the whole company. A
focused brief with nothing new says so in one line, with the default brief's count from code.
"Still open" and "Since last week" follow the same focus."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from othor.briefs import scope, since_week, step
from othor.briefs.assemble import assemble
from othor.engine.store import Point
from othor.ontology.compiler.api import canonical

RUN = dt.date(2026, 11, 1)


def _m(name: str, **kw: Any) -> dict[str, Any]:
    return {
        "name": name,
        "unit": "inr",
        "grain": "month",
        "kind": "flow",
        "status": "active",
        **kw,
    }


SPEC: dict[str, Any] = {
    "metrics": {
        "gross_revenue": _m("Gross revenue"),
        "returns": _m("Returns"),
        # computed from gross revenue and returns
        "net_revenue": _m("Net revenue", components=["gross_revenue", "returns"]),
        "freight": _m("Freight"),
        "dso": _m("DSO", unit="days"),
    },
    "forecast": {"goals": []},
    "detectors": {"materiality_inr": "500000"},
}


def _f(fp: str, metric: str | None, money: str = "900000", **kw: Any) -> dict[str, Any]:
    return {
        "fingerprint": fp,
        "status": "open",
        "detector": "changepoint",
        "metric": metric,
        "family": metric,
        "state": "measured",
        "confidence": None,
        "money": money,
        "change": "New",
        "days_open": 1,
        "first_seen": {"date": "2026-11-01"},
        "slots": {},
        "text": {"title": f"{metric} {fp}"},
        "window": ["2026-09-01", "2026-10-31"],
        "entities": [],
        "breakdown": "",
        "breakdown_value": None,
        "series": str(metric),
        **kw,
    }


def _points() -> list[Point]:
    out, d = [], dt.date(2025, 11, 1)
    for _ in range(12):
        for k in SPEC["metrics"]:
            out.append(Point(k, "month", "", None, d, Decimal("100")))
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


def _v(name: str, headline: list[str], **kw: Any) -> dict[str, Any]:
    return {
        "id": name.lower(),
        "number": 1,
        "name": name,
        "question": "?",
        "scope": {"metrics": None, "exclude": {}},
        "headline": headline,
        "ask_owner": "top_item",
        "goals": [],
        "time": "06:00",
        **kw,
    }


def _issue(version: dict[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
    return assemble(
        SPEC,
        version,
        run_date=RUN,
        findings=[dict(f) for f in findings],
        watches=[],
        goals=[],
        record=[],
        points=_points(),
        sweep_counts={"findings": len(findings)},
        tests=212,
    )


FOUND = [
    _f("fr", "freight", "2000000"),  # off-topic for a revenue brief
    _f("gr", "gross_revenue", "900000"),  # net revenue is computed from it
    _f("nr", "net_revenue", "800000"),  # the picked number itself
    _f("co", None, "700000"),  # company-wide (no metric)
]


def test_focus_is_the_picked_numbers_and_what_they_are_computed_from() -> None:
    assert scope.focus(SPEC, _v("Revenue", ["net_revenue"])) == [
        "gross_revenue",
        "net_revenue",
        "returns",
    ]
    # not focused: the default brief, no picked numbers, or scoped another way
    assert scope.focus(SPEC, _v("Leadership", ["net_revenue"], default=True)) is None
    assert scope.focus(SPEC, _v("All", [])) is None
    scoped = _v("Areas", ["net_revenue"], scope={"metrics": None, "areas": ["Sales"]})
    assert scope.focus(SPEC, scoped) is None


def test_a_focused_brief_keeps_its_numbers_and_leaves_out_the_rest() -> None:
    got = _issue(_v("Revenue", ["net_revenue"]), FOUND)
    assert {i["fingerprint"] for i in got["items"]} == {"gr", "nr"}
    assert "focus_note" not in got
    # a brief on gross revenue keeps net revenue's finding too: computed from it (and gross
    # revenue's own finding leads)
    up = _issue(_v("Gross", ["gross_revenue"]), FOUND)
    assert [i["fingerprint"] for i in up["items"]] == ["gr", "nr"]
    # the counts say the same thing: freight and the company-wide finding are in Sweep
    assert got["since"]["new"] == 2 and got["more_in_sweep"] == 2


def test_its_own_numbers_lead_and_parts_fill_the_rest() -> None:
    """Owner, 20 Nov 2026: a focused brief's own numbers come first; a finding on a part (gross
    revenue under net revenue) only after them, even when its money is larger."""
    got = _issue(_v("Revenue", ["net_revenue"]), FOUND)
    assert [i["fingerprint"] for i in got["items"]] == ["nr", "gr"]  # gr's ₹9 L > nr's ₹8 L
    # one level: a part's own parts aren't the brief's
    assert scope.focus(SPEC, _v("Revenue", ["net_revenue"])) == sorted(
        {"net_revenue", "gross_revenue", "returns"}
    )


def test_the_default_brief_is_unchanged() -> None:
    lead = _v("Leadership", ["net_revenue"], default=True)
    got = _issue(lead, FOUND)
    assert {i["fingerprint"] for i in got["items"]} == {"fr", "gr", "nr", "co"}
    # the same issue as before focus existed: a brief with no picked numbers keeps all too
    assert {i["fingerprint"] for i in _issue(_v("All", []), FOUND)["items"]} == {
        "fr",
        "gr",
        "nr",
        "co",
    }
    assert canonical(got) == canonical(_issue(lead, FOUND))


def test_a_pinned_finding_stays_in_a_focused_brief() -> None:
    from othor.engine.findings import pattern_of

    fr = FOUND[0]
    v = _v("Revenue", ["net_revenue"], pinned=[pattern_of(fr)])
    got = _issue(v, [fr, *FOUND[1:]])
    assert "fr" in {i["fingerprint"] for i in got["items"]}


def _make(briefs: list[dict[str, Any]], findings: list[dict[str, Any]], **kw: Any):
    return step.make(
        SPEC,
        RUN,
        {"briefs": briefs},
        findings,
        [],
        {},
        _points(),
        {"sweep": {"counts": {"findings": len(findings)}, "tests": 212}},
        **kw,
    )


def test_a_focused_brief_with_nothing_new_says_where_the_rest_is() -> None:
    lead = _v("Leadership", ["net_revenue"], default=True)
    dso = _v("Collections", ["dso"])
    doc = _make([lead, dso], FOUND)
    by = {i["brief_id"]: i["issue"] for i in doc["issues"]}
    assert by["collections"]["quiet"] and not by["collections"]["items"]
    n = len(by["leadership"]["items"])
    assert n == 4
    assert by["collections"]["focus_note"] == "Nothing new on Collections · Leadership has 4 items"
    assert "focus_note" not in by["leadership"]
    # one item: the singular
    doc = _make([lead, dso], [FOUND[1]])
    by = {i["brief_id"]: i["issue"] for i in doc["issues"]}
    assert by["collections"]["focus_note"] == "Nothing new on Collections · Leadership has 1 item"
    # nothing anywhere: the brief's own line only
    doc = _make([lead, dso], [])
    by = {i["brief_id"]: i["issue"] for i in doc["issues"]}
    assert by["collections"]["focus_note"] == "Nothing new on Collections"
    # the same inputs give the same bytes
    assert canonical(_make([lead, dso], FOUND)) == canonical(_make([lead, dso], FOUND))


def test_without_the_default_brief_the_count_is_the_unscoped_ranking() -> None:
    """A preview of one brief, or a night whose default brief isn't made (Weekly)."""
    dso = _v("Collections", ["dso"])
    doc = _make([dso], FOUND)
    note = doc["issues"][0]["issue"]["focus_note"]
    assert note == "Nothing new on Collections · Leadership has 4 items"
    ref = _v("Board", [], default=True)
    doc = _make([dso], FOUND, reference=ref)
    assert doc["issues"][0]["issue"]["focus_note"] == (
        "Nothing new on Collections · Board has 4 items"
    )


def test_still_open_and_since_last_week_follow_the_focus() -> None:
    old = {"change": "Open · day 12", "days_open": 12, "first_seen": {"date": "2026-10-20"}}
    found = [_f("fr", "freight", **old), _f("nr", "net_revenue", **old)]
    got = _issue(_v("Revenue", ["net_revenue"]), found)
    assert [x["fingerprint"] for x in got["still_open"]] == ["nr"]
    week = since_week.build(
        SPEC, _v("Revenue", ["net_revenue"]), RUN, found, {"nights": [], "meta": {}}, []
    )
    assert week is not None and [x["fingerprint"] for x in week["open"]] == ["nr"]
