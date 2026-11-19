# SPDX-License-Identifier: Apache-2.0
"""0.28 (M28.1): the targets offered for a goal are computed by code from the company's own
history and forecasts: last year's actual, where it's heading, a stretch; never a model's."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from othor.briefs import values
from othor.engine.detectors.base import SeriesIndex
from othor.engine.store import Point
from othor.ontology import goal_targets as gt

D = dt.date


def _months(start: dt.date, n: int, value: str) -> list[Point]:
    out, p = [], start
    for _ in range(n):
        out.append(Point("rev", "month", "", None, p, Decimal(value)))
        p = (p.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


def _row(period: str, point: str, lower: str, upper: str) -> dict:  # type: ignore[type-arg]
    return {
        "metric": "rev",
        "grain": "month",
        "period": period,
        "point": point,
        "lower": lower,
        "upper": upper,
        "reconstructed": False,
    }


# FY from April: FY26 Apr 2025–Mar 2026 at 10 a month; FY27 so far Apr–Oct 2026 at 12 a month
INDEX = SeriesIndex(_months(D(2025, 4, 1), 12, "10") + _months(D(2026, 4, 1), 7, "12"))
ROWS = [
    _row(p, "12", "11", "14")
    for p in ("2026-11-01", "2026-12-01", "2027-01-01", "2027-02-01", "2027-03-01")
]


def test_fiscal_year_end_follows_the_setting() -> None:
    assert gt.fiscal_year_end(D(2026, 11, 19), 4) == D(2027, 3, 31)
    assert gt.fiscal_year_end(D(2026, 11, 19), 1) == D(2026, 12, 31)
    assert gt.fiscal_year_end(D(2026, 3, 31), 4) == D(2026, 3, 31)


def test_a_year_total_offers_last_year_heading_and_stretch() -> None:
    chips = gt.candidates(
        INDEX, ROWS, "rev", aggregate="fiscal_year_total", direction="at_least", by=D(2027, 3, 31)
    )
    by = {c.kind: c.value for c in chips}
    assert by["last_year"] == Decimal("120")  # FY26: 12 months at 10
    assert by["heading"] == Decimal("144")  # 7 months at 12 so far + 5 forecast at 12
    assert by["stretch"] == Decimal("154")  # the band's upper edge: 84 + 5 x 14
    assert [c.kind for c in chips] == ["last_year", "heading", "stretch"]


def test_at_most_stretches_down_and_a_level_reads_one_month() -> None:
    low = gt.candidates(
        INDEX, ROWS, "rev", aggregate="fiscal_year_total", direction="at_most", by=D(2027, 3, 31)
    )
    assert {c.kind: c.value for c in low}["stretch"] == Decimal("139")  # 84 + 5 x 11
    lvl = gt.candidates(
        INDEX, ROWS, "rev", aggregate="value", direction="at_least", by=D(2027, 3, 31)
    )
    by = {c.kind: c.value for c in lvl}
    assert by["last_year"] == Decimal("10") and by["heading"] == Decimal("12")


def test_no_forecast_means_only_last_year() -> None:
    chips = gt.candidates(
        INDEX, [], "rev", aggregate="fiscal_year_total", direction="at_least", by=D(2027, 3, 31)
    )
    assert [c.kind for c in chips] == ["last_year"]
    assert (
        gt.candidates(
            SeriesIndex([]), [], "rev", aggregate="value", direction="at_least", by=D(2027, 3, 31)
        )
        == []
    )


def test_a_quoted_target_comes_first() -> None:
    chips = gt.candidates(
        INDEX,
        ROWS,
        "rev",
        aggregate="fiscal_year_total",
        direction="at_least",
        by=D(2027, 3, 31),
        quoted=Decimal("150"),
    )
    assert chips[0].kind == "file" and chips[0].value == Decimal("150")


def test_round_keeps_three_figures() -> None:
    assert gt.nice(Decimal("572345678.9")) == Decimal("572000000")
    assert gt.nice(Decimal("0.2134")) == Decimal("0.213")
    assert gt.nice(Decimal("41")) == Decimal("41")


@pytest.mark.parametrize(
    ("unit", "text", "want"),
    [
        ("inr", "60 Cr", "600000000"),
        ("inr", "₹ 4.5 L", "450000"),
        ("inr", "60,00,00,000", "600000000"),
        ("inr", "2.5M", "2500000"),
        ("inr", "1.2 bn", "1200000000"),
        ("inr", "75k", "75000"),
        ("percent", "21%", "0.21"),
        ("percent", "21", "0.21"),
        ("days", "45 d", "45"),
        ("metres", "50,000", "50000"),
        (None, "3.5", "3.5"),
    ],
)
def test_a_typed_target_is_read_in_its_unit(unit: str | None, text: str, want: str) -> None:
    assert values.parse(unit, text) == Decimal(want)


@pytest.mark.parametrize("text", ["", "lots", "60 Crores of", "-", "1e400"])
def test_a_target_that_isnt_a_number_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        values.parse("inr", text)


def test_code_suggests_a_goal_only_where_higher_is_plainly_better():
    from othor.ontology.goal_suggest import plainly_higher_good, summable

    base = {"kind": "flow", "type": "base", "unit": "inr"}
    assert plainly_higher_good("net_revenue", {**base, "name": "Net revenue"})
    assert not plainly_higher_good("cogs", {**base, "name": "Cost of goods"})
    assert not plainly_higher_good("dso", {"kind": "flow", "type": "derived", "unit": "days"})
    # a worked-out number or a level is a goal on its value, never a year's total
    assert not summable({"kind": "flow", "type": "derived", "unit": "days"})
    assert summable({"kind": "flow", "type": "derived", "unit": "inr"})  # net revenue
    assert not summable({"kind": "snapshot", "type": "base", "unit": "inr"})
    assert not summable({"kind": "flow", "type": "base", "unit": "percent"})
    assert summable(base)
