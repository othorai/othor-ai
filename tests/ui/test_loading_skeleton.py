# SPDX-License-Identifier: Apache-2.0
"""#63 (owner, 14 Nov 2026): Morning Brief → Sweep took a while with nothing changing. A menu
click now shows the new page's menu item as current at once and the main panel's loading view
(very light grey boxes) until the page arrives."""

from __future__ import annotations

import pytest

from tests.ui.test_capture_states import serve, session_cookie
from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

pytestmark = pytest.mark.browser


def test_a_menu_click_shows_the_item_and_the_loading_view_at_once(night):  # noqa: F811
    sync_api = pytest.importorskip("playwright.sync_api")
    env, *_ = night
    name, val = session_cookie(env).split("=", 1)
    with serve(env.app) as port, sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # pragma: no cover - chromium not installed
            pytest.skip(f"chromium not installed: {e}")
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_cookies(
            [{"name": name, "value": val, "domain": f"indigothread.{BASE}", "path": "/"}]
        )
        pg = ctx.new_page()
        pg.goto(f"http://indigothread.{BASE}:{port}/brief")
        # stop the navigation itself, after othor.js has handled the click (a listener added
        # later runs later), so the page as the owner sees it in that moment can be read
        pg.evaluate(
            "() => document.addEventListener('click', (e) => { if (e.target.closest('.side a'))"
            " e.preventDefault(); })"
        )
        pg.locator(".side a.nav-item[href='/sweep']").click()
        assert pg.locator(".side a.nav-item[href='/sweep']").get_attribute("aria-current") == "page"
        assert pg.locator(".side a.nav-item[href='/brief']").get_attribute("aria-current") is None
        assert pg.locator("#main .skel").count() == 1
        assert pg.url.endswith("/brief")
        b.close()
