# SPDX-License-Identifier: Apache-2.0
"""#21: on a quiet day Home's brief and Sweep cards had an empty white area where the chart goes.
With nothing to chart, the area shows the Othor AI mark, faint, and a short line instead."""

from __future__ import annotations

from typing import Any

from othor.web.templating import templates

CARD = '{% from "components/home.html" import home_card %}{{ home_card(**kw) }}'


def _card(**kw: Any) -> str:
    return templates.env.from_string(CARD).render(kw=kw)


def test_a_card_with_nothing_to_chart_shows_the_quiet_mark() -> None:
    html = _card(
        name="Leadership brief",
        title="Nothing needs you today.",
        meta="Quiet day",
        href="/brief/x",
        visual=True,
        quiet="Quiet day",
    )
    visual = html.split('<div class="visual">', 1)[1].split('<div class="body">', 1)[0]
    assert 'class="quiet"' in visual and "Quiet day" in visual
    assert 'aria-hidden="true"' in visual  # the mark is decoration; the card's text says it


def test_a_card_without_the_visual_area_has_no_quiet_mark() -> None:
    html = _card(name="Leadership brief", title="x", meta="y", href="/brief/x", quiet="Quiet day")
    assert 'class="quiet"' not in html
