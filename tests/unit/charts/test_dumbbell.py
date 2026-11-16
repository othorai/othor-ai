# SPDX-License-Identifier: Apache-2.0
"""The dumbbell (owner, 10 Nov 2026): two series over the same categories, one row each."""

import pytest

from othor.charts.spec import Annotation, Axis, ChartSpec, Series, SpecError, Unit
from othor.charts.svg import render_svg


def _spec(**kw: object) -> ChartSpec:
    base = dict(
        id="d",
        kind="dumbbell",
        title="Pages",
        alt_text="Server time per page at two sizes",
        unit=Unit(type="ms"),
        x=Axis(type="category"),
        series=(
            Series("a", "small", "positive", (("Home", 15.8), ("Sweep", 104.1))),
            Series("b", "big", "positive", (("Home", 14.1), ("Sweep", 105.0))),
        ),
        annotations=(Annotation(type="threshold", value=300, label="300 ms budget"),),
    )
    return ChartSpec(**{**base, **kw})  # type: ignore[arg-type]


def test_needs_two_series_over_the_same_categories() -> None:
    one = (Series("a", "small", "positive", (("Home", 1.0),)),)
    with pytest.raises(SpecError, match="dumbbell"):
        _spec(series=one)
    other = (
        Series("a", "small", "positive", (("Home", 1.0),)),
        Series("b", "big", "positive", (("Sweep", 1.0),)),
    )
    with pytest.raises(SpecError, match="dumbbell"):
        _spec(series=other)


def test_renders_each_row_with_both_dots_and_the_second_value() -> None:
    svg = render_svg(_spec(), (640, 300))
    assert svg == render_svg(_spec(), (640, 300))  # deterministic
    assert svg.count("<circle") == 4
    assert ">Home<" in svg and ">Sweep<" in svg
    assert "105 ms" in svg and "14 ms" in svg  # the second series is labelled
    assert "300 ms budget" in svg and 'stroke-dasharray="3 3"' in svg


def test_narrow_puts_names_above_the_dots() -> None:
    wide, narrow = render_svg(_spec(), (640, 300)), render_svg(_spec(), (340, 400))
    assert 'x="0" y=' in wide and 'x="16" y=' in narrow  # names inset above, not in a column


def test_json_round_trip() -> None:
    assert ChartSpec.from_json(_spec().to_json()) == _spec()
