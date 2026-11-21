# SPDX-License-Identifier: Apache-2.0
"""Forecasts 1.0, F1 (owner, 21 Nov 2026): each number forecast 12 periods ahead for the Forecasts
page, from the same fit: its first periods are exactly the forecast every other reader uses, so the
brief, Sweep and the goals don't move; the run stores the 12 under `horizon`."""

from __future__ import annotations

import datetime as dt

from othor.engine.forecast import store as fstore


def _weeks(n: int) -> list[dt.date]:
    return [dt.date(2026, 1, 5) + dt.timedelta(weeks=i) for i in range(n)]


def _months(n: int) -> list[dt.date]:
    out, d = [], dt.date(2023, 1, 1)
    for _ in range(n):
        out.append(d)
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


def test_a_longer_forecast_starts_with_the_same_numbers():
    for p, grain in ((_weeks(40), "week"), (_months(30), "month")):
        v = [100.0 + 2 * i + (5 if i % 4 == 0 else -1) for i in range(len(p))]
        short, _ = fstore.forecast_series(p, v, grain, 4, mask=[False] * len(p), band=True)
        long, _ = fstore.forecast_series(
            p, v, grain, fstore.PAGE_AHEAD, mask=[False] * len(p), band=True
        )
        assert short is not None and long is not None
        assert len(long.points) == fstore.PAGE_AHEAD == 12
        assert long.points[:4] == short.points
        assert long.method == short.method


def test_horizon_rows_are_stored_in_order_with_their_range():
    p = _weeks(40)
    v = [100.0 + 2 * i + (5 if i % 4 == 0 else -1) for i in range(40)]
    long, _ = fstore.forecast_series(p, v, "week", 12, mask=[False] * 40, band=True)
    assert long is not None
    rows = fstore.horizon_rows(long)
    assert [r["h"] for r in rows] == list(range(1, 13))
    assert rows[0]["period"] == "2026-10-12"  # the week after the last one
    for r in rows:
        assert float(r["p10"]) <= float(r["p50"]) <= float(r["p90"])
    # the range widens the further ahead
    assert float(rows[-1]["p90"]) - float(rows[-1]["p10"]) > float(rows[0]["p90"]) - float(
        rows[0]["p10"]
    )
