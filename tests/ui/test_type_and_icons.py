# SPDX-License-Identifier: Apache-2.0
"""Type and icons (UI foundation step 3): every weight the boards use is a real Inter face (700
is not synthesised), numbers are tabular everywhere, and every icon a template asks for is in
the Lucide sprite."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "othor" / "web" / "static"
TEMPLATES = ROOT / "othor" / "web" / "templates"
BASE = "othor.localhost"


def test_every_icon_used_is_in_the_sprite():
    sprite = (STATIC / "icons" / "sprite.svg").read_text()
    ids = set(re.findall(r'<symbol id="([a-z0-9-]+)"', sprite))
    used = set()
    for p in TEMPLATES.rglob("*.html"):
        used |= set(re.findall(r'icon\("([a-z0-9-]+)"', p.read_text()))
    shell = (TEMPLATES / "shells" / "app.html").read_text()
    used |= set(re.findall(r'\("([a-z]+)", "[^"]+", "/[a-z]*"\)', shell))  # nav keys
    assert used and used <= ids, sorted(used - ids)


def test_inter_weights_are_vendored():
    css = (STATIC / "css" / "fonts.css").read_text()
    for w in (400, 500, 600, 700):
        assert re.search(rf"font-family: 'Inter';\s*font-style: normal;\s*font-weight: {w};", css)
    assert (STATIC / "fonts" / "ttf" / "Inter-700.ttf").exists()  # the PNG renderer too


@pytest.mark.browser
def test_real_bold_and_tabular_numbers(live: tuple[int, str]) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    port, _ = live
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # pragma: no cover - chromium not installed
            pytest.skip(f"chromium not installed: {e}")
        page = browser.new_page()
        page.goto(f"http://{BASE}:{port}/_components/9a", wait_until="load")
        # ask for each weight: a real face must load (none is synthesised)
        faces = page.evaluate(
            "Promise.all(['400','500','600','700'].map(w => document.fonts.load(w + ' 16px Inter')"
            ".then(fs => fs.map(f => f.weight))))"
        )
        body = page.evaluate("getComputedStyle(document.body).fontVariantNumeric")
        browser.close()
    for w, got in zip(("400", "500", "600", "700"), faces, strict=True):
        assert w in got, (w, faces)
    assert body == "tabular-nums"
