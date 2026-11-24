# SPDX-License-Identifier: Apache-2.0
"""Previews of What to do (Morning Brief 2.5, #110) for the owner. Dev only:

    .venv/bin/python -m pytest tests/ui/preview_v033_what_to_do.py -s -p no:cacheprovider \
        -o addopts=""

Needs `make dev && make seed`. The sample's 1 Nov night as the run makes it; the held state is a
real Snooze posted the way the row's button posts it. Desktop (1440) and phone (390) into
~/Desktop/othor-preview/v0.33/what-to-do/."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from othor.briefs.models import Brief
from othor.core.tenancy import tenant_scope
from tests.ui.preview_v032_chat13_bm24 import _serve
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / "v0.33" / "what-to-do"


def _shoot(app: str, cookies: list[dict[str, str]], shots: list[tuple[str, str, str]]) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        for width, tag in ((1440, ""), (390, "-phone")):
            ctx = b.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            for name, path, at in shots:
                pg.goto(app + path, wait_until="networkidle")
                pg.wait_for_timeout(500)
                if at:
                    pg.locator(at).first.screenshot(path=str(OUT / f"{name}{tag}.png"))
                else:
                    pg.screenshot(path=str(OUT / f"{name}{tag}.png"), full_page=True)
            ctx.close()
        b.close()


def test_preview_what_to_do(night) -> None:  # noqa: F811
    from othor.briefs.models import BriefIssue
    from othor.engine.models import Run

    OUT.mkdir(parents=True, exist_ok=True)
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        b = s.scalars(sa.select(Brief)).one()
        i = s.scalars(sa.select(BriefIssue).where(BriefIssue.brief_id == b.id)).one()
        n, bid = s.get(Run, i.run_id).number, b.id
    with _serve(env.app, env.client) as (app, cookies):
        _shoot(
            app,
            cookies,
            [
                ("01-what-to-do-page", "/what-to-do", ""),
                ("02-brief-section", f"/brief/{bid}", 'section[aria-label="What to do"]'),
                ("03-home-card", "/home", 'section[aria-label="What to do today"]'),
                ("04-read-only", f"/brief/{bid}/shared", 'section[aria-label="What to do"]'),
                ("05-components", "/_components", '[data-demo="what-to-do"]'),
            ],
        )
        tok = env.csrf(h + "/what-to-do")
        env.client.post(
            h + f"/sweep/finding/83f4e966442fc01cd415cf2c/snooze?run={n}&back=what-to-do",
            data={"csrf": tok},
        )
        _shoot(app, cookies, [("06-what-to-do-held", "/what-to-do", "")])
