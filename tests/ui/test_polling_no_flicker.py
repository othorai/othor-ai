# SPDX-License-Identifier: Apache-2.0
"""Areas that refresh themselves don't blink (owner, 11 Nov 2026: the screen flickered while a file
was being read). htmx carries the old element's `htmx-swapping` class onto the refreshed one, and
`.htmx-swapping` fades to opacity 0 for a removed row; on a polling area that made every refresh
start invisible. A polling area, or one that holds a poller (a panel or `main` swapped with
hx-select), stays visible; a one-off removal still fades out."""

from __future__ import annotations

from typing import Any

import pytest

from tests.ui.css_build import build_css_module

pytestmark = pytest.mark.browser

PAGE = """<!doctype html><html><head><style>{css}</style></head><body>
<div id="files" hx-get="/setup/upload/list" hx-trigger="every 2s" class="htmx-swapping">list</div>
<main id="main" class="htmx-swapping"><div hx-get="/x" hx-trigger="every 2s">drafting</div></main>
<aside class="panel htmx-added"><div hx-get="/y" hx-trigger="every 1s">scanning</div></aside>
<div id="row" data-row class="htmx-swapping">a removed row</div>
</body></html>"""


@pytest.fixture(scope="module")
def css() -> str:
    try:
        return build_css_module().build().read_text()
    except (OSError, SystemExit) as e:  # no network and no cached CLI
        pytest.skip(f"Tailwind CLI unavailable: {e}")


@pytest.fixture
def page(css: str) -> Any:
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # pragma: no cover - chromium not installed
            pytest.skip(f"chromium not installed: {e}")
        pg = b.new_page()
        pg.set_content(PAGE.format(css=css))
        pg.wait_for_timeout(400)  # past the 150 ms swap transition
        yield pg
        b.close()


def _opacity(page: Any, sel: str) -> float:
    return float(page.eval_on_selector(sel, "e => getComputedStyle(e).opacity"))


def test_polling_areas_stay_visible_through_a_refresh(page: Any) -> None:
    assert _opacity(page, "#files") == 1.0
    assert _opacity(page, "#main") == 1.0
    assert page.eval_on_selector("aside", "e => getComputedStyle(e).animationName") == "none"


def test_a_removed_row_still_fades(page: Any) -> None:
    assert _opacity(page, "#row") == 0.0
