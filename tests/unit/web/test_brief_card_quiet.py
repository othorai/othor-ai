# SPDX-License-Identifier: Apache-2.0
"""#29: a brief card with nothing to chart (a quiet day, or no issue yet) shows the faint Othor AI
mark and a short line, as Home's cards do (#21), never an empty box."""

from __future__ import annotations

import datetime as dt

from othor.web.templating import templates


def _card(**kw):
    return {
        "id": "b1",
        "name": "Leadership",
        "default": True,
        "title": "Nothing needs you today.",
        "item_count": 0,
        "quiet": True,
        "scope": "Whole company",
        "recipients": 1,
        "ask": "",
        "time": "06:00",
        "ready": True,
        "chart": None,
        **kw,
    }


def _render(card):
    mod = templates.env.get_template("components/brief.html").make_module({})
    return str(mod.brief_card(card))


def test_a_quiet_card_shows_the_mark_and_quiet_day():
    html = _render(_card())
    assert 'class="quiet"' in html and 'class="mark"' in html and "Quiet day" in html


def test_a_card_with_no_issue_yet_says_when_the_first_comes():
    html = _render(_card(quiet=False, ready=False, title="", time=dt.time(6, 0)))
    assert 'class="mark"' in html and "First issue at 6:00 AM" in html
