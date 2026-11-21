# SPDX-License-Identifier: Apache-2.0
"""Forecasts 1.0 (F2): how far a number's forecast can see ahead, from the stored `horizon`
rows and the company's one boundary setting (the outlook's `fog_band_share`, ±35% by default)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from othor.engine.forecast import sight


def _rows(halves: list[str], p50: str = "100") -> list[dict[str, object]]:
    start = dt.date(2026, 11, 2)
    return [
        {
            "h": i + 1,
            "period": (start + dt.timedelta(weeks=i)).isoformat(),
            "p10": str(Decimal(p50) - Decimal(h)),
            "p50": p50,
            "p90": str(Decimal(p50) + Decimal(h)),
        }
        for i, h in enumerate(halves)
    ]


def test_counts_the_periods_whose_half_range_is_within_the_share() -> None:
    got = sight.can_see(_rows(["10", "20", "34", "36", "50"]), Decimal("0.35"))
    assert got.periods == 3 and got.last == "2026-11-16"


def test_the_boundary_itself_is_inside() -> None:
    # exactly ±35% is still a call (decimal arithmetic: no float rounding at the edge)
    got = sight.can_see(_rows(["35", "35.000001"]), Decimal("0.35"))
    assert got.periods == 1


def test_it_stops_at_the_first_period_past_the_boundary() -> None:
    # a later narrower period doesn't count: once the range passes the boundary it's a guess
    got = sight.can_see(_rows(["10", "40", "10"]), Decimal("0.35"))
    assert got.periods == 1 and got.last == "2026-11-02"


def test_no_range_or_a_zero_forecast_sees_nothing() -> None:
    rows = _rows(["10"])
    rows[0]["p10"] = rows[0]["p90"] = None
    assert sight.can_see(rows, Decimal("0.35")) == sight.Sight(0, None, 1)
    assert sight.can_see(_rows(["1"], p50="0"), Decimal("0.35")).periods == 0
    assert sight.can_see([], Decimal("0.35")) == sight.Sight(0, None, 0)


def test_negative_forecasts_use_the_size_of_the_point() -> None:
    assert sight.can_see(_rows(["30", "40"], p50="-100"), Decimal("0.35")).periods == 1


def test_all_periods_inside_says_so() -> None:
    got = sight.can_see(_rows(["1"] * 12), Decimal("0.35"))
    assert got.periods == 12 and got.all_of == 12 and got.full


def test_the_share_comes_from_the_company_setting() -> None:
    assert sight.share({}) == Decimal("0.35")
    assert sight.share({"weather": {"fog_band_share": "0.25"}}) == Decimal("0.25")


def test_zone_of_a_period() -> None:
    rows = _rows(["10", "20", "40"])
    s = sight.can_see(rows, Decimal("0.35"))
    assert [sight.zone(s, i) for i in (1, 2, 3, 4)] == [
        "likely",
        "likely",
        "estimate",
        "assumption",
    ]


def test_period_words() -> None:
    assert sight.span(2, "week") == "2 weeks"
    assert sight.span(1, "month") == "1 month"
    assert sight.span(12, "month", full=True) == "12 months or more"
    assert sight.span(0, "week") == "Not yet"
