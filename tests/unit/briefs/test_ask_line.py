# SPDX-License-Identifier: Apache-2.0
"""The Ask line never reads "for None" (found in M11a, fixed in M12): a person with no name
gives "Question to ask today" with no "for" chip, on the page and in both email formats."""

from __future__ import annotations

from types import SimpleNamespace as NS

import jinja2

from othor.web.templating import templates

ASK = {"text": "Have Ravira Exports moved orders elsewhere?", "to": None, "role": ""}


def _macro(person: str) -> str:
    t = templates.env.from_string(
        '{% from "components/blocks.html" import ask_today %}{{ ask_today(person, q) }}'
    )
    return t.render(person=person, q=ASK["text"])


def test_the_page_line_without_a_name() -> None:
    html = templates.env.from_string(
        '{% from "components/blocks.html" import ask_today %}'
        '{{ ask_today([a.to, a.role] | select | join(" · "), a.text) }}'
    ).render(a=NS(**ASK))
    assert "None" not in html and "for " not in html and "Question to ask today" in html
    assert "for Priya Menon · Head of Sales" in _macro("Priya Menon · Head of Sales")


def test_the_email_lines_without_a_name() -> None:

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader("othor/mail/templates"), autoescape=True
    )
    for name in ("brief_simple.txt", "brief_full.txt"):
        src = env.loader.get_source(env, name)[0]  # type: ignore[union-attr]
        start = src.index("{% if b.ask %}")
        block = (
            src[start : src.index("{{ b.ask.text }}", start) + len("{{ b.ask.text }}")]
            + "{% endif %}"
        )
        out = env.from_string(block).render(b=NS(ask=NS(**ASK)))
        assert "None" not in out and "for" not in out.split("\n")[1], out
    macro = env.from_string('{% import "_email.html" as e %}{{ e.ask(a) }}').render(a=NS(**ASK))
    assert "None" not in macro and ">for " not in macro
