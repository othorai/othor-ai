# SPDX-License-Identifier: Apache-2.0
"""What to do renamed Action plan (owner, 22 Nov 2026): the page at /action-plan, Home's
"Today's action plan" card and the brief's "Action plan" section, and the old address redirecting.
Dev only, run by hand (needs `make dev && make seed`):

    .venv/bin/python -m pytest tests/ui/preview_v0332_action_plan.py -s -p no:cacheprovider \\
        -o addopts=""

Writes ~/Desktop/othor-preview/v0.33.2/action-plan/ (desktop and phone)."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from othor.briefs.models import Brief
from othor.core.tenancy import tenant_scope
from tests.ui.preview_v033_what_to_do import _serve
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / "v0.33.2" / "action-plan"


def test_preview_action_plan(night) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    env, _h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        bid = s.scalars(sa.select(Brief)).one().id
    shots = [
        ("01-action-plan-page", "/action-plan", ""),
        ("02-home-card", "/home", 'section[aria-label="Today’s action plan"]'),
        ("03-brief-section", f"/brief/{bid}", 'section[aria-label="Action plan"]'),
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
        ctx = br.new_context()
        ctx.add_cookies(cookies)  # type: ignore[arg-type]
        pg = ctx.new_page()
        pg.goto(app + "/what-to-do", wait_until="networkidle")
        print("old address lands on:", pg.url.rsplit("/", 1)[-1])
        assert pg.url.endswith("/action-plan")
        ctx.close()
        br.close()
