# SPDX-License-Identifier: Apache-2.0
"""Actions happen in place, in a real browser (docs/07, M12b): on Home, Accept doesn't reload the
page, keeps the scroll position, moves focus to the row in its place and updates the counts; Undo
restores the row. Needs `make dev && make seed` (the steady night)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.ui.test_capture_states import serve, session_cookie
from tests.web.board_worlds import setup_world  # noqa: F401
from tests.web.conftest import BASE, env  # noqa: F401
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

pytestmark = pytest.mark.browser


@pytest.fixture
def browser() -> Iterator[Any]:
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # pragma: no cover - chromium not installed
            pytest.skip(f"chromium not installed: {e}")
        yield b
        b.close()


def test_accept_and_undo_on_home_happen_in_place(browser: Any, night) -> None:  # noqa: F811
    expect = pytest.importorskip("playwright.sync_api").expect
    world, *_ = night
    name, _, value = session_cookie(world).partition("=")
    with serve(world.app) as port:
        ctx = browser.new_context(viewport={"width": 1440, "height": 600})
        host = f"indigothread.{BASE}"
        ctx.add_cookies([{"name": name, "value": value, "domain": host, "path": "/"}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://{host}:{port}/home", wait_until="networkidle")
        page.evaluate("window.__stay = 1")  # gone if the page reloads
        rows = page.locator('[aria-label="Proposals"] [data-row]')
        n = rows.count()
        assert n >= 1
        # the open proposals count down in place (design B2: no header line; the rows say it)
        open_rows = page.locator('[aria-label="Proposals"] [data-row]:not(.is-decided)')
        expect(open_rows).to_have_count(n)
        first = rows.nth(0).get_attribute("data-row")
        page.locator('[aria-label="Proposals"]').scroll_into_view_if_needed()
        y = page.evaluate("window.scrollY")
        rows.nth(0).get_by_role("button", name="Accept").click()
        expect(page.locator("tr.is-decided")).to_have_count(1)
        assert page.evaluate("window.__stay") == 1, "the page reloaded"
        assert page.url.endswith("/home")
        assert abs(page.evaluate("window.scrollY") - y) <= 1
        expect(open_rows).to_have_count(n - 1)
        assert rows.nth(0).get_attribute("data-row") == first  # the Undo row sits in its place
        focused = page.evaluate(
            "document.activeElement.closest('[data-row]')?.getAttribute('data-row')"
        )
        assert focused == first  # focus is on the row now in its place (its Undo)
        page.locator("tr.is-decided").get_by_role("button", name="Undo").click()
        expect(page.locator("tr.is-decided")).to_have_count(0)
        expect(open_rows).to_have_count(n)
        assert page.evaluate("window.__stay") == 1
        assert errors == []
        ctx.close()


def test_sources_panels_work_in_place(browser: Any, setup_world) -> None:  # noqa: F811
    """M11c: on the Sources step the scanning panel polls while the scan runs; Tables opens over
    the list, a table's use changes from its menu, and Undo puts it back. Nothing reloads."""
    from tests.fixtures import board_states as bs
    from tests.web.board_worlds import apply, cookies

    expect = pytest.importorskip("playwright.sync_api").expect
    st = bs.STATES["2k"]
    values = apply(st, setup_world)
    with serve(setup_world.env.app) as port:
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        host = f"indigothread.{BASE}"
        ctx.add_cookies(
            [
                {"name": n, "value": v, "domain": host, "path": "/"}
                for n, _, v in (
                    c.partition("=") for c in cookies(setup_world.env, setup_world.host).split("; ")
                )
            ]
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        polls: list[str] = []
        page.on("request", lambda r: "panel=scan" in r.url and polls.append(r.url))
        page.goto(f"http://{host}:{port}{bs.fill(st.path, values)}", wait_until="networkidle")
        expect(page.locator("aside.panel")).to_contain_text("You can close this.")
        page.evaluate("window.__stay = 1")
        n = len(polls)
        page.wait_for_timeout(2500)
        assert len(polls) > n, "the scanning panel polls while the scan runs"
        # close it, open Tables from the list: the panel swaps in, no reload
        page.locator('aside.panel a[aria-label="Close"]').click()
        expect(page.locator("aside.panel")).to_have_count(0)
        row = page.locator(".src-row", has_text="PostgreSQL · sales_dw")
        row.get_by_role("link", name="Tables").click()
        expect(page.locator("aside.panel")).to_contain_text("How each table is used.")
        assert page.evaluate("window.__stay") == 1, "the page reloaded"
        page.locator('summary[aria-label="Change how sales_dw.fact_order is used"]').click()
        page.get_by_role("menuitem", name="Not needed").click()
        toast = page.locator(".toast")
        expect(toast).to_contain_text("sales_dw.fact_order · Ignored · Not needed")
        assert page.evaluate("window.__stay") == 1, "the page reloaded"
        toast.get_by_role("button", name="Undo").click()
        expect(page.locator(".toast")).to_have_count(0)
        expect(page.locator("aside.panel")).to_contain_text("sales_dw.fact_order")
        assert page.evaluate("window.__stay") == 1, "the page reloaded"
        assert errors == []
        ctx.close()


def test_connect_in_the_panel_goes_on_to_the_scan(browser: Any, setup_world) -> None:  # noqa: F811
    """M11c p0 → p1 → p2 → p3: Connect on a suggestion opens the panel over the list; Connect
    tests, connects and shows the scan; when the scan ends the panel closes and the list shows
    the source. One page load for the whole flow."""
    from tests.fixtures import board_states as bs
    from tests.web.board_worlds import apply, cookies
    from tests.web.board_worlds_setup import PW, _jobs

    expect = pytest.importorskip("playwright.sync_api").expect
    st = bs.STATES["10a"]
    apply(st, setup_world)
    with serve(setup_world.env.app) as port:
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        host = f"indigothread.{BASE}"
        ctx.add_cookies(
            [
                {"name": n, "value": v, "domain": host, "path": "/"}
                for n, _, v in (
                    c.partition("=") for c in cookies(setup_world.env, setup_world.host).split("; ")
                )
            ]
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://{host}:{port}/setup/sources", wait_until="networkidle")
        page.evaluate("window.__stay = 1")
        page.locator(".src-row", has_text="PostgreSQL · sales_dw").get_by_role(
            "link", name="Connect"
        ).click()
        panel = page.locator("aside.panel")
        expect(panel).to_contain_text("Connect PostgreSQL · sales_dw")
        expect(panel.locator("details.conn-step[open]")).to_have_count(0)  # steps 1, 2 closed
        expect(page.locator("#f-database")).to_have_value("sales_dw")
        page.fill("#f-host", "localhost")
        page.fill("#f-port", "5443")
        page.fill("#f-password", PW)
        page.select_option("select[name=ssl]", "disable")
        panel.get_by_role("button", name="Connect").click()
        expect(panel).to_contain_text("Testing the connection…")
        for _ in range(40):  # the worker: the test, then the scan
            _jobs(setup_world)
            if page.locator("aside.panel").count() == 0:
                break
            page.wait_for_timeout(500)
        expect(page.locator("aside.panel")).to_have_count(0)
        row = page.locator(".src-row", has_text="PostgreSQL · sales_dw")
        expect(row).to_contain_text("Just added")
        expect(row).to_contain_text("of 48 tables used")
        assert page.evaluate("window.__stay") == 1, "the page reloaded"
        assert errors == []
        ctx.close()


def test_decisions_step_by_step_in_place(browser: Any, setup_world) -> None:  # noqa: F811
    """#32: Things only you can decide, one per screen: pick and Use this answer saves it and
    moves on in place (the page's counts with it); Set aside on the last closes the panel; no
    reload. (Replaces #20's test: with one decision per screen there's no other pick to keep.)"""
    from tests.web.board_worlds import PREPS, cookies

    expect = pytest.importorskip("playwright.sync_api").expect
    PREPS["metrics_drafted"](setup_world)
    with serve(setup_world.env.app) as port:
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        host = f"indigothread.{BASE}"
        ctx.add_cookies(
            [
                {"name": n, "value": v, "domain": host, "path": "/"}
                for n, _, v in (
                    c.partition("=") for c in cookies(setup_world.env, setup_world.host).split("; ")
                )
            ]
        )
        page = ctx.new_page()
        page.goto(f"http://{host}:{port}/setup/metrics?panel=decisions", wait_until="networkidle")
        panel = page.locator("aside.panel")
        expect(panel).to_contain_text("Decision 1 of 2 · 0 decided")
        expect(page.locator("#main")).to_contain_text("1 required decision left")
        page.evaluate("window.__stay = 1")
        panel.get_by_role("link", name="Next").click()
        expect(panel).to_contain_text("Decision 2 of 2 · 0 decided")
        assert "d=order_book_value" in page.url
        panel.get_by_role("link", name="Back").click()
        expect(panel).to_contain_text("Decision 1 of 2 · 0 decided")
        panel.locator('input[name="choice:net_revenue"]').first.check()
        panel.get_by_role("button", name="Use this answer").click()
        expect(panel).to_contain_text("Decision 2 of 2 · 1 decided")
        expect(page.locator("#main")).not_to_contain_text("1 required decision left")
        panel.get_by_role("button", name="Set aside for now").click()
        expect(page.locator("aside.panel")).to_have_count(0)
        assert page.evaluate("window.__stay") == 1, "the page reloaded"
        ctx.close()
