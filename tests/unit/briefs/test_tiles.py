# SPDX-License-Identifier: Apache-2.0
"""Headline tiles (decided 5 Nov 2026): the value of the last complete period and one comparison,
named: band first (distance from the nearest band edge), then the goal, then the last period
("no change" under 0.5%). Before 8 weeks of history: "N days of history · trend and band from
<date>" (10j). A stale source: "as of Fri" (10l). The sparkline is a ChartSpec."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from othor.briefs import tiles
from othor.charts.spec import ChartSpec
from othor.engine.store import Point

RUN = dt.date(2026, 11, 1)
SPEC = {
    "metrics": {
        "net_revenue": {"name": "Net revenue", "unit": "inr", "grain": "month", "kind": "flow"},
        "dso": {"name": "DSO", "unit": "days", "grain": "month", "kind": "flow"},
        "cash": {"name": "Cash", "unit": "inr", "grain": "day", "kind": "snapshot"},
    }
}


def _months(metric, values, start=dt.date(2025, 11, 1)):
    out, d = [], start
    for v in values:
        out.append(Point(metric, "month", "", None, d, Decimal(str(v))))
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


def _rec(metric, period, lo, hi, grain="month"):
    return {
        "series": f"{metric}|{grain}",
        "metric": metric,
        "grain": grain,
        "period": period,
        "point": str((lo + hi) / 2),
        "lower": str(lo),
        "upper": str(hi),
        "as_of": "2026-09-30",
    }


def test_band_first_measured_from_the_nearest_edge():
    pts = _months("net_revenue", [45e6] * 11 + [46_200_000])
    rec = [_rec("net_revenue", "2026-10-01", 46_728_592, 49_000_000)]
    t = tiles.tile(SPEC, "net_revenue", pts, rec, [], RUN)
    assert t["label"] == "Net revenue · Oct" and t["value"] == "₹4.62 Cr"
    assert t["comparison"] == {"kind": "band", "text": "−1% vs band"}
    assert t["role"] == "negative"
    inside = tiles.tile(
        SPEC, "net_revenue", pts, [_rec("net_revenue", "2026-10-01", 4e7, 5e7)], [], RUN
    )
    assert inside["comparison"] == {"kind": "band", "text": "in band"}
    chart = ChartSpec.from_json(t["chart"])
    assert chart.kind == "sparkline" and len(chart.series[0].points) == 12 and chart.band


def test_a_miss_that_rounds_to_zero_is_in_band_never_minus_zero():
    """0.33.1 (the 100-night run, 38 nights): "−0% vs band"."""
    pts = _months("net_revenue", [45e6] * 11 + [46_200_000])
    just_under = [_rec("net_revenue", "2026-10-01", 46_210_000, 49_000_000)]
    t = tiles.tile(SPEC, "net_revenue", pts, just_under, [], RUN)
    assert t["comparison"] == {"kind": "band", "text": "in band"}
    assert t["role"] == "neutral"
    just_over = [_rec("net_revenue", "2026-10-01", 40_000_000, 46_190_000)]
    assert tiles.tile(SPEC, "net_revenue", pts, just_over, [], RUN)["comparison"]["text"] == (
        "in band"
    )


def test_a_month_close_tomorrow_is_one_day():
    """0.33.1 (the 100-night run, 29 Sep): "September close in 1 days"."""
    from othor.briefs import consequence

    f = {"detector": "net_hiding_gross", "metric": "net_revenue", "slots": {}, "text": {}}
    assert consequence.of(f, SPEC, dt.date(2026, 9, 29))["text"] == "September close in 1 day"
    assert consequence.of(f, SPEC, dt.date(2026, 9, 30))["text"] == "September close today"
    assert consequence.of(f, SPEC, dt.date(2026, 9, 2))["text"] == "September close in 28 days"


def test_then_the_goal_then_the_last_period():
    pts = _months("dso", [50] * 11 + [46])
    goal = [{"metric": "dso", "target": "45", "on_track": True, "aggregate": "value"}]
    assert tiles.tile(SPEC, "dso", pts, [], goal, RUN)["comparison"] == {
        "kind": "goal",
        "text": "on track for 45 · vs target",
    }
    off = [{**goal[0], "on_track": False}]
    assert (
        tiles.tile(SPEC, "dso", pts, [], off, RUN)["comparison"]["text"] == "target 45 · vs target"
    )
    flat = _months("dso", [50] * 11 + [50.1])
    assert tiles.tile(SPEC, "dso", flat, [], [], RUN)["comparison"] == {
        "kind": "period",
        "text": "no change vs Sep",
    }
    up = _months("dso", [50] * 11 + [52])
    assert tiles.tile(SPEC, "dso", up, [], [], RUN)["comparison"]["text"] == "+4% vs Sep"


def test_first_days_and_stale():
    days = [
        Point("cash", "day", "", None, RUN - dt.timedelta(days=i), Decimal("21000000"))
        for i in (3, 2, 1)
    ]
    t = tiles.tile(SPEC, "cash", days, [], [], RUN)
    assert t["comparison"] == {
        "kind": "history",
        "text": "3 days of history · trend and band from 24 Dec",
    }
    s = tiles.tile(SPEC, "cash", days, [], [], RUN, stale_as_of={"cash": dt.date(2026, 10, 30)})
    assert s["stale"] == "as of Fri"
