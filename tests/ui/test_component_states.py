# SPDX-License-Identifier: Apache-2.0
"""Every shared control has its states (UI foundation step 4), with the board's values (9b):
read as computed styles on /_components/states."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.browser
BASE = "othor.localhost"
ACCENT, HOVER, PRESS = "rgb(44, 202, 255)", "rgb(23, 185, 238)", "rgb(15, 166, 217)"
RING = "rgba(44, 202, 255, 0.35) 0px 0px 0px 3px"


def test_states_match_the_board(live: tuple[int, str]) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    port, _ = live
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # pragma: no cover - chromium not installed
            pytest.skip(f"chromium not installed: {e}")
        page = browser.new_page()
        page.goto(f"http://{BASE}:{port}/_components/states", wait_until="load")

        def style(sel: str, prop: str) -> str:
            return page.eval_on_selector(sel, f"e => getComputedStyle(e).{prop}")

        got = {
            "primary": style(".btn.primary:not([class*=is-])", "backgroundColor"),
            "primary hover": style(".btn.primary.is-hover", "backgroundColor"),
            "primary pressed": style(".btn.primary.is-active", "backgroundColor"),
            "primary focus": style(".btn.primary.is-focus", "boxShadow"),
            "disabled": style(".btn.primary.is-disabled", "opacity"),
            "input error": style("input.input.is-error", "borderColor"),
            "select focus": style("select.select.is-focus", "boxShadow"),
            "chip selected": style(".chip.is-on", "backgroundColor"),
            "tab selected": style(".tab.is-on", "borderBottomColor"),
            "row selected": style("tr.is-selected td", "backgroundColor"),
        }
        browser.close()
    assert got == {
        "primary": ACCENT,
        "primary hover": HOVER,
        "primary pressed": PRESS,
        "primary focus": RING,
        "disabled": "0.45",
        "input error": "rgb(214, 69, 69)",
        "select focus": RING,
        "chip selected": "rgb(230, 247, 255)",
        "tab selected": ACCENT,
        "row selected": "rgb(230, 247, 255)",
    }
