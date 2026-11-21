# SPDX-License-Identifier: Apache-2.0
"""Golden SVGs for every chart kind on 9c; byte-identical across runs (docs/12)."""

import os
from html import escape
from pathlib import Path

import pytest

from othor.charts.svg import render_svg
from othor.web.components_demo import demo_specs

GOLDEN = Path(__file__).parent / "golden"
SPECS = demo_specs()
SIZES = {
    "sparkline_below": (120, 36),
    "sparkline_neutral": (120, 36),
    "sparkline_on_track": (120, 36),
    "bars_contribution": (380, 110),
    "bars_share": (300, 90),
    "sparkline_marks": (1000, 76),
}


@pytest.mark.parametrize("name", sorted(SPECS))
def test_golden(name: str) -> None:
    svg = render_svg(SPECS[name], SIZES.get(name, (400, 160)))
    path = GOLDEN / f"{name}.svg"
    if os.environ.get("OTHOR_UPDATE_GOLDEN") == "1" or not path.exists():
        path.write_text(svg)
    assert svg == path.read_text(), (
        f"{name} changed; review it and rerun with OTHOR_UPDATE_GOLDEN=1"
    )


@pytest.mark.parametrize("name", sorted(SPECS))
def test_deterministic_no_green_and_alt_text(name: str) -> None:
    a, b = render_svg(SPECS[name]), render_svg(SPECS[name])
    assert a == b
    assert "#1B8A4A" not in a.upper()  # green is for status dots only, never data
    assert escape(SPECS[name].alt_text) in a
