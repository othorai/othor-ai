# SPDX-License-Identifier: Apache-2.0
"""#59 (14 Nov 2026): a new company's brief cards on Home were blank but for a dot: the card
drew a one-month spark, a line with a single point. A card skips a chart it can't draw as a line
and says it is building history instead."""

from __future__ import annotations

from othor.charts.spec import ChartSpec, Series
from othor.engine.finding_charts import dump
from othor.web.brief_view import lead_chart


def _spark(n: int) -> str:
    pts = tuple((f"2026-{m:02d}-01", float(m)) for m in range(10 - n + 1, 11))
    return dump(
        ChartSpec(
            id=f"tile:x:{n}",
            kind="sparkline",
            title="x",
            alt_text=f"x, last {n} months",
            series=(Series("x", "x", "neutral", pts),),
        )
    )


def test_a_one_point_spark_is_not_a_card_chart():
    one = [{"title": "x"}]  # a finding with no chart of its own
    assert lead_chart({"items": one, "tiles": [{"chart": _spark(1)}]}) is None
    got = lead_chart({"items": one, "tiles": [{"chart": _spark(1)}, {"chart": _spark(3)}]})
    assert got is not None and len(got.series[0].points) == 3
    assert lead_chart({"items": [{"chart": _spark(2)}], "tiles": []}) is not None


def test_a_quiet_card_shows_the_quiet_day_mark():
    """#64 (owner, 14 Nov 2026): on a quiet day every brief card shows the Quiet day mark; the
    headline tile's spark is no longer the card's chart."""
    tiles = [{"chart": _spark(3)}]
    assert lead_chart({"items": [], "tiles": tiles}) is None
    assert lead_chart({"items": [{"chart": _spark(2)}], "tiles": tiles}, quiet=True) is None
    # a day with findings keeps the top finding's chart, else the tile's
    assert lead_chart({"items": [{"chart": _spark(2)}], "tiles": tiles}) is not None
    assert lead_chart({"items": [{"title": "x"}], "tiles": tiles}) is not None
