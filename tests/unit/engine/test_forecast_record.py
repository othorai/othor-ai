# SPDX-License-Identifier: Apache-2.0
"""v0.19 (M19.3): the real track record, carried night to night, and how sure each forecast is."""

from __future__ import annotations

import datetime as dt

from othor.engine.forecast.confidence import MIN_REAL, level, track
from othor.engine.forecast.record import KEEP, carry

D = dt.date


def _pt(period: str, p50: str = "100") -> dict:
    return {"period": period, "p10": "90", "p50": p50, "p90": "110"}


def test_a_forecast_is_kept_until_its_period_completes_then_scored_once():
    # 1 Nov: the week of 2 Nov hasn't started: pending
    rec, pend = carry(None, {"r|week": [_pt("2026-11-02")]}, {}, D(2026, 11, 1))
    assert rec == {} and pend["r|week"]["2026-11-02"]["p50"] == "100"
    # 2 Nov: it has started; tonight's newer forecast doesn't replace the one made before it began
    prev = {"record": rec, "pending": pend}
    rec, pend = carry(prev, {"r|week": [_pt("2026-11-02", "120")]}, {}, D(2026, 11, 3))
    assert pend["r|week"]["2026-11-02"]["p50"] == "100"
    # 9 Nov: the week is complete at 105: scored, inside, 5% error, gone from pending
    prev = {"record": rec, "pending": pend}
    rec, pend = carry(prev, {}, {"r|week": {"2026-11-02": 105.0}}, D(2026, 11, 9))
    (r,) = rec["r|week"]
    assert r["inside"] is True and r["error_pct"] == "4.76" and r["as_of"] == "2026-10-31"
    assert pend == {}
    # and never scored twice
    rec2, _ = carry(
        {"record": rec, "pending": pend}, {}, {"r|week": {"2026-11-02": 105.0}}, D(2026, 11, 10)
    )
    assert len(rec2["r|week"]) == 1


def test_the_record_keeps_the_last_twelve():
    old = [
        {
            "period": f"2026-{m:02d}-01",
            "p10": "1",
            "p50": "2",
            "p90": "3",
            "actual": "2",
            "inside": True,
            "error_pct": "0",
        }
        for m in range(1, 13)
    ]
    pend = {"r|month": {"2027-01-01": {"p10": "1", "p50": "2", "p90": "3", "as_of": "2026-12-31"}}}
    rec, _ = carry(
        {"record": {"r|month": old}, "pending": pend},
        {},
        {"r|month": {"2027-01-01": 2.5}},
        D(2027, 2, 1),
    )
    assert len(rec["r|month"]) == KEEP and rec["r|month"][-1]["period"] == "2027-01-01"
    assert rec["r|month"][0]["period"] == "2026-02-01"


def test_backtest_until_six_real_forecasts_then_the_real_record():
    sel = {"calibration": {"inside": 10, "of": 12}, "mape": "0.0800"}
    t = track([], sel)
    assert t["source"] == "backtest" and t["n"] == 12 and t["typical_error_pct"] == "8.00"
    assert t["level"] == "high"
    real = [
        {
            "period": f"2026-0{i}-01",
            "p10": "9",
            "p50": "10",
            "p90": "11",
            "actual": "10.5",
            "inside": i != 3,
            "error_pct": str(e),
        }
        for i, e in zip(range(1, MIN_REAL + 1), (2, 4, 30, 6, 8, 10), strict=True)
    ]
    t = track(real, sel)
    assert t["source"] == "real" and t["n"] == 6 and t["inside"] == 5
    assert t["typical_error_pct"] == "7.00"  # the median, not the mean
    assert t["last"]["period"] == "2026-06-01"


def test_the_three_levels():
    assert level(0.8, 5.0) == "high"
    assert level(0.7, 5.0) == "medium"
    assert level(0.9, 15.0) == "medium"
    assert level(0.5, 5.0) == "low"
    assert level(0.9, 30.0) == "low"
