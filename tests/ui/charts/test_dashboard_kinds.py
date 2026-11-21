# SPDX-License-Identifier: Apache-2.0
"""0.29 (Discover 2.0): the dashboard's chart kinds. Each round-trips through JSON, draws only
what its spec carries, and uses role colours (no green for data)."""

from __future__ import annotations

import re

import pytest

from othor.charts.png import render_png
from othor.charts.spec import ChartSpec
from othor.charts.svg import ROLE, render_svg
from othor.web.components_demo import demo_specs

NEW = ("stacked_share", "heatmap", "bullet", "small_multiples", "line_ahead")
S = demo_specs()


@pytest.mark.parametrize("name", NEW)
def test_round_trip_and_png(name: str) -> None:
    spec = S[name]
    again = ChartSpec.from_json(spec.to_json())
    assert render_svg(again) == render_svg(spec)
    assert render_png(spec)[:4] == b"\x89PNG"


def test_stacked_share_fills_each_bar_to_the_top() -> None:
    svg = render_svg(S["stacked_share"], (520, 200))
    heights = [float(h) for h in re.findall(r'<rect [^>]*height="([\d.]+)"', svg)]
    per_bar = len(S["stacked_share"].series)
    bars = [sum(heights[i : i + per_bar]) for i in range(0, len(heights), per_bar)]
    assert all(abs(b - (200 - 18 - 20)) < 0.1 for b in bars)
    assert ">29%<" in svg


def test_heatmap_draws_one_cell_per_value() -> None:
    svg = render_svg(S["heatmap"], (520, 160))
    cells = sum(len(s.points) for s in S["heatmap"].series)
    assert svg.count('rx="4"') == cells
    assert ">Fri<" in svg


def test_bullet_tints_the_bad_side_of_the_goal() -> None:
    svg = render_svg(S["bullet"], (300, 50))
    assert ">goal 45<" in svg and ">44<" in svg
    assert f'fill="{ROLE["negative"]}" fill-opacity="0.14"' in svg


def test_small_multiples_colour_only_the_one_to_look_at() -> None:
    svg = render_svg(S["small_multiples"], (300, 190))
    assert svg.count(f'stroke="{ROLE["negative"]}"') == 1
    assert svg.count(f'stroke="{ROLE["muted"]}"') == 2


def test_the_forecast_band_reaches_past_the_last_actual() -> None:
    svg = render_svg(S["line_ahead"], (760, 260))
    assert ">Jan<" in svg  # the axis runs to the band's end, three months ahead


# Forecasts 1.0 (F3): the line's horizon option ------------------------------------------------


def test_horizon_round_trips_and_draws_png() -> None:
    spec = S["line_horizon"]
    again = ChartSpec.from_json(spec.to_json())
    assert again == spec and render_svg(again) == render_svg(spec)
    assert render_png(spec)[:4] == b"\x89PNG"


def test_horizon_marks_where_it_can_see_and_runs_the_axis_to_its_end() -> None:
    svg = render_svg(S["line_horizon"], (760, 260))
    assert ">can see to here · Nov<" in svg and ">today<" in svg
    assert ">Mar<" in svg  # the axis runs past the band to the horizon's end (the year end)
    assert ">Assumption<" in svg  # the zone past the stored periods, shaded and named
    # the range past where it can see is drawn lighter than the likely range before it
    fills = re.findall(rf'fill="{ROLE["positive"]}" fill-opacity="([\d.]+)"', svg)
    assert fills[:2] == ["0.22", "0.1"]


def test_horizon_needs_its_date() -> None:
    from othor.charts.spec import Annotation, SpecError

    with pytest.raises(SpecError):
        ChartSpec(
            id="x",
            kind="line",
            title="x",
            alt_text="x",
            annotations=(Annotation("horizon", label="can see to here"),),
        )


def test_a_line_without_a_horizon_is_drawn_as_before() -> None:
    import dataclasses

    plain = dataclasses.replace(
        S["line_horizon"],
        annotations=tuple(a for a in S["line_horizon"].annotations if a.type != "horizon"),
    )
    svg = render_svg(plain, (760, 260))
    assert "can see to here" not in svg
    assert re.findall(rf'fill="{ROLE["positive"]}" fill-opacity="([\d.]+)"', svg)[:1] == ["0.22"]
