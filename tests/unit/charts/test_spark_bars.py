# SPDX-License-Identifier: Apache-2.0
"""A sparkline of bars (M15.8, the AI usage card): one bar per point from a zero baseline, the
tallest the full height; a zero is a hairline so the day still shows."""

from __future__ import annotations

import re

from othor.charts.spec import ChartSpec, Series
from othor.charts.svg import render_svg


def _spec(values):
    pts = tuple(
        (f"2026-11-{d:02d}", v) for d, v in zip(range(8, 8 + len(values)), values, strict=True)
    )
    return ChartSpec(
        id="usage-7d",
        kind="sparkline",
        title="Tokens, last 7 days",
        alt_text="Tokens per day",
        series=(Series("tokens", "Tokens", "neutral", pts, style="bar"),),
    )


def test_one_bar_per_day_the_tallest_full_height():
    svg = render_svg(_spec([0, 100, 400, 50, 0, 0, 200]), (140, 28))
    heights = [float(h) for h in re.findall(r'<rect[^>]*height="([0-9.]+)"', svg)]
    assert len(heights) == 7
    assert max(heights) == heights[2] and heights[2] > heights[1] > heights[3]
    assert min(heights) > 0  # a zero day is a hairline, not nothing
    assert "polyline" not in svg
    assert render_svg(_spec([0, 100, 400, 50, 0, 0, 200]), (140, 28)) == svg  # same bytes


def test_all_zero_is_seven_hairlines():
    svg = render_svg(_spec([0] * 7), (140, 28))
    assert len(re.findall(r"<rect", svg)) >= 7
