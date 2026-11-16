# SPDX-License-Identifier: Apache-2.0
"""The tile colour rule (owner, 11 Nov 2026; M15.3): red only for a clear move, outside the band
by more than the band's half-width, or a goal falling behind; just outside is dark with a ring;
in band is grey. Worked out from the stored tile, so the brief's stored body never changes."""

from __future__ import annotations

from othor.briefs.tiles import emphasis
from othor.charts.spec import Band, ChartSpec, Period, Series
from othor.engine.finding_charts import dump


def _tile(
    value: float, lo: float = 90, hi: float = 110, kind: str = "band", role: str = "negative"
):
    pts = (("2026-10-25", 100.0), ("2026-11-01", value))
    spec = ChartSpec(
        id="tile:x:week:2026-11-01",
        kind="sparkline",
        title="X",
        period=Period("week", "2026-10-25", "2026-11-01"),
        series=(Series("x", "X", role, pts),),
        band=Band("x", 0.8, (("2026-11-01", lo),), (("2026-11-01", hi),)),
        alt_text="x",
        provenance={},
    )
    return {"comparison": {"kind": kind, "text": "t"}, "role": role, "chart": dump(spec)}


def test_in_band_is_grey():
    assert emphasis(_tile(100, role="neutral")) == "in"


def test_just_outside_is_dark_with_a_ring():
    assert emphasis(_tile(112)) == "edge"  # 2 above, half-width 10
    assert emphasis(_tile(81)) == "edge"


def test_a_clear_move_is_red():
    assert emphasis(_tile(121)) == "clear"  # 11 above the band, more than its half-width
    assert emphasis(_tile(79)) == "clear"


def test_goals_and_known_patterns():
    assert emphasis({"comparison": {"kind": "goal", "text": "t"}, "role": "neutral"}) == "clear"
    assert emphasis({"comparison": {"kind": "goal", "text": "t"}, "role": "positive"}) == ""
    assert emphasis(_tile(130, role="muted")) == ""  # a known pattern explains it
    assert emphasis({"comparison": {"kind": "period", "text": "t"}, "role": "neutral"}) == ""
