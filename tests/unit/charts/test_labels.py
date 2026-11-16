# SPDX-License-Identifier: Apache-2.0
"""Chart labels don't collide (M12, the 9c deferrals in docs/ui-review/gaps.md): at the sizes
9c and the brief use, no two text labels overlap, no label sits on an annotated point, whole-number
ticks print without decimals ("4 d", not "4.0 d"), and the "no invoices" box fits its label."""

from __future__ import annotations

import itertools
import re

import pytest

from othor.charts.svg import TEXT_W, render_svg
from othor.web.components_demo import demo_specs

SPECS = demo_specs()
SIZES = {
    "bars_contribution": (380, 96),
    "step": (400, 104),
    "bars_share": (300, 84),
    "gap_block": (400, 96),
    "line_spread": (400, 100),
    "line_decoupling": (400, 110),
    "line_forecast": (400, 100),
}
TEXT = re.compile(
    r'<text x="([-\d.]+)" y="([-\d.]+)" font-size="(\d+)" fill="[^"]+" '
    r'text-anchor="(\w+)"[^>]*>([^<]*)</text>'
)


def boxes(svg: str) -> list[tuple[str, float, float, float, float]]:
    out = []
    for x, y, size, anchor, s in TEXT.findall(svg):
        x, y, size = float(x), float(y), int(size)
        w = len(s) * size * TEXT_W
        x0 = {"start": x, "middle": x - w / 2, "end": x - w}[anchor]
        out.append((s, x0, y - size * 0.8, x0 + w, y + size * 0.2))
    return out


def overlaps(a: tuple, b: tuple) -> bool:  # type: ignore[type-arg]
    return a[1] < b[3] and b[1] < a[3] and a[2] < b[4] and b[2] < a[4]


@pytest.mark.parametrize("name", sorted(SIZES))
def test_no_two_labels_overlap(name: str) -> None:
    bx = boxes(render_svg(SPECS[name], SIZES[name]))
    bad = [(a[0], b[0]) for i, a in enumerate(bx) for b in bx[i + 1 :] if overlaps(a, b)]
    assert bad == []


@pytest.mark.parametrize("name", sorted(SIZES))
def test_labels_stay_inside_the_chart(name: str) -> None:
    w, h = SIZES[name]
    for s, x0, y0, x1, y1 in boxes(render_svg(SPECS[name], SIZES[name])):
        assert x0 >= -1 and x1 <= w + 1 and y0 >= -1 and y1 <= h + 1, (s, x0, y0, x1, y1)


def test_no_label_on_the_breach_point() -> None:
    svg = render_svg(SPECS["line_forecast"], SIZES["line_forecast"])
    (cx, cy) = map(float, re.search(r'<circle cx="([-\d.]+)" cy="([-\d.]+)" r="3.5"', svg).groups())  # type: ignore[union-attr]
    dot = ("•", cx - 4, cy - 4, cx + 4, cy + 4)
    assert [b[0] for b in boxes(svg) if overlaps(b, dot)] == []


def test_whole_number_ticks_have_no_decimals() -> None:
    svg = render_svg(SPECS["line_spread"], SIZES["line_spread"])
    ticks = [b[0] for b in boxes(svg) if b[0].endswith(" d") and not b[0].startswith("average")]
    assert ticks and all(".0 d" not in t for t in ticks), ticks


def test_the_gap_box_fits_its_label() -> None:
    svg = render_svg(SPECS["gap_block"], SIZES["gap_block"])
    rect = re.search(
        r'<rect x="([-\d.]+)" y="[-\d.]+" width="([-\d.]+)" height="[-\d.]+" fill="none"', svg
    )
    assert rect
    x, w = float(rect.group(1)), float(rect.group(2))
    (label,) = [b for b in boxes(svg) if b[0] == "no invoices"]
    assert label[1] >= x + 2 and label[3] <= x + w - 2


@pytest.mark.parametrize("name", ["line_spread", "line_decoupling", "line_forecast", "step"])
def test_no_label_sits_on_a_data_line(name: str) -> None:
    svg = render_svg(SPECS[name], SIZES[name])
    pts = []
    for poly in re.findall(r'<polyline points="([^"]+)"', svg):
        xy = [tuple(map(float, p.split(","))) for p in poly.split()]
        for (x0, y0), (x1, y1) in itertools.pairwise(xy):
            pts += [(x0 + (x1 - x0) * i / 10, y0 + (y1 - y0) * i / 10) for i in range(11)]
    bad = {
        b[0]
        for b in boxes(svg)
        for x, y in pts
        if b[1] + 1 < x < b[3] - 1 and b[2] + 1 < y < b[4] - 1
    }
    assert bad == set()


def test_the_last_month_at_the_right_edge_stays_inside() -> None:
    """6a2 (M12b): twelve months to the right edge; "Oct" was cut to "O"."""
    import datetime as dt

    from othor.charts.spec import ChartSpec, Period, Series, Unit

    months = [dt.date(2025 + (10 + i) // 12, (10 + i) % 12 + 1, 1) for i in range(12)]
    pts = tuple((d.isoformat(), 4.5 + (i % 3) * 0.2) for i, d in enumerate(months))
    spec = ChartSpec(
        id="t",
        kind="line",
        title="t",
        unit=Unit("currency", "INR", "cr", 2),
        period=Period("month", pts[0][0], pts[-1][0]),
        series=(Series("m", "Net revenue", "neutral", pts),),
        alt_text="t",
    )
    w, h = 600, 160
    labels = boxes(render_svg(spec, (w, h)))
    assert any(s == "Oct" for s, *_ in labels)
    for s, x0, _y0, x1, _y1 in labels:
        assert x0 >= -1 and x1 <= w + 1, (s, x0, x1)
