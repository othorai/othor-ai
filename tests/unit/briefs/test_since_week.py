# SPDX-License-Identifier: Apache-2.0
"""0.28.5 · "Since last week" (othor/briefs/since_week.py): what the brief reported over the
week, what got fixed and how (in plain words), what is still open and whether it got better or
worse, the open items night by night, and at most one suggestion. Pure and deterministic."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from othor.briefs import since_week as sw
from othor.charts.spec import ChartSpec

D = dt.date(2026, 11, 25)
SPEC: dict[str, Any] = {"metrics": {"net_revenue": {}, "dso": {}}}
BRIEF: dict[str, Any] = {"id": "b1", "scope": {}}


def f(fp: str, status: str = "open", **kw: Any) -> dict[str, Any]:
    first = kw.pop("first", "2026-11-10")
    return {
        "fingerprint": fp,
        "status": status,
        "detector": kw.pop("detector", "changepoint"),
        "metric": kw.pop("metric", "net_revenue"),
        "breakdown": "",
        "breakdown_value": None,
        "entities": [],
        "state": kw.pop("state", "assessed"),
        "money": kw.pop("money", "100000"),
        "change": kw.pop("change", "Open · day 16"),
        "first_seen": {"date": first},
        "days_open": kw.pop("days", 16),
        "text": {"title": kw.pop("title", fp.upper())},
        **kw,
    }


def night(day: str, *rows: list[Any]) -> dict[str, Any]:
    return {"date": day, "rows": [list(r) for r in rows]}


def meta(fp: str, first: str = "2026-11-10", money: str = "100000", **kw: Any) -> dict[str, Any]:
    return {
        "detector": kw.get("detector", "changepoint"),
        "metric": kw.get("metric", "net_revenue"),
        "breakdown": "",
        "breakdown_value": None,
        "entities": [],
        "state": "assessed",
        "title": kw.get("title", fp.upper()),
        "first_seen": first,
        "first_money": money,
    }


def test_fixed_and_how_in_plain_words() -> None:
    tonight = [
        f("a", "resolved", resolved={"date": "2026-11-25", "reason": "Cleared by data"}),
        f(
            "b",
            "resolved",
            detector="cross_source",
            resolved={"date": "2026-11-23", "reason": "Cleared by data · within tolerance"},
        ),
        f("c", "covered", covered={"date": "2026-11-22", "by": "Diwali week"}),
        f("d", "snoozed", snoozed_until="2026-12-01"),
        f(
            "e",
            "resolved",
            resolved={"date": "2026-11-21", "reason": "Explained by your answer (Skarfab's pause)"},
        ),
    ]
    hist = {
        "nights": [
            night(
                "2026-11-20", *[[x, "open", "100000", "Open · day 10", None, None] for x in "abcde"]
            )
        ],
        "meta": {x: meta(x) for x in "abcde"},
    }
    got = sw.build(SPEC, BRIEF, D, tonight, hist, [])
    assert got is not None
    hows = {x["fingerprint"]: x["how"] for x in got["fixed"]}
    assert hows == {
        "a": "Back to normal on 25 Nov",
        "b": "The two sources agree again on 23 Nov",
        "c": "Became a known pattern on 22 Nov: Diwali week",
        "d": "You set it aside until 1 Dec",
        "e": "Explained by your answer on 21 Nov",
    }
    assert [x["fingerprint"] for x in got["fixed"]][:2] == ["a", "b"]  # newest first
    assert got["counts"]["fixed"] == 5


def test_an_item_closed_before_the_week_is_not_counted() -> None:
    tonight = [f("old", "resolved", resolved={"date": "2026-11-16", "reason": "Cleared by data"})]
    hist = {
        "nights": [
            night("2026-11-15", ["old", "open", "1", "Open · day 3", None, None]),
            night(
                "2026-11-16", ["old", "resolved", "1", "Resolved", "Cleared by data", "2026-11-16"]
            ),
        ],
        "meta": {"old": meta("old", first="2026-11-13")},
    }
    assert sw.build(SPEC, BRIEF, D, tonight, hist, []) is None


def test_still_open_days_and_better_or_worse_and_shown_items_left_out() -> None:
    tonight = [
        f("up", money="150000", days=16),
        f("down", money="50000", days=7, first="2026-11-19"),
        f("flat", money="104000", days=12, first="2026-11-14"),
        f("top", days=20, first="2026-11-06"),
        f("new", change="New", days=1, first="2026-11-25"),
        # a contradiction that needs confirmation is a Home question: in no count, list or chart
        f("ask", state="needs_confirmation", days=30, first="2026-10-27"),
    ]
    hist = {
        "nights": [],
        "meta": {
            "up": meta("up"),
            "down": meta("down", first="2026-11-19"),
            "flat": meta("flat", first="2026-11-14"),
            "top": meta("top", first="2026-11-06"),
        },
    }
    got = sw.build(SPEC, BRIEF, D, tonight, hist, ["top"])
    assert got is not None
    assert [(x["fingerprint"], x["days"], x["trend"]) for x in got["open"]] == [
        ("up", 16, "worse"),
        ("flat", 12, "about the same"),
        ("down", 7, "better"),
        ("new", 1, "about the same"),
    ]
    # down (19 Nov) and new reported; "top" is one of today's items, so the line names it and the
    # list leaves it out (owner, 20 Nov 2026: the line and the list agree)
    assert got["counts"] == {"reported": 2, "fixed": 0, "open": 5, "in_items": 1}
    assert got["counts"]["open"] == got["counts"]["in_items"] + len(got["open"])
    assert got["longest"] == {"fingerprint": "top", "title": "TOP", "days": 20}
    assert got["line"] == (
        "Last 7 days: 2 reported · 0 fixed · 5 still open (1 in today’s items) · "
        "longest open: TOP, day 20"
    )
    none_shown = sw.build(SPEC, BRIEF, D, tonight, hist, [])
    assert none_shown is not None and "in today’s items" not in none_shown["line"]


def test_scope_leaves_out_other_metrics() -> None:
    brief = {"id": "b2", "scope": {"metrics": ["dso"]}}
    tonight = [f("rev", metric="net_revenue"), f("d", metric="dso", days=3, first="2026-11-23")]
    got = sw.build(SPEC, brief, D, tonight, {"nights": [], "meta": {}}, [])
    assert got is not None and got["counts"]["open"] == 1 and got["longest"]["fingerprint"] == "d"


def test_the_chart_counts_open_items_and_marks_fixes(monkeypatch) -> None:
    monkeypatch.setattr(sw, "CHART_MIN", 2)  # three nights here; the real minimum is below
    hist = {
        "nights": [
            night(
                "2026-11-23",
                ["a", "open", "1", "Open · day 13", None, None],
                ["b", "open", "1", "Open · day 2", None, None],
            ),
            night(
                "2026-11-24",
                ["a", "open", "1", "Open · day 14", None, None],
                ["b", "resolved", "1", "Resolved", "Cleared by data", "2026-11-24"],
            ),
        ],
        "meta": {"a": meta("a"), "b": meta("b", first="2026-11-22")},
    }
    got = sw.build(SPEC, BRIEF, D, [f("a")], hist, [])
    assert got is not None
    spec = ChartSpec.from_dict(json.loads(got["chart"]))
    assert spec.kind == "step"
    assert [v for _, v in spec.series[0].points] == [2.0, 1.0, 1.0]
    assert [(a.at, a.label) for a in spec.annotations] == [("2026-11-24", "1 fixed")]
    assert got["fixed"][0]["how"] == "Back to normal on 24 Nov"
    # 21 Nov 2026: fewer than 5 nights draw a flat line that says nothing; no chart then
    monkeypatch.setattr(sw, "CHART_MIN", 5)
    assert "chart" not in sw.build(SPEC, BRIEF, D, [f("a")], hist, [])


def test_the_chart_is_dated_a_week_apart() -> None:
    """The brief's chart style (owner, 20 Nov 2026): dates on the x-axis a week apart, small
    text, the fix dots kept; other charts keep their month row."""
    from othor.charts.svg import render_svg

    days = [dt.date(2026, 10, 27) + dt.timedelta(days=i) for i in range(30)]
    hist = {
        "nights": [night(d.isoformat(), ["a", "open", "1", "Open", None, None]) for d in days[:-1]],
        "meta": {"a": meta("a", first="2026-10-27")},
    }
    got = sw.build(SPEC, BRIEF, days[-1], [f("a", first="2026-10-27")], hist, [])
    assert got is not None
    svg = render_svg(ChartSpec.from_dict(json.loads(got["chart"])), (560, 150))
    for label in ("27 Oct", "3 Nov", "10 Nov", "17 Nov"):
        assert f">{label}<" in svg, label
    assert ">Nov<" not in svg and ">Oct<" not in svg
    # a week of history (a new company): every second day, so it still reads
    week = sw.build(
        SPEC,
        BRIEF,
        days[6],
        [f("a", first="2026-10-27")],
        {"nights": hist["nights"][:6], "meta": hist["meta"]},
        [],
    )
    assert week is not None
    short = render_svg(ChartSpec.from_dict(json.loads(week["chart"])), (560, 150))
    assert ">27 Oct<" in short and ">29 Oct<" in short and ">31 Oct<" in short


def test_one_suggestion_for_the_longest_unchanged_item_only() -> None:
    tonight = [f("long", days=16), f("moved", change="Worse", days=30, first="2026-10-27")]
    hist = {
        "nights": [night("2026-11-17", ["long", "open", "1", "Worse", None, None])],
        "meta": {"long": meta("long"), "moved": meta("moved", first="2026-10-27")},
    }
    got = sw.build(SPEC, BRIEF, D, tonight, hist, [])
    assert got is not None
    assert got["suggest"] == {
        "fingerprint": "long",
        "action": "snooze",
        "text": "Open 16 days, unchanged for 8: LONG. Set it aside for a week?",
    }
    # changed four days ago: no suggestion
    hist["nights"].append(night("2026-11-21", ["long", "open", "1", "Better", None, None]))
    again = sw.build(SPEC, BRIEF, D, tonight, hist, [])
    assert again is not None and "suggest" not in again


def test_a_weekly_brief_reads_its_period() -> None:
    brief = {"id": "w", "scope": {}, "period": {"from": "2026-11-18", "word": "last week"}}
    got = sw.build(SPEC, brief, D, [f("x", days=3, first="2026-11-23")], None, [])
    assert got is not None and got["line"].startswith("Since last week: 1 reported")


def test_same_inputs_same_bytes() -> None:
    tonight = [
        f("a"),
        f("b", "resolved", resolved={"date": "2026-11-24", "reason": "Cleared by data"}),
    ]
    hist = {
        "nights": [
            night(
                "2026-11-24",
                ["a", "open", "1", "Open", None, None],
                ["b", "open", "1", "Open", None, None],
            )
        ],
        "meta": {"a": meta("a"), "b": meta("b")},
    }
    one = json.dumps(sw.build(SPEC, BRIEF, D, tonight, hist, []), sort_keys=True)
    two = json.dumps(sw.build(SPEC, BRIEF, D, list(reversed(tonight)), hist, []), sort_keys=True)
    assert one == two
