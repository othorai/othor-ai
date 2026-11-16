# SPDX-License-Identifier: Apache-2.0
"""Components under real-data stress (M11a): long customer names, 24 headline numbers, very
large and very small ₹ values, empty and error states, at phone and desktop width. Nothing
scrolls sideways, no text spills out of its box, and keyboard focus is visible."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.browser
BASE = "othor.localhost"

BOXES = ", ".join(
    f"[data-stress] {s}"
    for s in (
        "td",
        ".brief-tile",
        ".brief-card",
        ".ont-row",
        ".q-row",
        ".banner",
        ".toast",
        ".empty",
    )
)
SPILL = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('BOXES')) {
    const cs = getComputedStyle(el);
    const clipped = cs.textOverflow === 'ellipsis' || ['auto', 'scroll'].includes(cs.overflowX);
    if (!clipped && el.scrollWidth > el.clientWidth + 1)
      out.push(el.className + ': ' + (el.scrollWidth - el.clientWidth) + 'px');
  }
  return out;
}
""".replace("BOXES", BOXES)


def _browser(sync_api, p):  # type: ignore[no-untyped-def]
    try:
        return p.chromium.launch()
    except Exception as e:  # pragma: no cover - chromium not installed
        pytest.skip(f"chromium not installed: {e}")


@pytest.mark.parametrize("width", (390, 1440))
def test_stress_components_hold(live: tuple[int, str], width: int) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    port, _session = live
    with sync_api.sync_playwright() as p:
        browser = _browser(sync_api, p)
        page = browser.new_context(viewport={"width": width, "height": 900}).new_page()
        page.goto(f"http://{BASE}:{port}/_components/stress", wait_until="load")
        over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        spill = page.evaluate(SPILL)
        text = page.inner_text("[data-stress]")
        browser.close()
    assert over <= 0, f"scrolls sideways by {over}px"
    assert spill == [], spill
    assert "₹48,000 Cr" in text and "₹12,345 Cr" in text and "<₹0.1" in text
    assert "₹48,000.00 Cr" not in text


def test_keyboard_focus_is_visible(live: tuple[int, str]) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    port, session = live
    ring = """() => { if (document.activeElement === document.body) return true;
      const s = getComputedStyle(document.activeElement);
      const outline = s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0;
      return outline || s.boxShadow !== 'none'; }"""
    with sync_api.sync_playwright() as p:
        browser = _browser(sync_api, p)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_cookies(
            [
                {
                    "name": "othor_session",
                    "value": session,
                    "domain": f"indigothread.{BASE}",
                    "path": "/",
                }
            ]
        )
        page = ctx.new_page()
        problems = []
        for host, path in ((BASE, "/login"), (BASE, "/"), (f"indigothread.{BASE}", "/settings")):
            page.goto(f"http://{host}:{port}{path}", wait_until="load")
            first = page.evaluate(
                "document.querySelector('a[href], button, input, select, textarea').textContent"
            )
            if first.strip() != "Skip to content":
                problems.append(f"{path}: the first focusable isn't the skip link")
            page.evaluate("document.activeElement && document.activeElement.blur()")
            page.keyboard.press("Tab")
            for i in range(5):
                if not page.evaluate(ring):
                    tag = page.evaluate("document.activeElement.outerHTML.slice(0, 80)")
                    problems.append(f"{host}{path} tab {i + 1}: no focus ring on {tag}")
                page.keyboard.press("Tab")
        browser.close()
    assert problems == [], problems
