# SPDX-License-Identifier: Apache-2.0
"""PNG images for emails (docs/09): the logo and the charts, rendered on the server from the
same ChartSpec as the page (`render_png`), attached inline (cid:), never hosted.

Each chart's alt text is the spec's own, which carries its computed numbers. An item's chart in
the email is a sparkline of its subject series (line, step), or its own chart drawn small (bars,
gap); an item with no chart by design (missing records) has none.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from othor.charts.png import render_png
from othor.charts.spec import ChartSpec
from othor.engine.finding_charts import load as load_chart
from othor.mail.relay import InlineImage

ICONS = Path(__file__).resolve().parents[1] / "web" / "templates" / "components" / "icons.html"
LOGO_CID = "logo@othor.ai"
SPARK = (132, 40)
SMALL = (200, 64)


@lru_cache(maxsize=1)
def _logo_png() -> bytes:
    import resvg_py  # type: ignore[import-untyped]

    m = re.search(r"\{% macro logo\(\) -%\}\s*(<svg.*?</svg>)", ICONS.read_text(), re.S)
    if m is None:  # pragma: no cover - the macro is part of the design system
        raise RuntimeError("logo macro not found")
    svg = m.group(1).replace(
        'role="img" aria-label="Othor AI" ', 'xmlns="http://www.w3.org/2000/svg" '
    )
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, zoom=2, background="#ffffff"))


def logo() -> InlineImage:
    return InlineImage(LOGO_CID, _logo_png(), "othor-ai.png")


COMPANY_LOGO_CID = "company-logo@othor.ai"
COMPANY_LOGO_H = 24


def company_logo(png: bytes | None, imgs: list[InlineImage]) -> dict[str, Any] | None:
    """The company's logo in the email header (M15.7): a square mark, attached inline, drawn
    24 × 24 beside the company's name."""
    if not png:
        return None
    imgs.append(InlineImage(COMPANY_LOGO_CID, png, "logo.png"))
    return {"cid": COMPANY_LOGO_CID, "w": COMPANY_LOGO_H, "h": COMPANY_LOGO_H}


def _cid(spec: ChartSpec, size: tuple[int, int]) -> str:
    h = hashlib.sha256(f"{spec.id}|{size}".encode()).hexdigest()[:16]
    return f"chart-{h}@othor.ai"


def email_spec(spec: ChartSpec) -> tuple[ChartSpec, tuple[int, int]]:
    """The item's chart as the email row shows it."""
    if spec.kind in ("line", "step", "sparkline") and spec.series:
        pick = (
            [s for s in spec.series if s.emphasis]
            or [s for s in spec.series if s.role != "neutral"]
            or list(spec.series)
        )[0]
        band = spec.band if spec.band and spec.band.series_id == pick.id else None
        return (
            dataclasses.replace(spec, kind="sparkline", series=(pick,), band=band, annotations=()),
            SPARK,
        )
    return spec, SMALL


@dataclasses.dataclass(frozen=True)
class EmailChart:
    cid: str
    alt: str
    width: int
    height: int
    image: InlineImage


def ahead_spec(spec: ChartSpec) -> ChartSpec:
    """What's ahead (M19.4) as an email sparkline: the actuals run on into the forecast as one
    line, its likely range shaded, so the row shows where the number has been and is heading."""
    actual = next((s for s in spec.series if s.id == "actual"), None)
    fc = next((s for s in spec.series if s.id == "forecast"), None)
    if actual is None or fc is None:
        return spec
    pts = actual.points + tuple(p for p in fc.points if p[0] > actual.points[-1][0])
    one = dataclasses.replace(fc, points=pts, style="line")
    return dataclasses.replace(spec, kind="sparkline", series=(one,), annotations=())


def spec_png(spec: ChartSpec, size: tuple[int, int], *, legend: bool = False) -> EmailChart:
    """A ChartSpec as an inline PNG at a set size (Big Picture's cards and range bars, M19.5)."""
    cid = _cid(spec, size)
    png = render_png(spec, size, legend=legend)
    return EmailChart(
        cid, spec.alt_text, size[0], size[1], InlineImage(cid, png, f"{cid[:22]}.png")
    )


AHEAD_BESIDE_CARD = (260, 112)  # What's ahead's line beside its forecast card (16 Nov 2026)


def chart(
    raw: str | None, *, ahead: bool = False, size: tuple[int, int] | None = None
) -> EmailChart | None:
    """A stored ChartSpec (JSON) → an inline PNG for an email, or None (no chart by design)."""
    if not raw:
        return None
    spec, size = (
        (ahead_spec(load_chart(raw)), size or SPARK) if ahead else email_spec(load_chart(raw))
    )
    cid = _cid(spec, size)
    png = render_png(spec, size)
    return EmailChart(
        cid, spec.alt_text, size[0], size[1], InlineImage(cid, png, f"{cid[:22]}.png")
    )


# --- business weather (v0.12 part 1c) -----------------------------------------------------------

WEATHER = Path(__file__).resolve().parent / "assets" / "weather"
HEADER = (600, 120)  # the 1200 × 240 scene, shown at 2×
ICON = 32  # the 96 × 96 icon, shown small in the outlook rows


@lru_cache(maxsize=32)
def _asset(kind: str, file: str) -> bytes:
    return (WEATHER / kind / f"{file}.png").read_bytes()


def weather_header(file: str) -> InlineImage:
    """The brief's scene as the email's header picture (the owner's 1200 × 240 PNG)."""
    return InlineImage(f"wx-{file}@othor.ai", _asset("header", file), f"wx-{file}.png")


def weather_icon(file: str) -> InlineImage:
    """A scene's icon for an outlook row (the 96 × 96 PNG; email clients don't show SVG)."""
    return InlineImage(f"wx-icon-{file}@othor.ai", _asset("icons", file), f"wx-icon-{file}.png")
