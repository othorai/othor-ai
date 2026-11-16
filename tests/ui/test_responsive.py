# SPDX-License-Identifier: Apache-2.0
"""Every M1 page is usable on a phone: at 390 px nothing scrolls sideways (agreed 4 Nov).

Starts the real app on a free port (SQLite, memory mail relay), signs up through HTTP, then
loads each page in headless Chromium. Needs `playwright install chromium`.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.browser
BASE = "othor.localhost"
WIDTHS = (390, 768, 1440)


PAGES = [
    ("root", "/login"),
    ("root", "/signup"),
    ("root", "/_components/screens/2a"),
    ("root", "/_components/screens/1c"),
    ("root", "/_components/screens/1h"),
    ("company", "/login"),
    ("company", "/login?signed_out=1"),
    ("signed", "/setup/upload"),
    ("signed", "/settings"),
    ("signed", "/usage"),  # M15.8
    ("signed", "/summary"),  # M16.3
    ("signed", "/account"),  # M15.8
    ("signed", "/home"),
    ("signed", "/sources"),
    ("signed", "/sources?tab=all"),
    ("signed", "/sources?panel=add"),
    ("signed", "/sources?panel=connect&kind=postgres"),
]


@pytest.mark.parametrize("width", WIDTHS)
def test_no_horizontal_scroll(live: tuple[int, str], width: int) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    port, session = live
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # pragma: no cover - chromium not installed
            pytest.skip(f"chromium not installed: {e}")
        ctx = browser.new_context(viewport={"width": width, "height": 900})
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
        for kind, path in PAGES:
            host = BASE if kind == "root" else f"indigothread.{BASE}"
            page.goto(f"http://{host}:{port}{path}", wait_until="load")
            over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            if over > 0:
                problems.append(f"{path} on {kind} host overflows by {over}px")
        browser.close()
    assert problems == [], problems


def test_phone_nav_opens_from_the_menu_button(live: tuple[int, str]) -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    port, session = live
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 390, "height": 800})
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
        page.goto(f"http://indigothread.{BASE}:{port}/settings")
        assert not page.is_visible("text=Morning Brief")
        page.click("label.nav-button")
        assert page.is_visible("text=Morning Brief")
        browser.close()


def test_the_account_stays_in_view_on_a_short_window(live: tuple[int, str]) -> None:
    """#30: on a short window (1440×652) the sidebar is the window's height, so the account at
    its foot is in view at the top of a long page and after scrolling, not only at the end."""
    sync_api = pytest.importorskip("playwright.sync_api")
    port, session = live
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # pragma: no cover - chromium not installed
            pytest.skip(f"chromium not installed: {e}")
        ctx = browser.new_context(viewport={"width": 1440, "height": 652})
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
        page.goto(f"http://indigothread.{BASE}:{port}/home", wait_until="load")
        page.evaluate("document.querySelector('.app-main').style.minHeight = '3000px'")
        for y in (0, 1200):
            page.evaluate(f"window.scrollTo(0, {y})")
            box = page.locator(".side .account").bounding_box()
            assert box is not None
            assert box["y"] >= 0 and box["y"] + box["height"] <= 652, (y, box)
        browser.close()


def test_the_ontology_title_and_its_pill_line_up(live: tuple[int, str]) -> None:
    """#34: the "N questions waiting on Home" pill sat 9 px below the title's middle (the title
    carried the page title's bottom margin inside the row)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    port, _session = live
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # pragma: no cover - chromium not installed
            pytest.skip(f"chromium not installed: {e}")
        page = browser.new_page(viewport={"width": 1440, "height": 652})
        page.goto(f"http://{BASE}:{port}/login", wait_until="load")
        css = page.eval_on_selector('link[rel="stylesheet"][href*="/css/app."]', "e => e.href")
        page.set_content(
            f'<link rel="stylesheet" href="{css}"><main class="app-main"><div class="ont-head flex '
            'items-center gap-12 flex-wrap"><h1 class="ont-title">Ontology</h1><a class='
            '"wait-pill" href="/home"><span class="dot accent"></span>5 questions waiting on '
            "Home →</a></div></main>",
            wait_until="load",
        )
        mid = page.evaluate(
            "() => ['.ont-title', '.wait-pill'].map(s => { const r = document.querySelector(s)"
            ".getBoundingClientRect(); return r.top + r.height / 2; })"
        )
        assert abs(mid[0] - mid[1]) <= 1, mid
        browser.close()


def test_the_finding_panels_run_line_is_one_line(live: tuple[int, str]) -> None:
    """#35: the run line ("Run #0002 · 178c…2b · spec v2 · replayable") was squeezed beside
    the actions and wrapped word by word; it has its own row now, on one line."""
    sync_api = pytest.importorskip("playwright.sync_api")
    port, _session = live
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # pragma: no cover - chromium not installed
            pytest.skip(f"chromium not installed: {e}")
        page = browser.new_page(viewport={"width": 680, "height": 400})
        page.goto(f"http://{BASE}:{port}/login", wait_until="load")
        css = page.eval_on_selector('link[rel="stylesheet"][href*="/css/app."]', "e => e.href")
        buttons = "".join(f'<a class="btn">{t}</a>' for t in ("Snooze 7 days", "Mute", "Watch"))
        page.set_content(
            f'<link rel="stylesheet" href="{css}"><div class="flex flex-wrap items-center '
            'justify-between gap-8 w-full" style="width:620px"><p class="prov-foot w-full '
            'min-w-0">Run #0002 · 178c…2b · spec v2 · replayable</p><div class="flex flex-wrap '
            f'items-center justify-end gap-8 w-full md:w-auto">{buttons}</div></div>',
            wait_until="load",
        )
        box = page.locator(".prov-foot").bounding_box()
        assert box is not None and box["height"] <= 20 and box["width"] >= 600, box
        browser.close()
