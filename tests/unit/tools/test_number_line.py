# SPDX-License-Identifier: Apache-2.0
"""0.27.3 (an outside AI charted balances beside period totals, 19 Nov 2026): each number in the
numbers tool says what it is, its period and its change, as the brief's tile computed them."""

from __future__ import annotations

import datetime as dt

from othor.tools.read import number_line

RUN = dt.date(2026, 11, 19)


def _row(kind: str, grain: str, period: str, comp: dict | None = None, value: str = "₹4.77 Cr"):  # type: ignore[no-untyped-def]
    return {
        "today": value,
        "kind": kind,
        "tile": {
            "period": period,
            "grain": grain,
            "value": value,
            "comparison": comp or {"kind": "history", "text": "3 months of history"},
            "stale": None,
        },
    }


def test_a_total_says_its_period_and_change() -> None:
    r = _row("flow", "month", "2026-11-01", {"kind": "period", "text": "+3% vs Oct"})
    assert number_line(r, RUN) == "₹4.77 Cr · total for Nov 2026 so far · +3% vs Oct"
    r = _row("flow", "month", "2026-10-01", {"kind": "period", "text": "−2% vs Sep"})
    assert number_line(r, RUN) == "₹4.77 Cr · total for Oct 2026 · −2% vs Sep"


def test_a_balance_says_its_day() -> None:
    r = _row("snapshot", "day", "2026-11-18", value="₹2.01 Cr")
    assert number_line(r, RUN) == "₹2.01 Cr · balance on 18 Nov"


def test_a_worked_out_number_and_one_without_data() -> None:
    r = _row("derived", "week", "2026-11-16", {"kind": "band", "text": "in band"}, value="41 d")
    assert number_line(r, RUN) == "41 d · for the week of 16 Nov · in band"
    assert number_line({"today": "No data yet", "tile": None}, RUN) == "No data yet"
