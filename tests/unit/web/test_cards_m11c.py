# SPDX-License-Identifier: Apache-2.0
"""Cards (M11c · A): brief cards and the Home Sweep card lead with their chart and sit on Deck
sheets; the Home Sweep card leads with the Sweep page's first finding that has a chart."""

from __future__ import annotations

import re
from typing import Any

import pytest

from othor.web import home_view
from othor.web.templating import templates

BRIEF = templates.env.from_string(
    '{% from "components/brief.html" import brief_card %}{{ brief_card(c) }}'
)
HOME = templates.env.from_string(
    '{% from "components/home.html" import home_card %}'
    '{{ home_card("Sweep · 8 findings", "t", "m", "/sweep",'
    " chart_spec=None, visual=True, count=n) }}"
)


def _brief(items: int, chart: Any = None) -> str:
    c = {
        "id": "b1",
        "name": "Leadership",
        "item_count": items,
        "chart": chart,
        "title": "South fell",
        "scope": "Whole company",
        "recipients": 1,
        "ready": True,
        "time": "06:00",
        "default": False,
        "ask": None,
    }
    return BRIEF.render(c=c)


@pytest.mark.parametrize(
    ("items", "sheets", "tag"), [(1, 0, None), (2, 1, "1 of 2"), (4, 2, "1 of 4")]
)
def test_a_brief_card_has_one_sheet_per_item_behind_it_up_to_two(
    items: int, sheets: int, tag: str | None
) -> None:
    html = _brief(items)
    assert html.count('class="stack-sheet') == sheets
    found = re.search(r'class="stack-count">([^<]+)<', html)
    assert (found.group(1) if found else None) == tag


def test_a_brief_card_without_a_chart_keeps_its_band() -> None:
    html = _brief(1)
    # the 184 px band stays (no layout jump), with the faint mark and a line in it (#29)
    assert '<div class="visual"><span class="quiet"><span class="mark"' in html


@pytest.mark.parametrize(("n", "sheets"), [(1, 0), (2, 1), (8, 2)])
def test_the_home_sweep_card_stacks_by_its_findings(n: int, sheets: int) -> None:
    html = HOME.render(n=n)
    assert html.count('class="stack-sheet') == sheets
    assert ("1 of 8" in html) is (n == 8)


def test_home_leads_with_the_sweep_pages_first_card_that_has_a_chart(monkeypatch) -> None:
    spread = {"fp": "a", "title": "Dispatch spread", "chart": object()}  # in today's brief
    group = {"fp": "b", "title": "14 customers silent", "chart": None}  # rows only
    orders = {"fp": "c", "title": "37 orders with no invoice", "chart": object()}
    ravira = {"fp": "d", "title": "Ravira Exports stopped invoicing", "chart": object()}
    monkeypatch.setattr(
        home_view.sv, "finding_cards", lambda rv: ([spread, group, orders], [ravira])
    )
    # the Sweep page shows briefed findings as one line: its first card is the 37 orders
    assert home_view._lead(None, {"a"})["lead"] == "37 orders with no invoice"
    # every charted finding briefed: the first charted one of all
    assert home_view._lead(None, {"a", "c", "d"})["lead"] == "Dispatch spread"
