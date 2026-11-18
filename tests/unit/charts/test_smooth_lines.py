# SPDX-License-Identifier: Apache-2.0
"""Smooth lines, values on lines, hover (v0.25 M25.1, owner 18 Nov 2026): every line chart is drawn
smooth by default, through every value and never past one; Key points carry their values (last,
highest, lowest, a finding's own; at most three a line and six a chart, left out where they don't
fit); a page shows each point's date and value on hover, a picture doesn't; Settings › Charts
turns each off, and the drawing is then the straight one again."""

from __future__ import annotations

import re

from othor.charts.png import render_png
from othor.charts.spec import Annotation, Axis, ChartSpec, Series, Unit
from othor.charts.svg import _beziers, render_svg
from othor.core import fmt
from othor.web.components_demo import demo_specs

MONTHS = [f"2025-{m:02d}-01" for m in range(1, 13)]
VALUES = [4.2, 4.5, 4.1, 4.9, 5.3, 5.0, 4.4, 4.6, 5.8, 5.1, 4.8, 5.2]


def _pts(vals: list[float]) -> tuple[tuple[str, float], ...]:
    return tuple(zip(MONTHS, vals, strict=True))


def _spec(**kw: object) -> ChartSpec:
    base: dict[str, object] = {
        "id": "t:line",
        "kind": "line",
        "title": "Invoice value by month",
        "alt_text": "Invoice value by month",
        "unit": Unit(type="currency", scale="cr", decimals=2, code="INR"),
        "x": Axis("time"),
        "series": (
            Series("v", "Invoice value", "neutral", tuple(zip(MONTHS, VALUES, strict=True))),
        ),
    }
    base.update(kw)
    return ChartSpec(**base)  # type: ignore[arg-type]


def test_smooth_by_default_and_never_past_a_value() -> None:
    svg = render_svg(_spec(), (480, 180))
    assert '<path d="M ' in svg and "<polyline" not in svg
    xy = [(float(i), v) for i, v in enumerate(VALUES)]
    for p0, c1, c2, p1 in _beziers(xy):
        lo, hi = min(p0[1], p1[1]), max(p0[1], p1[1])
        assert lo - 1e-9 <= c1[1] <= hi + 1e-9 and lo - 1e-9 <= c2[1] <= hi + 1e-9


def test_key_points_carry_their_values() -> None:
    svg = render_svg(_spec(), (480, 180))
    assert ">Invoice value · 5.20</text>" in svg  # the last rides with the line's name
    texts = re.findall(r">([\d.]+)</text>", svg)
    assert "5.80" in texts or "4.10" in texts  # the highest or the lowest, where they fit


def test_a_findings_point_is_a_key_point_and_the_caps_hold() -> None:
    spec = _spec(
        annotations=(Annotation(type="point", at=MONTHS[6], value=VALUES[6], role="negative"),),
        series=tuple(
            Series(
                f"s{k}",
                f"Line {k}",
                "neutral",
                tuple(zip(MONTHS, [v + k for v in VALUES], strict=True)),
            )
            for k in range(4)
        ),
    )
    svg = render_svg(spec, (640, 260))
    marks = svg.count('r="2.5"')
    assert 1 <= marks <= 6


def test_hover_on_pages_never_in_pictures() -> None:
    svg = render_svg(_spec(), (480, 180))
    assert svg.count('fill="transparent"><title>Invoice value · ') == 12
    assert "Invoice value · Mar 2025 · ₹4.10 Cr" in svg
    with fmt.chart_style(fmt.ChartStyle(hover=False)):
        assert 'fill="transparent"' not in render_svg(_spec(), (480, 180))
    assert render_png(_spec(), (480, 180))[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_settings_turn_each_off() -> None:
    with fmt.chart_style(fmt.ChartStyle(smooth=False, values="off", hover=False)):
        svg = render_svg(_spec(), (480, 180))
    assert "<polyline" in svg and '<path d="M ' not in svg
    assert ">Invoice value</text>" in svg and 'r="2.5"' not in svg


def test_a_step_chart_stays_stepped() -> None:
    svg = render_svg(demo_specs()["step"], (400, 104))
    assert "<polyline" in svg and 'fill="transparent"' not in svg


def test_a_company_without_choices_gets_the_defaults() -> None:
    class Co:
        pass

    assert fmt.style_of(Co()) == fmt.ChartStyle()
    co = Co()
    co.chart_smooth, co.chart_values = False, "off"  # type: ignore[attr-defined]
    assert fmt.style_of(co) == fmt.ChartStyle(smooth=False, values="off")
