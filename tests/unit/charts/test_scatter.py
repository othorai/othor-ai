# SPDX-License-Identifier: Apache-2.0
"""The scatter (17 Nov 2026): values by category, each column's points in value order, a named
point drawn dark once with its label; the same bytes every time."""

from __future__ import annotations

from othor.charts.spec import Annotation, Axis, ChartSpec, Series
from othor.charts.svg import render_svg

SPEC = ChartSpec(
    id="t:scatter",
    kind="scatter",
    title="Value per customer",
    alt_text="A3 stands out in East.",
    x=Axis("category"),
    series=(
        Series(
            "c",
            "Customers",
            "neutral",
            (("East", 1.0), ("East", 1.2), ("East", 3.4), ("West", 0.9), ("West", 1.1)),
            style="dots",
        ),
    ),
    annotations=(Annotation("point", at="East", value=3.4, label="A3", role="edge"),),
)


def test_the_scatter_draws_each_point_once_and_names_the_outlier() -> None:
    a = render_svg(SPEC, (480, 200))
    assert a == render_svg(SPEC, (480, 200))
    assert a.count("<circle") == 5 and ">A3<" in a
    assert ">East<" in a and ">West<" in a
