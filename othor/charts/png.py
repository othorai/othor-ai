# SPDX-License-Identifier: Apache-2.0
"""render_png(spec, size) -> PNG bytes for email: the SVG rasterised at 2× by resvg, with the
bundled Inter/Geist Mono fonts only (no system fonts), so output doesn't vary by machine."""

from __future__ import annotations

from pathlib import Path

import resvg_py  # type: ignore[import-untyped]

from othor.charts.spec import ChartSpec
from othor.charts.svg import render_svg

FONTS = Path(__file__).resolve().parents[1] / "web" / "static" / "fonts" / "ttf"


def render_png(
    spec: ChartSpec, size: tuple[int, int] = (400, 160), scale: int = 2, *, legend: bool = False
) -> bytes:
    import dataclasses

    from othor.core import fmt

    # a picture can't hover: the same drawing without the hover targets (v0.25)
    with fmt.chart_style(dataclasses.replace(fmt.charts(), hover=False)):
        svg = render_svg(spec, size, legend=legend)
    out = resvg_py.svg_to_bytes(
        svg_string=svg,
        zoom=scale,
        background="#ffffff",
        skip_system_fonts=True,
        font_files=sorted(str(p) for p in FONTS.glob("*.ttf")),
        font_family="Inter",
        sans_serif_family="Inter",
    )
    return bytes(out)
