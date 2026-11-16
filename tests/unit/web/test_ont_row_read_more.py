# SPDX-License-Identifier: Apache-2.0
"""#48: a long Ontology line (the business description from onboarding) is cut at a character
limit, at a word, with "Read more" showing the whole line in place; short lines are unchanged."""

from __future__ import annotations

from othor.web.templating import templates

ROW = '{% from "components/ontology.html" import ont_row %}{{ ont_row(text, "Your Ontology") }}'


def _render(text: str) -> str:
    return templates.env.from_string(ROW).render(text=text)


def test_a_long_line_is_cut_with_read_more() -> None:
    words = " ".join(f"word{i}" for i in range(120))  # about 840 characters
    html = _render(words)
    assert "Read more" in html
    short = html[html.index('class="ont-short"') :]
    short = short[: short.index("</span>")]
    assert "word119" not in short and short.rstrip().endswith("…")
    assert "word119" in html  # the whole line is there for Read more


def test_a_short_line_is_unchanged() -> None:
    html = _render("Textile manufacturer: two plants, Surat and Coimbatore.")
    assert "Read more" not in html and "Textile manufacturer: two plants" in html
