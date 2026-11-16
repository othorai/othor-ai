# SPDX-License-Identifier: Apache-2.0
"""M12 interactions in a real browser: menus, the popover, tab keys, panels that open and close
without a reload (with the slide), focus, reduced motion, and no CSP violations."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.browser
BASE = "othor.localhost"
CO = f"indigothread.{BASE}"


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


def _page(
    browser: Any, live: tuple[int, str], *, motion: str = "no-preference", width: int = 1440
) -> Any:
    _port, session = live
    ctx = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion=motion)
    ctx.add_cookies([{"name": "othor_session", "value": session, "domain": CO, "path": "/"}])
    page = ctx.new_page()
    page.errors = []  # type: ignore[attr-defined]
    page.on("console", lambda m: m.type == "error" and page.errors.append(m.text))  # type: ignore[attr-defined]
    page.on("pageerror", lambda e: page.errors.append(str(e)))  # type: ignore[attr-defined]
    return page


def _url(live: tuple[int, str], path: str) -> str:
    return f"http://{CO}:{live[0]}{path}"


def test_account_menu_opens_closes_and_returns_focus(browser: Any, live: tuple[int, str]) -> None:
    page = _page(browser, live)
    page.goto(_url(live, "/home"))
    page.wait_for_function("window.Alpine !== undefined")
    expect = pytest.importorskip("playwright.sync_api").expect
    trigger = page.locator("details.account > summary")
    # aria-expanded follows the <details> toggle event, which fires a task after the click:
    # wait for it rather than reading it once (it raced under full-suite load).
    expect(trigger).to_have_attribute("aria-expanded", "false")
    trigger.click()
    expect(trigger).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Escape")
    expect(page.locator("details.account[open]")).to_have_count(0)
    expect(trigger).to_be_focused()
    trigger.click()
    expect(page.locator("details.account[open]")).to_have_count(1)
    page.mouse.click(700, 500)
    expect(page.locator("details.account[open]")).to_have_count(0)
    # keyboard: ArrowDown opens and moves into the items
    trigger.focus()
    page.keyboard.press("ArrowDown")
    page.wait_for_function("document.activeElement.getAttribute('role') === 'menuitem'")
    assert page.errors == []  # type: ignore[attr-defined]


def test_one_menu_open_at_a_time(browser: Any, live: tuple[int, str]) -> None:
    page = _page(browser, live)
    page.goto(_url(live, "/_components"))
    page.wait_for_function("window.Alpine !== undefined")
    menus = page.locator("details[x-data=menu] > summary")
    assert menus.count() >= 2
    expect = pytest.importorskip("playwright.sync_api").expect
    opened = page.locator("details[x-data=menu][open]")
    # the account-menu demo is rendered open; opening another menu closes it
    expect(opened).to_have_count(1)
    menus.nth(1).click()
    expect(menus.nth(1).locator("xpath=..")).to_have_attribute("open", "")
    expect(opened).to_have_count(1)
    menus.nth(0).click()
    expect(menus.nth(0).locator("xpath=..")).to_have_attribute("open", "")
    expect(opened).to_have_count(1)


def test_popover_opens_on_click_and_closes_on_escape(browser: Any, live: tuple[int, str]) -> None:
    page = _page(browser, live)
    page.goto(_url(live, "/_components"))
    page.wait_for_function("window.Alpine !== undefined")
    btn = page.locator('.pop button[aria-controls="pop-demo"]')
    assert page.locator("#pop-demo").is_hidden()
    btn.click()
    assert page.locator("#pop-demo").is_visible() and btn.get_attribute("aria-expanded") == "true"
    page.keyboard.press("Escape")
    assert page.locator("#pop-demo").is_hidden()


def test_tab_bar_arrow_keys(browser: Any, live: tuple[int, str]) -> None:
    # Data sources has no tabs since 13 Nov 2026 (the page style): the tab bar on /_components
    page = _page(browser, live)
    page.goto(_url(live, "/_components"))
    page.wait_for_function("window.Alpine !== undefined")
    tabs = page.locator("nav.tabs .tab")
    assert tabs.nth(0).get_attribute("aria-current") == "page"
    tabs.nth(0).focus()
    page.keyboard.press("ArrowRight")
    assert page.evaluate("document.activeElement.textContent") == tabs.nth(1).inner_text()
    page.keyboard.press("End")
    assert page.evaluate("document.activeElement.textContent") == tabs.last.inner_text()


def test_panel_opens_without_a_reload_slides_and_closes(
    browser: Any, live: tuple[int, str]
) -> None:
    page = _page(browser, live)
    page.goto(_url(live, "/sources"))
    page.wait_for_function("window.Alpine !== undefined")
    origin = page.evaluate("performance.timeOrigin")
    opener = page.locator('a[href="/sources?panel=add"]').first
    opener.click()
    page.wait_for_selector(".panel.wide.is-entering", timeout=2000)
    anim = page.evaluate(
        "(() => { const s = getComputedStyle(document.querySelector('.panel.wide'));"
        " return [s.animationName, s.animationDuration, s.animationTimingFunction]; })()"
    )
    assert anim == ["panel-in", "0.18s", "cubic-bezier(0.2, 0.8, 0.2, 1)"]
    assert page.url.endswith("/sources?panel=add")
    assert page.evaluate("performance.timeOrigin") == origin  # no reload
    page.wait_for_selector(".panel.wide:not(.is-entering)")
    assert page.evaluate("document.querySelector('.panel.wide').contains(document.activeElement)")
    # Tab stays inside the 680 px panel
    for _ in range(25):
        page.keyboard.press("Tab")
        assert page.evaluate(
            "document.querySelector('.panel.wide').contains(document.activeElement)"
        )
    page.keyboard.press("Escape")
    page.wait_for_selector(".panel.wide", state="detached", timeout=2000)
    assert page.url.endswith("/sources")
    assert page.evaluate("performance.timeOrigin") == origin
    assert page.evaluate("document.activeElement.getAttribute('href')") == "/sources?panel=add"
    # Back and forward move through the panel states, still without a reload
    page.go_back()
    page.wait_for_selector(".panel.wide")
    page.go_forward()
    page.wait_for_selector(".panel.wide", state="detached")
    assert page.evaluate("performance.timeOrigin") == origin
    assert page.errors == []  # type: ignore[attr-defined]


def test_a_direct_load_shows_the_panel_without_the_slide(
    browser: Any, live: tuple[int, str]
) -> None:
    page = _page(browser, live)
    page.goto(_url(live, "/sources?panel=add"))
    assert (
        page.evaluate("getComputedStyle(document.querySelector('.panel.wide')).animationName")
        == "none"
    )


def test_reduced_motion_turns_every_duration_to_zero(browser: Any, live: tuple[int, str]) -> None:
    page = _page(browser, live, motion="reduce")
    page.goto(_url(live, "/sources"))
    page.wait_for_function("window.Alpine !== undefined")
    css = "getComputedStyle(document.documentElement).getPropertyValue('--dur-panel').trim()"
    assert page.evaluate(css) == "0s"
    page.locator('a[href="/sources?panel=add"]').first.click()
    page.wait_for_selector(".panel.wide")
    dur = page.evaluate("getComputedStyle(document.querySelector('.panel.wide')).animationDuration")
    assert dur in ("0s", "0s, 0s")
    assert page.evaluate(
        "getComputedStyle(document.querySelector('.btn')).transitionDuration"
    ).startswith("0s")


def test_phone_sheet_slides_up(browser: Any, live: tuple[int, str]) -> None:
    page = _page(browser, live, width=390)
    page.goto(_url(live, "/sources"))
    page.wait_for_function("window.Alpine !== undefined")
    page.locator('a[href="/sources?panel=add"]').first.click()
    page.wait_for_selector(".panel.wide.is-entering", timeout=2000)
    assert (
        page.evaluate("getComputedStyle(document.querySelector('.panel.wide')).animationName")
        == "sheet-in"
    )


@pytest.mark.parametrize(
    "path",
    [
        "/home",
        "/sweep",
        "/brief",
        "/sources",
        "/ontology",
        "/settings",
        "/usage",
        "/summary",
        "/account",
        "/_components",
        "/_components/9b",
    ],
)
def test_no_csp_violation_or_script_error(browser: Any, live: tuple[int, str], path: str) -> None:
    page = _page(browser, live)
    page.goto(_url(live, path))
    page.wait_for_function("window.Alpine !== undefined")
    assert page.errors == []  # type: ignore[attr-defined]


SHIFTS = """
window.__shift = 0;
new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__shift += e.value; })
  .observe({ type: 'layout-shift', buffered: false });
"""


def test_opening_and_closing_a_panel_moves_nothing_behind_it(
    browser: Any, live: tuple[int, str]
) -> None:
    page = _page(browser, live, width=1440)
    page.set_viewport_size({"width": 1440, "height": 500})
    page.goto(_url(live, "/sources"))
    page.wait_for_function("window.Alpine !== undefined")
    page.evaluate("window.scrollTo(0, 120)")
    rect = (
        "JSON.stringify([window.scrollY,"
        " document.querySelector('#main').getBoundingClientRect().toJSON()])"
    )
    before = page.evaluate(rect)
    page.evaluate(SHIFTS)
    page.locator('a[href="/sources?panel=add"]').first.click()
    page.wait_for_selector(".panel.wide:not(.is-entering)")
    page.keyboard.press("Escape")
    page.wait_for_selector(".panel.wide", state="detached")
    assert page.evaluate(rect) == before
    assert page.evaluate("window.__shift") < 0.01


# --- M11c: the listbox and the dialog --------------------------------------------------------


def test_listbox_keys_pick_a_value_and_keep_the_select_in_step(
    browser: Any, live: tuple[int, str]
) -> None:
    expect = pytest.importorskip("playwright.sync_api").expect
    page = _page(browser, live)
    page.goto(_url(live, "/_components/9b"))
    page.wait_for_function("window.Alpine !== undefined")
    btn = page.locator("#f-demo-fy-b")
    sel = page.locator("#f-demo-fy")
    expect(btn).to_have_text("April")
    expect(sel).to_be_hidden()  # our list on a fine pointer; the select stays the form value
    btn.focus()
    page.keyboard.press("ArrowDown")  # opens on the chosen month
    expect(btn).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#f-demo-fy-list .lb-opt.is-active")).to_have_text("April")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(btn).to_have_attribute("aria-expanded", "false")
    expect(btn).to_have_text("May")
    assert sel.input_value() == "5"
    expect(btn).to_be_focused()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("o")  # typing a letter moves to the first match
    expect(page.locator("#f-demo-fy-list .lb-opt.is-active")).to_have_text("October")
    page.keyboard.press("Escape")  # closes without changing the value
    expect(btn).to_have_attribute("aria-expanded", "false")
    assert sel.input_value() == "5"
    assert page.errors == []  # type: ignore[attr-defined]


def test_listbox_groups_second_lines_and_note(browser: Any, live: tuple[int, str]) -> None:
    expect = pytest.importorskip("playwright.sync_api").expect
    page = _page(browser, live)
    page.goto(_url(live, "/_components/9b"))
    page.wait_for_function("window.Alpine !== undefined")
    page.locator("#f-demo-tz-b").click()
    menu = page.locator("#f-demo-tz-list")
    expect(menu.locator(".lb-grp")).to_have_text(["Detected from your browser", "Other time zones"])
    expect(menu.locator(".lb-opt.is-on .lb-sub")).to_have_text("Asia/Kolkata · UTC+05:30")
    expect(menu.locator(".lb-note")).to_contain_text("02:00")
    page.locator("#f-demo-tz-list .lb-opt", has_text="Dubai").click()
    expect(page.locator("#f-demo-tz-b")).to_have_text("Dubai")


def test_listbox_keeps_the_native_picker_on_phones(browser: Any, live: tuple[int, str]) -> None:
    expect = pytest.importorskip("playwright.sync_api").expect
    _port, session = live
    ctx = browser.new_context(
        viewport={"width": 390, "height": 800}, has_touch=True, is_mobile=True
    )
    ctx.add_cookies([{"name": "othor_session", "value": session, "domain": CO, "path": "/"}])
    page = ctx.new_page()
    page.goto(_url(live, "/_components/9b"))
    page.wait_for_function("window.Alpine !== undefined")
    expect(page.locator("#f-demo-fy")).to_be_visible()
    expect(page.locator("#f-demo-fy-b")).to_have_count(0)


def test_dialog_opens_closes_on_escape_and_returns_focus(
    browser: Any, live: tuple[int, str]
) -> None:
    expect = pytest.importorskip("playwright.sync_api").expect
    page = _page(browser, live)
    page.goto(_url(live, "/_components/9b"))
    trigger = page.locator('[data-dialog-open="demo-dialog"]')
    trigger.click()
    dialog = page.locator("#demo-dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("technical textiles")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(trigger).to_be_focused()
    trigger.click()
    dialog.locator(".dlg-head button[aria-label='Close']").click()
    expect(dialog).to_be_hidden()
    assert page.errors == []  # type: ignore[attr-defined]


def test_a_listbox_menu_stays_on_screen_and_on_top(browser: Any, live: tuple[int, str]) -> None:
    """#18: on Settings the menus sat at the control's left edge, ran past the card, were
    clipped by it, and scrolled the page sideways. Now: on the window, lined up with the
    control's right edge, over everything, and the page doesn't move."""
    page = _page(browser, live)
    page.set_viewport_size({"width": 1440, "height": 657})  # the window #18 was filed from
    page.goto(_url(live, "/settings"))
    page.wait_for_function("window.Alpine !== undefined")
    for fid in ("f-fy", "f-tz"):
        btn = page.locator(f"#{fid}-b")
        btn.click()
        menu = page.locator(f"#{fid}-list")
        m, b = menu.bounding_box(), btn.bounding_box()
        assert page.evaluate("scrollX") == 0, fid
        assert m["x"] >= 0 and m["x"] + m["width"] <= 1440, (fid, m)
        assert m["y"] >= 0 and m["y"] + m["height"] <= 657, (fid, m)
        assert abs((m["x"] + m["width"]) - (b["x"] + b["width"])) <= 1, (fid, m, b)
        top = page.evaluate(
            "id => { const r = document.getElementById(id).getBoundingClientRect();"
            " const e = document.elementFromPoint(r.left + r.width / 2, r.bottom - 6);"
            " return e !== null && e.closest('#' + id) !== null; }",
            f"{fid}-list",
        )
        assert top, f"{fid}: the menu is covered or clipped at its bottom"
        page.keyboard.press("Escape")
    assert not page.errors  # type: ignore[attr-defined]


def test_code_boxes_start_over_after_a_wrong_code_and_backspace_clears(
    browser: Any, live: tuple[int, str]
) -> None:
    """#41: after a wrong code the six boxes kept the old digits and were hard to clear. Now the
    first key starts over (all six cleared, the digit typed goes in the first box, nothing is
    submitted with a mix of old and new digits); Backspace clears one digit a press; typing into
    a filled box replaces it."""
    page = _page(browser, live)
    page.goto(_url(live, "/_components"))
    wrong = page.locator('[data-demo="code-wrong"] .digit')
    wrong.nth(0).focus()
    page.keyboard.press("1")
    vals = [wrong.nth(i).input_value() for i in range(6)]
    assert vals == ["1", "", "", "", "", ""], vals
    assert page.evaluate("document.activeElement.name") == "wrong1"
    page.keyboard.type("2345")
    assert [wrong.nth(i).input_value() for i in range(6)] == ["1", "2", "3", "4", "5", ""]
    # Backspace: one press a digit, from the box after the last digit backwards
    page.keyboard.press("Backspace")
    page.keyboard.press("Backspace")
    assert [wrong.nth(i).input_value() for i in range(6)] == ["1", "2", "3", "", "", ""]
    assert page.evaluate("document.activeElement.name") == "wrong3"
    # typing into a filled box replaces its digit
    wrong.nth(0).focus()
    page.keyboard.press("9")
    assert wrong.nth(0).input_value() == "9"
    assert not page.errors  # type: ignore[attr-defined]


def test_a_long_ontology_line_reads_more_in_place(browser: Any, live: tuple[int, str]) -> None:
    """#48: a long line shows its first 280 characters and Read more; Read more shows it all in
    place and becomes Show less."""
    page = _page(browser, live)
    page.goto(_url(live, "/_components"))
    page.wait_for_function("window.Alpine !== undefined")
    row = page.locator('[data-demo="ont-long"]')
    short = row.locator(".ont-short")
    assert short.is_visible() and short.inner_text().endswith("…")
    btn = row.get_by_role("button", name="Read more")
    btn.click()
    short.wait_for(state="hidden", timeout=3000)
    row.get_by_role("button", name="Show less").wait_for(timeout=3000)
    assert not page.errors  # type: ignore[attr-defined]
