# SPDX-License-Identifier: Apache-2.0
"""#24: Home's brief and Sweep cards take the theme blue border on hover and keyboard focus,
like the website footer's What's new card (it was a dark grey)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.ui.css_build import build_css_module

pytestmark = pytest.mark.browser

PAGE = """<!doctype html><html><head><style>{css}</style></head><body style="padding:40px">
<div class="brief-cards"><div class="card-stack"><a class="brief-card has-visual" href="#">
<div class="visual"></div><div class="body"><p class="headline">A card</p></div></a></div></div>
</body></html>"""


@pytest.fixture
def page() -> Any:
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        css = build_css_module().build().read_text()
    except (OSError, SystemExit) as e:  # no network and no cached CLI
        pytest.skip(f"Tailwind CLI unavailable: {e}")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # pragma: no cover - chromium not installed
            pytest.skip(f"chromium not installed: {e}")
        pg = b.new_page()
        pg.set_content(PAGE.format(css=css))
        yield pg
        b.close()


def _border(page: Any) -> str:
    return str(page.eval_on_selector(".brief-card", "e => getComputedStyle(e).borderTopColor"))


def test_a_card_turns_theme_blue_on_hover_and_focus(page: Any) -> None:
    blue = "rgb(44, 202, 255)"  # --color-accent, #2CCAFF
    assert _border(page) != blue
    page.hover(".brief-card")
    page.wait_for_timeout(250)
    assert _border(page) == blue
    page.mouse.move(0, 0)
    page.focus(".brief-card")
    page.wait_for_timeout(250)
    assert _border(page) == blue
