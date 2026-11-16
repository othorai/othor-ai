# SPDX-License-Identifier: Apache-2.0
"""#55 (13 Nov 2026): an (i) next to a caps heading opens its words in normal case, not in the
heading's capitals (Ontology's Known patterns, and every caps heading)."""

from __future__ import annotations

import pytest

from tests.web.conftest import BASE

pytestmark = pytest.mark.browser

HEADINGS = [
    # the Ontology's section heading (components/ontology.html ont_section)
    '<section class="ont-section"><div class="ont-head"><p class="t">Known patterns '
    '<span class="pop"><button class="info">i</button><span class="pop-body" id="x">Patterns '
    "Othor AI knows about.</span></span></p></div></section>",
    # a page's caps title with an (i)
    '<p class="caps-title">Sessions <span class="pop"><button class="info">i</button>'
    '<span class="pop-body" id="x">Your live sessions.</span></span></p>',
    '<section class="section lines"><div class="section-head">Company <span class="pop">'
    '<button class="info">i</button><span class="pop-body" id="x">About it.</span></span>'
    "</div></section>",
]


@pytest.mark.parametrize("html", HEADINGS)
def test_the_info_words_are_in_normal_case(live, html) -> None:  # type: ignore[no-untyped-def]
    sync_api = pytest.importorskip("playwright.sync_api")
    port, _session = live
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # pragma: no cover - chromium not installed
            pytest.skip(f"chromium not installed: {e}")
        page = browser.new_page()
        page.goto(f"http://{BASE}:{port}/login", wait_until="load")
        css = page.eval_on_selector('link[rel="stylesheet"][href*="/css/app."]', "e => e.href")
        page.set_content(
            f'<link rel="stylesheet" href="{css}"><main class="app-main">{html}</main>',
            wait_until="load",
        )
        got = page.eval_on_selector(
            ".pop-body",
            "e => { const s = getComputedStyle(e); "
            "return [s.textTransform, s.letterSpacing, s.fontWeight]; }",
        )
        browser.close()
    assert got[0] == "none"
    assert got[1] in ("normal", "0px")
    assert got[2] == "400"
