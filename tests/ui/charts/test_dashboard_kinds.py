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
