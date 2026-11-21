# SPDX-License-Identifier: Apache-2.0
"""The ordinary picture (Morning Brief 2.4, Weekly Digest 1.1; docs/plans/ordinary-picture.md):
on a day or week with nothing significant, code picks and computes a few plain updates from the
run's stored points: the numbers that moved most (against their own usual variation), where each
sits in its usual range, the best and weakest part, each goal's progress. Pure and
deterministic; never worded as a finding."""

from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal

from othor.briefs import ordinary
from othor.engine.store import Point
from othor.ontology.compiler.api import canonical

SPEC = {
    "metrics": {
        "collections": {
            "name": "Collections",
            "unit": "inr",
            "grain": "day",
            "kind": "flow",
            "status": "active",
        },
        "dso": {
            "name": "Days to collect",
            "unit": "days",
            "grain": "month",
            "kind": "flow",
            "status": "active",
        },
        "margin": {
            "name": "Gross margin",
            "unit": "percent",
            "grain": "month",
            "kind": "flow",
            "status": "active",
        },
    },
    "breakdowns": {
        "region": {"name": "Region", "cap": 50, "entity": False},
        "customer": {"name": "Customer", "cap": 50, "entity": True},
    },
}
WEEK0 = dt.date(2026, 8, 3)  # a Monday


def _weeks(metric, values, breakdown="", bv=None):
    return [
        Point(metric, "week", breakdown, bv, WEEK0 + dt.timedelta(weeks=i), Decimal(str(v)))
        for i, v in enumerate(values)
    ]


def _months(metric, values):
    out, d = [], dt.date(2026, 1, 1)
    for v in values:
        out.append(Point(metric, "month", "", None, d, Decimal(str(v))))
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


# collections: steady around ₹1 Cr with a little wobble, last week up 8%
COLL = [10_000_000, 10_200_000, 9_900_000, 10_100_000, 9_800_000, 10_000_000, 10_300_000]
COLL += [9_900_000, 10_100_000, 10_000_000, 9_800_000, 9_814_815, 10_600_000]
POINTS = (
    _weeks("collections", COLL)
    # regions add up to the total in the last two weeks
    + _weeks("collections", [0] * 11 + [4_000_000, 4_600_000], "region", "West")
    + _weeks("collections", [0] * 11 + [3_314_815, 3_254_815], "region", "North")
    + _weeks("collections", [0] * 11 + [2_500_000, 2_745_185], "region", "South")
    # a customer breakdown never counts as a part (named accounts are not groups)
    + _weeks("collections", [0] * 11 + [1_000_000, 9_000_000], "customer", "Acme")
    + _months("dso", [44, 45, 46, 45, 44, 45, 46, 45, 45, 45.2])
    + _months("margin", [0.30, 0.31, 0.30, 0.29, 0.30, 0.31, 0.30, 0.30, 0.30, 0.32])
)
GOALS = [
    {
        "key": "fy27",
        "metric": "collections",
        "aggregate": "fiscal_year_total",
        "direction": "at_least",
        "target": "600000000",
        "projection": "572000000",
        "by": "2027-03-31",
        "probability": 61,
    }
]


def _pic(**kw):
    args = {"record": [], "goals": GOALS, **kw}
    return ordinary.picture(SPEC, ["collections", "dso", "margin"], POINTS, **args)


def test_the_numbers_that_moved_most_lead_ranked_by_their_own_usual_variation():
    p = _pic()
    assert p["movers"][0]["metric"] == "collections"
    c = p["movers"][0]
    assert c["text"] == (
        "Collections ₹1.06 Cr in the week of 26 Oct, up 8% on the week before, "
        "the highest in 13 weeks."
    )
    assert c["window"] == ["2026-10-19", "2026-10-26"]
    # a small move on a steady number still says how it moved, in its own unit
    dso = next(m for m in p["movers"] if m["metric"] == "dso")
    assert dso["text"].startswith("Days to collect 45 d in Oct, about the same as Sep")
    margin = next(m for m in p["movers"] if m["metric"] == "margin")
    assert "up 2 points on Sep" in margin["text"]


def test_where_a_number_sits_in_its_usual_range_from_the_forecast_of_record():
    rec = [
        {
            "metric": "collections",
            "grain": "week",
            "period": "2026-10-26",
            "lower": "9500000",
            "upper": "11500000",
        }
    ]
    rec[0]["upper"] = "11500000"
    c = _pic(record=rec)["movers"][0]
    assert c["range"] == "within its usual range, near the middle"
    rec[0]["upper"] = "10700000"
    assert _pic(record=rec)["movers"][0]["range"] == "within its usual range, near the top"
    rec[0]["upper"] = "10500000"
    assert _pic(record=rec)["movers"][0]["range"] == "above its usual range"


def test_the_best_and_weakest_part_never_an_account():
    (part, *_) = _pic()["parts"]
    assert part["breakdown"] == "region"
    assert part["text"] == (
        "Collections by region: West up ₹6 L and North down ₹60,000 on the week before."
    )
    assert part["chart"]  # contribution bars, a stored ChartSpec
    assert "Acme" not in canonical(_pic())


def test_each_goal_s_progress_even_on_track():
    (g,) = _pic()["goals"]
    assert g["text"] == (
        "Collections FY27: heading for ₹57.2 Cr against the ₹60 Cr goal, 61% likely to reach it."
    )


def test_up_to_four_lines_movers_first_then_the_parts_then_goals():
    lines = _pic()["lines"]
    assert len(lines) == 4
    assert [x["kind"] for x in lines] == ["mover", "parts", "mover", "goal"]
    assert all(x["text"].endswith(".") for x in lines)
    assert all(x["metric"] for x in lines)


def test_never_worded_as_a_finding():
    from tests.guards.test_plain_words import _texts, offenders

    text = canonical(_pic()["lines"]).lower()
    for word in ("finding", "at stake", "needs you", "open ·", "anomal", "band"):
        assert word not in text
    assert offenders(_texts([x["text"] for x in _pic()["lines"]])) == []


def test_deterministic_same_inputs_same_bytes():
    a = canonical(_pic())
    b = canonical(
        ordinary.picture(
            SPEC,
            ["collections", "dso", "margin"],
            copy.deepcopy(POINTS),
            record=[],
            goals=copy.deepcopy(GOALS),
        )
    )
    assert a == b
    # the order of the points never matters
    shuffled = list(reversed(POINTS))
    assert (
        canonical(
            ordinary.picture(
                SPEC, ["collections", "dso", "margin"], shuffled, record=[], goals=GOALS
            )
        )
        == a
    )


def test_nothing_to_say_without_history_or_for_a_stale_number():
    assert ordinary.picture(SPEC, ["collections"], _weeks("collections", [5]))["lines"] == []
    got = _pic(stale={"collections"})
    assert all(x["metric"] != "collections" for x in got["movers"] + got["parts"])
    assert ordinary.picture(SPEC, [], POINTS)["lines"] == []


def test_a_rebound_is_called_a_rebound_never_a_huge_percentage():
    """A slow week then back to usual: "back to its usual level after a slow week", not +378%."""
    steady = [10_000_000, 10_200_000, 9_900_000, 10_100_000, 9_800_000, 10_000_000, 10_300_000]
    pts = _weeks("collections", [*steady, 2_100_000, 10_050_000])
    (m,) = ordinary.picture(SPEC, ["collections"], pts)["movers"]
    assert m["shape"] == "rebound" and "%" not in m["text"]
    assert m["text"] == (
        "Collections ₹1 Cr in the week of 28 Sep, back to its usual level after a slow week."
    )


def test_a_rise_over_100_percent_is_written_from_one_value_to_the_other():
    pts = _weeks("collections", [1_000_000] * 8 + [3_000_000])
    (m,) = ordinary.picture(SPEC, ["collections"], pts)["movers"]
    assert m["change"] == "up from ₹10 L the week before" and "%" not in m["text"]
    assert m["shape"] == "record_high" and '"kind":"line"' in m["chart"]


# --- Morning Brief 2.4: the quiet issue -------------------------------------------------------


def _brief(findings, **kw):
    from othor.briefs.assemble import assemble

    version = {
        "name": "Finance",
        "question": "How is cash coming in?",
        "scope": {"metrics": None, "exclude": {}},
        "headline": ["collections", "dso", "margin"],
        "ask_owner": "top_item",
        "goals": ["fy27"],
        "time": "06:00",
    }
    return assemble(
        {**SPEC, "forecast": {"goals": []}, "detectors": {"materiality_inr": "500000"}},
        version,
        run_date=dt.date(2026, 11, 2),
        findings=findings,
        watches=[],
        goals=GOALS,
        record=[],
        points=POINTS,
        sweep_counts={"findings": len(findings)},
        tests=212,
        **kw,
    )


def test_a_quiet_issue_says_nothing_significant_then_the_updates():
    got = _brief([])
    assert got["quiet"] and got["title"] == "Nothing significant came up."
    o = got["ordinary"]
    assert o["intro"] == "Here are some updates from your numbers"
    assert [x["text"] for x in o["lines"]] == [x["text"] for x in _pic()["lines"]]
    # never in the money line, never counted as items
    assert not got["items"] and "₹1.06 Cr" not in got["line"]
    assert canonical(got) == canonical(_brief([]))  # same inputs, same bytes


def test_an_issue_with_items_has_no_ordinary_picture():
    f = {
        "fingerprint": "x",
        "status": "open",
        "detector": "drift",
        "metric": "collections",
        "family": "collections",
        "state": "measured",
        "confidence": "high",
        "money": "9000000",
        "change": "New",
        "first_seen": {"date": "2026-11-02"},
        "slots": {},
        "text": {"title": "Collections fell"},
        "window": ["2026-10-01", "2026-11-01"],
        "entities": [],
    }
    got = _brief([f])
    assert not got["quiet"] and "ordinary" not in got
