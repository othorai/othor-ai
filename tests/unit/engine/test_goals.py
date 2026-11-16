# SPDX-License-Identifier: Apache-2.0
"""Goal projection (docs/04 §5): a fiscal-year total = actual to date (April onwards) + tonight's
stored forecasts for the remaining months; a value goal = the forecast for its month."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from othor.engine.detectors.base import SeriesIndex
from othor.engine.forecast.store import goals
from tests.detectors.synth import points

SPEC = {
    "forecast": {
        "goals": [
            {
                "key": "fy",
                "metric": "rev",
                "target": "600",
                "by": "2027-03-31",
                "aggregate": "fiscal_year_total",
            },
            {
                "key": "dso",
                "metric": "dso",
                "target": "45",
                "by": "2027-03-31",
                "direction": "at_most",
            },
        ]
    }
}


def _rows(metric, months, point="10", lo="8", hi="12"):
    return [
        {
            "metric": metric,
            "grain": "month",
            "period": m,
            "point": point,
            "lower": lo,
            "upper": hi,
            "reconstructed": False,
        }
        for m in months
    ]


def test_fiscal_year_total_and_value_goal():
    months = [dt.date(2026, m, 1) for m in range(1, 11)]  # Jan–Oct actuals
    index = SeriesIndex(points("rev", "month", months, [50] * 10))
    ahead = [
        dt.date(2026, 11, 1),
        dt.date(2026, 12, 1),
        dt.date(2027, 1, 1),
        dt.date(2027, 2, 1),
        dt.date(2027, 3, 1),
    ]
    rows = _rows("rev", [d.isoformat() for d in ahead], "60", "55", "65")
    rows += _rows("dso", ["2027-03-01"], "47", "44", "50")
    fy, dso = goals(SPEC, index, rows)
    # April–October (7 months × 50) + 5 forecast months × 60; January–March 2026 are not FY27
    assert Decimal(fy["to_date"]) == 350 and Decimal(fy["projection"]) == 650
    assert Decimal(fy["lower"]) == 625 and Decimal(fy["upper"]) == 675
    assert Decimal(fy["short_by"]) == -50 and fy["on_track"] is True
    assert (
        Decimal(dso["projection"]) == 47
        and Decimal(dso["short_by"]) == 2
        and dso["on_track"] is False
    )


def test_no_projection_without_every_remaining_month():
    months = [dt.date(2026, m, 1) for m in range(4, 11)]
    index = SeriesIndex(points("rev", "month", months, [50] * 7))
    assert (
        goals(
            {"forecast": {"goals": SPEC["forecast"]["goals"][:1]}},
            index,
            _rows("rev", ["2026-11-01"]),
        )
        == []
    )
