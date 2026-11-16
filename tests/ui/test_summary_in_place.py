# SPDX-License-Identifier: Apache-2.0
"""Summary's controls and row actions happen in place (M16.3; owner: reloads are defects): the
window select and Hide swap the page without a reload, and an opened row stays usable."""

from __future__ import annotations

import pytest

from tests.ui.test_capture_states import serve, session_cookie
from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

pytestmark = pytest.mark.browser


def test_controls_and_moves_swap_in_place(night):  # noqa: F811
    sync_api = pytest.importorskip("playwright.sync_api")
    env, _h, _cid, _rid, _svc = night
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
        pg.goto(f"http://indigothread.{BASE}:{port}/summary")
        pg.evaluate("window.__stay = 1")
        pg.select_option("select[name=window]", "12m")
        pg.wait_for_timeout(1000)  # the page's CSP has no eval, so no wait_for_function
        assert pg.evaluate("window.__stay") == 1  # no reload
        assert pg.locator("select[name=window]").input_value() == "12m"
        # focus stays on the control the owner used (data-keep-focus), not the page title
        assert pg.evaluate("document.activeElement.name") == "window"
        assert "vs Sep" in pg.content()  # 12 months: each month against the month before
        # % swaps the charts in place too (#65): a change line on a zero line
        before = pg.locator(".sum-chart svg").first.inner_html()
        pg.locator("input[name=show][value=pct]").locator("xpath=..").click()
        pg.wait_for_timeout(1000)
        assert pg.evaluate("window.__stay") == 1
        assert pg.locator(".sum-chart svg").first.inner_html() != before
        pg.locator("input[name=show][value=actual]").locator("xpath=..").click()
        pg.wait_for_timeout(1000)
        pg.locator("details.sum-row").nth(2).locator("summary").click()
        pg.locator("details.sum-row").nth(2).get_by_role("button", name="Hide").click()
        pg.wait_for_timeout(800)
        areas = pg.locator(".sum-area").all_inner_texts()
        assert pg.evaluate("window.__stay") == 1
        assert "OPERATIONS" not in [a.upper() for a in areas]
        # the Every number view, from the dropdown at the top right, in place
        pg.select_option("select[name=rows]", "numbers")
        pg.wait_for_timeout(1000)
        assert pg.evaluate("window.__stay") == 1
        assert pg.locator("section.sum-group").count() >= 4
        assert pg.locator("details.sum-row").count() > len(areas)
        b.close()
