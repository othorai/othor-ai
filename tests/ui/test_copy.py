# SPDX-License-Identifier: Apache-2.0
"""Every Copy button uses one shared script (answered 5 Nov 2026): the clipboard API with a
"Copied" toast; where the clipboard isn't allowed, the text is selected for Ctrl+C."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.browser
BASE = "othor.localhost"


def test_copy_uses_the_clipboard_then_falls_back_to_selecting(live: tuple[int, str]) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    port, _ = live
    url = f"http://{BASE}:{port}/_components"
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # pragma: no cover - chromium not installed
            pytest.skip(f"chromium not installed: {e}")
        ctx = browser.new_context()
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=f"http://{BASE}:{port}")
        page = ctx.new_page()
        page.goto(url, wait_until="load")
        page.click("#copy-demo")
        page.wait_for_selector("#toast-dock .toast")
        assert page.inner_text("#toast-dock") == "Copied"
        assert page.evaluate("navigator.clipboard.readText()") == "SELECT 1"
        # no clipboard allowed: the target's text is selected instead
        blocked = browser.new_context()
        blocked.add_init_script(
            "Object.defineProperty(navigator, 'clipboard', {value: {writeText: () => "
            "Promise.reject(new Error('denied'))}});"
        )
        page = blocked.new_page()
        page.goto(url, wait_until="load")
        page.click("#copy-target-demo")
        page.wait_for_selector("#toast-dock .toast")
        toast = page.inner_text("#toast-dock")
        mac = page.evaluate("/mac/i.test(navigator.userAgentData?.platform || navigator.platform)")
        key = "⌘C" if mac else "Ctrl+C"
        assert toast == f"Selected · press {key} to copy"
        assert "GROUP BY" in page.evaluate("window.getSelection().toString()")
        browser.close()
