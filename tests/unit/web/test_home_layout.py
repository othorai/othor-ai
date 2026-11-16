# SPDX-License-Identifier: Apache-2.0
"""Home's briefs row and Sweep box (owner's design B2, 11 Nov 2026): briefs that need you first,
at most two rows (six cards), the sixth grouping the rest; Sweep's top three findings by money."""

from __future__ import annotations

from decimal import Decimal

from othor.web import home_view


def _b(name: str, count: int, quiet: bool = False) -> dict:
    return {"name": name, "count": count, "quiet": quiet, "order": 0}


def test_briefs_that_need_you_come_first_and_six_is_the_most() -> None:
    cards = [_b("A", 0, True), _b("B", 2), _b("C", 1), _b("D", 0, True), _b("E", 3)]
    row = home_view.brief_row(cards)
    assert [c["name"] for c in row["cards"]] == ["E", "B", "C", "A", "D"]
    assert row["more"] is None


def test_more_than_six_shows_five_and_groups_the_rest() -> None:
    cards = [_b(f"N{i}", i % 3) for i in range(9)]
    row = home_view.brief_row(cards)
    assert len(row["cards"]) == 5
    more = row["more"]
    assert more["n"] == 4 and more["href"] == "/brief"
    assert more["need"] == sum(1 for c in row["rest"] if c["count"])


def test_six_briefs_fit_without_grouping() -> None:
    row = home_view.brief_row([_b(f"N{i}", 1) for i in range(6)])
    assert len(row["cards"]) == 6 and row["more"] is None


def test_the_sweep_box_takes_the_top_three_by_money() -> None:
    def card(fp: str, money: str) -> dict:
        return {"fp": fp, "money": f"₹{money}", "raw": Decimal(money), "title": fp}

    new = [card("a", "1.9"), card("c", "0.91")]
    old = [card("b", "1.4"), card("d", "0.2")]
    box = home_view.sweep_box_rows(new, old, found=8, n_new=2, in_briefs={"b"})
    assert [r["fp"] for r in box["rows"]] == ["a", "b", "c"]
    assert box["rows"][1]["in_brief"] is True
    assert box["more"] == 5
    assert box["top"] == Decimal("4.21")
