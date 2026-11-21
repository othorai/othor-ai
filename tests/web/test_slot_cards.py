# SPDX-License-Identifier: Apache-2.0
"""Home's Suggested by Othor AI (owner, 20 Nov 2026), the core's half: `slot_cards` interleaves
the cards plug-ins offer (the first plug-in first) up to three, counts them all, adds a plug-in's
"more" link when it has more than it got, carries what an action just did as a toast, and is None
when nothing is offered; Home's section draws it and is absent without it. No plug-in is named."""

from __future__ import annotations

from typing import Any

import pytest
from jinja2 import ChoiceLoader, DictLoader

from othor.core import plugins
from othor.core.plugins import Fragment
from othor.web.templating import templates

CARD = '<div class="brief-card" data-row="{{ item }}">{{ item }}</div>'


@pytest.fixture
def two(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    offered: dict[str, Any] = {"a": None, "b": None}
    frags = (
        Fragment("home.suggested", "a_card.html", context=lambda ctx: offered["a"]),
        Fragment("home.suggested", "b_card.html", context=lambda ctx: offered["b"]),
    )
    monkeypatch.setattr(
        plugins, "fragments", lambda slot: frags if slot == "home.suggested" else ()
    )
    loader = ChoiceLoader(
        [DictLoader({"a_card.html": CARD, "b_card.html": CARD}), templates.env.loader]
    )  # type: ignore[list-item]
    monkeypatch.setattr(templates.env, "loader", loader)
    return offered


def _cards(**ctx: Any) -> Any:
    return templates.env.from_string(
        '{% set sg = slot_cards("home.suggested") %}{{ sg | tojson if sg else "none" }}'
    ).render(**ctx)


def _section(**ctx: Any) -> str:
    src = (
        '{% from "components/home.html" import suggested_section %}'
        '{% set sg = slot_cards("home.suggested") %}'
        '{% if sg %}{{ suggested_section(sg, "tok") }}{% endif %}'
    )
    return templates.env.from_string(src).render(**ctx)


def _more(name: str) -> dict[str, str]:
    return {"label": f"All suggestions in {name} →", "short": f"In {name} →", "href": f"/{name}"}


def test_interleaved_first_plugin_first_up_to_three(two: dict[str, Any]) -> None:
    two["a"] = {"items": ["a1", "a2", "a3"], "total": 5, "more": _more("A")}
    two["b"] = {"items": ["b1", "b2"], "total": 2, "more": _more("B")}
    page = _section()
    assert page.index('data-row="a1"') < page.index('data-row="b1"') < page.index('data-row="a2"')
    assert 'data-row="b2"' not in page and 'data-row="a3"' not in page
    assert "Suggested by Othor AI · 7" in page
    assert "From last night’s results · nothing changes until you choose" in page
    # each plug-in with more than it got has its link; the first says "All suggestions"
    assert "All suggestions in A →" in page and "In B →" in page
    assert page.index("All suggestions in A →") < page.index("In B →")


def test_one_plugin_alone_and_no_link_when_all_shown(two: dict[str, Any]) -> None:
    two["b"] = {"items": ["b1", "b2"], "total": 2, "more": _more("B")}
    page = _section()
    assert (
        'data-row="b1"' in page and 'data-row="b2"' in page and "Suggested by Othor AI · 2" in page
    )
    assert "In B →" not in page and "All suggestions in B →" not in page


def test_nothing_offered_no_section(two: dict[str, Any]) -> None:
    assert _cards() == "none" and _section().strip() == ""
    two["a"] = {"items": [], "total": 0, "more": _more("A")}
    assert _cards() == "none"


def test_a_toast_shows_even_with_no_card_left(two: dict[str, Any]) -> None:
    two["a"] = {
        "items": [],
        "total": 0,
        "toast": {"text": "Gone.", "action": "/a/undo", "label": "Undo"},
    }
    page = _section()
    assert 'aria-label="Suggested by Othor AI"' not in page
    assert 'class="toast-dock"' in page and "Gone." in page and 'action="/a/undo"' in page


def test_a_failing_plugin_never_breaks_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(ctx: Any) -> dict[str, Any]:
        raise RuntimeError("down")

    frags = (Fragment("home.suggested", "x.html", context=boom),)
    monkeypatch.setattr(plugins, "fragments", lambda slot: frags)
    assert _cards() == "none"
