# SPDX-License-Identifier: Apache-2.0
"""Suppression proposals (docs/04 §10): a finding on the same calendar window in two or more
years is proposed once as a known pattern."""

from __future__ import annotations

import datetime as dt

from othor.engine.proposals import recurring

F = {
    "detector": "changepoint",
    "series": ["net_revenue|week||"],
    "entities": [],
    "state": "assessed",
    "window": ["2026-12-26", "2026-12-31"],
}


def _then(year, start):
    return (dt.date(year, 12, 31), {**F, "window": [start, f"{year}-12-31"]})


def test_the_same_window_in_an_earlier_year_recurs():
    run = dt.date(2027, 1, 1)
    assert recurring(F, run, [_then(2025, "2025-12-27")]) == [2025, 2026]
    assert recurring(F, run, [_then(2025, "2025-11-01")]) == [2026]  # another window
    other = (dt.date(2025, 12, 31), {**F, "series": ["cash|day||"], "window": ["2025-12-27", "x"]})
    assert recurring(F, run, [other]) == [2026]  # another pattern
    same = (dt.date(2026, 12, 28), {**F, "window": ["2026-12-26", "x"]})
    assert recurring(F, run, [same]) == [2026]  # this year's own nights don't count twice
