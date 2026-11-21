# SPDX-License-Identifier: Apache-2.0
"""A whole issue from a run (4d, 4d0): quiet when nothing in scope passes, with no Ask today; the
same inputs give the same bytes; a stale source marks its tiles and its items."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from othor.briefs.assemble import assemble
from othor.engine.store import Point
from othor.ontology.compiler.api import canonical

RUN = dt.date(2026, 11, 1)
SPEC = {
    "metrics": {
        "dso": {
            "name": "DSO",
            "unit": "days",
            "grain": "month",
            "kind": "flow",
            "status": "active",
        },
        "net_revenue": {
            "name": "Net revenue",
            "unit": "inr",
            "grain": "month",
            "kind": "flow",
            "status": "active",
        },
    },
    "forecast": {"goals": []},
    "detectors": {"materiality_inr": "500000"},
}


def _points(metric, values):
    out, d = [], dt.date(2025, 11, 1)
    for v in values:
        out.append(Point(metric, "month", "", None, d, Decimal(str(v))))
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


def _version(**kw):
    return {
        "name": "Finance",
        "question": "Is DSO on track?",
        "scope": {"metrics": ["dso"], "exclude": {}},
        "headline": ["dso"],
        "ask_owner": "top_item",
        "goals": [],
        "time": "06:00",
        **kw,
    }


def _issue(findings, **kw):
    return assemble(
        SPEC,
        _version(),
        run_date=RUN,
        findings=findings,
        watches=[],
        goals=[],
        record=[],
        points=_points("dso", [50] * 11 + [50.1]),
        sweep_counts={"findings": len(findings)},
        tests=212,
        **kw,
    )


def test_a_quiet_day_says_nothing_needs_you_and_asks_nothing():
    revenue = {
        "fingerprint": "x",
        "status": "open",
        "detector": "net_hiding_gross",
        "metric": "net_revenue",
        "family": "net_revenue",
        "state": "assessed",
        "confidence": "high",
        "money": "14000000",
        "first_seen": {"date": "2026-11-01"},
        "slots": {},
        "text": {"title": "South fell"},
        "window": ["2026-09-01", "2026-10-31"],
        "entities": [],
    }
    got = _issue([revenue])  # a finding outside this brief's scope (net revenue)
    assert got["quiet"] and not got["items"] and got["ask"] is None
    assert got["title"] == "DSO is on plan; nothing significant came up."
    assert got["line"] == (
        "The headline number is on plan. 212 tests found nothing above the ₹5 L floor in "
        "this brief’s scope."
    )
    assert got["more_in_sweep"] == 1  # the finding is in Sweep, outside this brief
    assert canonical(got) == canonical(_issue([revenue]))  # same inputs, same bytes


def test_a_stale_source_marks_its_tiles():
    got = _issue([], stale_as_of={"dso": dt.date(2026, 10, 30)})
    (t,) = got["tiles"]
    assert t["stale"] == "as of Fri" and got["stale"] == ["dso"]


def _no_headline(scope_metrics, order=None):
    spec = {**SPEC, **({"order": order} if order else {})}
    return assemble(
        spec,
        _version(headline=[], scope={"metrics": scope_metrics, "exclude": {}}),
        run_date=RUN,
        findings=[],
        watches=[],
        goals=[],
        record=[],
        points=_points("dso", [50] * 12) + _points("net_revenue", [100] * 12),
        sweep_counts={"findings": 0},
        tests=212,
    )


def test_no_headline_numbers_shows_the_first_confirmed_metrics():
    """v0.11 (owner, 7 Nov): no tiles is never shown; the first confirmed metrics with a value
    are, in the Ontology's order, with a note; the title still speaks of headline numbers only."""
    got = _no_headline(None, order=["net_revenue", "dso"])
    assert [t["metric"] for t in got["tiles"]] == ["net_revenue", "dso"]
    assert got["tiles_note"].startswith("No headline numbers chosen yet")
    assert "headline number is on plan" not in got["line"]
    assert canonical(got) == canonical(_no_headline(None, order=["net_revenue", "dso"]))


def test_first_metrics_keep_to_the_brief_scope():
    got = _no_headline(["dso"])
    assert [t["metric"] for t in got["tiles"]] == ["dso"]


def test_a_brief_with_headline_numbers_has_no_note():
    assert "tiles_note" not in _issue([])  # other issues keep their bytes (golden 1 Nov)


# --- M15.7c part B: a weekly or monthly issue covers its whole period ---------------------------


def _dso(fp, money, change="Open · day 9", days=9):
    return {
        "fingerprint": fp,
        "status": "open",
        "detector": "changepoint",
        "metric": "dso",
        "family": "dso",
        "state": "measured",
        "confidence": None,
        "money": money,
        "change": change,
        "days_open": days,
        "first_seen": {"date": "2026-10-20"},
        "slots": {},
        "text": {"title": f"DSO {fp}"},
        "window": ["2026-09-01", "2026-10-31"],
        "entities": [],
    }


def _was(money, metric="dso"):
    return {
        "money": money,
        "metric": metric,
        "breakdown": "",
        "breakdown_value": None,
        "entities": [],
    }


PERIOD = {
    "from": "2026-10-25",
    "word": "last week",
    "open": {
        "a": _was("1000000"),
        "b": _was("800000"),
        "c": _was("900000", "net_revenue"),  # outside this brief's scope
        "e": _was("700000"),
    },
}


def test_a_weekly_issue_says_what_changed_since_last_weeks_brief():
    tonight = [
        _dso("a", "1300000", change="Open · day 9"),  # up 30% on last week: Worse
        _dso("d", "600000", change="Open · day 3", days=3),  # not open at the last issue: New
        _dso("e", "700000", change="Worse"),  # worse since yesterday, but flat on the week
    ]  # b: open last week, gone tonight: closed in the period
    got = assemble(
        SPEC,
        _version(schedule="weekly", on="sun", period=PERIOD),
        run_date=RUN,
        findings=tonight,
        watches=[],
        goals=[],
        record=[],
        points=_points("dso", [50] * 11 + [50.1]),
        sweep_counts={"findings": 3},
        tests=212,
    )
    changes = {i["fingerprint"]: i["change"] for i in got["items"]}
    assert changes == {"a": "Worse", "d": "New", "e": "Open · day 9"}
    assert got["since"] == {"new": 1, "worse": 1, "better": 0, "open": 1, "resolved": 1}
    assert got["period"] == {"from": "2026-10-25", "word": "last week"}


def test_a_daily_issue_has_no_period():
    assert "period" not in _issue([])


def test_an_item_unchanged_for_a_week_steps_out_to_still_open():
    """0.28.4 (owner, 19 Nov 2026; the 100-night test: the same items for 63 nights)."""
    got = _issue(
        [
            _dso("old", "900000"),  # open since 20 Oct, unchanged: 12 days
            _dso("new", "600000", change="New", days=1),
            _dso("worse", "500000", change="Worse"),  # changed tonight: stays in
        ]
    )
    assert {i["fingerprint"] for i in got["items"]} == {"new", "worse"}
    assert got["still_open"] == [{"fingerprint": "old", "title": "DSO old", "days": 12}]
    assert all(i["since"] == RUN.isoformat() for i in got["items"])
    # yesterday's brief kept the day it last changed: 3 days ago is still in
    prev = [{"fingerprint": "old", "rank": 1, "score": "1", "since": "2026-10-29"}]
    again = assemble(
        SPEC,
        _version(previous=prev),
        run_date=RUN,
        findings=[_dso("old", "900000")],
        watches=[],
        goals=[],
        record=[],
        points=_points("dso", [50] * 11 + [50.1]),
        sweep_counts={"findings": 1},
        tests=212,
    )
    assert [i["fingerprint"] for i in again["items"]] == ["old"] and again["still_open"] == []


def test_a_quiet_day_with_items_still_open_says_so_the_largest_first():
    """Owner, 21 Nov 2026: nothing new or changing tonight, but items from before are still
    open: the key message says so; "Nothing significant came up" only when nothing is open."""
    one = _issue([_dso("old", "900000", days=13)])
    assert one["quiet"] and not one["items"]
    assert (
        one["title"] == "Nothing new came up. 1 item from last week is still open: DSO old, day 13."
    )
    three = _issue([_dso("a", "500000"), _dso("b", "900000", days=12), _dso("c", "700000")])
    assert three["quiet"]
    assert three["title"] == (
        "Nothing new came up. 3 items from last week are still open; the largest: DSO b, day 12."
    )
    assert _issue([])["title"] == "DSO is on plan; nothing significant came up."


def test_whom_to_ask_without_a_team():
    """0.28.4 (owner): a plain role from the number's area and where it happened."""
    from othor.briefs import title

    spec = {
        "metrics": {
            "net_revenue": {"name": "Net revenue", "area": "Sales"},
            "x": {"name": "Dispatch time"},
        }
    }
    assert title.role_for({"metric": "net_revenue", "breakdown_value": "South"}, spec) == (
        "your sales head for South"
    )
    assert title.role_for({"metric": "x"}, spec) == "whoever looks after dispatch time"
    assert title.role_for({"metric": "y", "detector": "missing_records"}, spec) == (
        "your account managers"
    )
