# SPDX-License-Identifier: Apache-2.0
"""The waterfall (M15.3, the Test tab): a total, each part's move from the running level, the
next total; drawn from the ChartSpec alone."""

from __future__ import annotations

import re

from othor.charts.spec import Axis, Category, ChartSpec, Unit
from othor.charts.svg import render_svg


def _spec() -> ChartSpec:
    cats = (
        Category("Sep total", 8.84, "neutral", total=True),
        Category("West", 1.20, "positive"),
        Category("South", -1.40, "negative"),
        Category("Oct total", 8.64, "neutral", total=True),
    )
    return ChartSpec(
        id="test:x",
        kind="waterfall",
        title="How the total moved",
        unit=Unit("currency", "INR", "cr", 2),
        x=Axis("category"),
        categories=cats,
        alt_text="x",
    )


def test_the_parts_lead_from_one_total_to_the_next():
    c = _spec().categories
    assert round(c[0].value + sum(x.value for x in c[1:-1]), 2) == c[-1].value


def test_it_draws_a_bar_per_category_with_its_value():
    svg = render_svg(_spec(), (620, 220))
    assert svg.count("<rect") >= 4
    for text in ("Sep total", "West", "South", "Oct total", "+1.20", "−1.40", "8.84", "8.64"):
        assert text in svg, text
    # the axis starts above zero so the totals keep their height, and says so
    assert re.search(r"axis from ", svg)
