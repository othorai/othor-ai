# SPDX-License-Identifier: Apache-2.0
"""Rename the company (owner, 24 Nov 2026): Settings › Company's name row in its states, on the
sample company. Desktop and phone. Dev only, run by hand (needs `make dev && make seed`):

    .venv/bin/python -m pytest tests/ui/preview_company_rename.py -s -p no:cacheprovider \\
        -o addopts=""

Writes ~/Desktop/othor-preview/company-rename/: 01 view, 02 editing, 03 error, 04 saved (the
toast, the header and the menu with the new name, no reload), 05 another page after it."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

from tests.ui.preview_v032_chat13_bm24 import _serve
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / "company-rename"
NEW = "Indigo Thread Company"


def _keep(out: list[str], e: Any) -> None:
    out.append(str(e))


def test_preview_company_rename(night: Any) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    env = night[0]
    with _serve(env.app, env.client) as (app, cookies), sync_playwright() as pw:
        br = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        for width, tag in ((1440, ""), (390, "-phone")):
            ctx = br.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            errors: list[str] = []
            pg.on("pageerror", partial(_keep, errors))
            loads: list[str] = []
            pg.on("load", partial(_keep, loads))

            def shot(name: str, pg: Any = pg, tag: str = tag) -> None:
                pg.wait_for_timeout(350)
                pg.screenshot(path=str(OUT / f"{name}{tag}.png"), full_page=True)

            pg.goto(app + "/settings", wait_until="networkidle")
            old = pg.locator("#name-row .title").nth(1).inner_text()
            shot("01-view")
            pg.click("#name-row button:has-text('Rename')")
            pg.wait_for_selector("#f-company_name")
            shot("02-editing")
            pg.fill("#f-company_name", "   ")
            pg.click("#name-row button:has-text('Save')")
            pg.wait_for_selector("#e-company_name")
            shot("03-error")
            pg.fill("#f-company_name", NEW)
            pg.click("#name-row button:has-text('Save')")
            pg.wait_for_selector("#name-row button:has-text('Rename')")
            assert pg.inner_text("#crumb-company") == NEW
            assert pg.inner_text("#nav-company").strip().lower() == NEW.lower()
            assert len(loads) == 1, "the rename reloaded the page"
            shot("04-saved")
            pg.goto(app + "/home", wait_until="networkidle")
            shot("05-home-after")
            assert not errors, errors
            # back to the old name for the next width, in place as the owner would
            pg.goto(app + "/settings", wait_until="networkidle")
            pg.click("#name-row button:has-text('Rename')")
            pg.fill("#f-company_name", old)
            pg.click("#name-row button:has-text('Save')")
            pg.wait_for_selector("#name-row button:has-text('Rename')")
            ctx.close()
        br.close()
    print("previews:", OUT)
