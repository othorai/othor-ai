# SPDX-License-Identifier: Apache-2.0
"""0.33.1 previews (owner, 22 Nov 2026: "go ahead and fix it all"): What to do with bold figures,
Watch this opening in place on What to do and on a brief, Watch on the brief cards, Home's card,
and the chosen tab kept after a hold (checked in the browser). Dev only, run by hand (needs
`make dev && make seed`):

    .venv/bin/python -m pytest tests/ui/preview_v0331_briefs.py -s -p no:cacheprovider \\
        -o addopts=""

Writes ~/Desktop/othor-preview/v0.33.1/briefs/ (desktop and phone)."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from othor.briefs.models import Brief
from othor.core.tenancy import tenant_scope
from tests.ui.preview_v033_what_to_do import _serve
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / "v0.33.1" / "briefs"
DISPATCH = "83f4e966442fc01cd415cf2c"


def test_preview_v0331_briefs(night) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    from othor.briefs.models import BriefIssue
    from othor.engine.models import Run

    OUT.mkdir(parents=True, exist_ok=True)
    env, _h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        b = s.scalars(sa.select(Brief)).one()
        i = s.scalars(sa.select(BriefIssue).where(BriefIssue.brief_id == b.id)).one()
        n, bid = s.get(Run, i.run_id).number, b.id
    watch = f"?panel=watch&fp={DISPATCH}&run={n}"
    shots = [
        ("01-what-to-do-page", "/what-to-do", ""),
        ("02-watch-in-place-what-to-do", "/what-to-do" + watch, ""),
        ("03-brief-cards-with-watch", f"/brief/{bid}", ".brief-items"),
        ("04-brief-what-to-do", f"/brief/{bid}", 'section[aria-label="What to do"]'),
        ("05-watch-in-place-brief", f"/brief/{bid}" + watch, ""),
        ("06-home-card", "/home", 'section[aria-label="What to do today"]'),
    ]
    with _serve(env.app, env.client) as (app, cookies), sync_playwright() as pw:
        br = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        for width, tag in ((1440, ""), (390, "-phone")):
            ctx = br.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            errors: list[str] = []
            pg.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            for name, path, at in shots:
                pg.goto(app + path, wait_until="networkidle")
                pg.wait_for_timeout(400)
                if at:
                    pg.locator(at).first.screenshot(path=str(OUT / f"{name}{tag}.png"))
                else:
                    pg.screenshot(path=str(OUT / f"{name}{tag}.png"), full_page=True)
            assert not errors, errors
            ctx.close()
        # the tab kept after a hold (0.33.1): pick Leadership, snooze a row in place
        ctx = br.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        ctx.add_cookies(cookies)  # type: ignore[arg-type]
        pg = ctx.new_page()
        pg.goto(app + "/what-to-do", wait_until="networkidle")
        pg.click('[data-wtd-tab="Leadership"]')
        pg.locator('button[aria-label^="Snooze"]').first.click()
        pg.wait_for_timeout(1200)
        on = pg.get_attribute('[data-wtd-tab="Leadership"]', "aria-current")
        pg.screenshot(path=str(OUT / "07-tab-kept-after-snooze.png"), full_page=True)
        print("tab kept after snooze:", on == "page", "| address:", pg.url.rsplit("/", 1)[-1])
        assert on == "page"
        ctx.close()
        br.close()
