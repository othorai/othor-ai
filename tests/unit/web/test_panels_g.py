# SPDX-License-Identifier: Apache-2.0
"""M11c · G: one side-panel head everywhere (tags or kicker, title, subtitle, the muted ×), and
the shared source rows used by onboarding and Add a source."""

from __future__ import annotations

import re
from pathlib import Path

from othor.web.templating import templates

TEMPLATES = Path(__file__).parents[3] / "othor" / "web" / "templates"
HEAD = (
    '{% from "components/panels.html" import pane_title %}'
    '{% call pane_title("Order value", "Confirmed 3 Nov", "/ontology", aside="₹4.2 L") %}'
    '<span class="chip">Confirmed</span>{% endcall %}'
)


def test_the_panel_head_is_tags_title_subtitle_and_a_close() -> None:
    html = templates.env.from_string(HEAD).render()
    tags, title, sub = (html.index(x) for x in ("panel-tags", "panel-title", 'class="hint"'))
    assert tags < title < sub
    assert '<span class="panel-money">₹4.2 L</span>' in html
    assert 'href="/ontology" aria-label="Close"' in html


def test_the_head_without_tags_and_with_a_connector_tile() -> None:
    html = templates.env.from_string(
        '{% from "components/panels.html" import pane_title %}'
        '{{ pane_title("History", tile="PG") }}'
    ).render()
    assert "panel-tags" not in html and "panel-money" not in html
    assert "tile-kind conn pg" in html


def test_no_panel_writes_its_own_head() -> None:
    """The old hand-written heads (panel-ctx, tags in their own column) are gone."""
    for f in TEMPLATES.rglob("*.html"):
        text = f.read_text()
        assert "panel-ctx" not in text, f
        if f.name != "panels.html":
            assert '"panel-head-row"' not in text and "panel-head-row" not in text, f


def test_other_connectors_and_the_support_line() -> None:
    html = templates.env.from_string(
        '{% from "components/sources.html" import other_connectors %}{{ other_connectors(c, h) }}'
    ).render(
        c=[("postgres", "PostgreSQL", ""), ("gsheets", "Google Sheets", "")],
        h={"postgres": "/p", "gsheets": "/g"},
    )
    assert "Other connectors · 2" in html and "<details" in html and " open" not in html
    assert re.search(r'href="/p">Connect</a>', html)
    # the support line shows only when the install sets an address (M20.2)
    assert "Not listed? Write to" not in html
    # an install that sets a support address: its own environment (templates cache their globals)
    env = templates.env.overlay(cache_size=0)
    env.globals = {**templates.env.globals, "install": {"support_email": "s@x.example"}}
    src = '{% from "components/sources.html" import other_connectors %}'
    html = env.from_string(src + "{{ other_connectors(c, h) }}").render(
        c=[("postgres", "PostgreSQL", "")], h={"postgres": "/p"}
    )
    assert "Not listed? Write to" in html and "mailto:s@x.example" in html
