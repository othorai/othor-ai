# SPDX-License-Identifier: Apache-2.0
"""0.33.6 order (owner, 22 Nov 2026): Home's "Today's action plan" below the briefs and above
Suggested by Othor AI; every brief's Action plan last, just above Since last week. Full pages of
Home and the brief (desktop and phone) and the brief's PDF. Dev only, run by hand (needs `make dev
&& make seed`):

    .venv/bin/python -m pytest tests/ui/preview_v0336_order.py -s -p no:cacheprovider \\
        -o addopts=""

Writes ~/Desktop/othor-preview/v0.33.6/."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from othor.briefs.models import Brief
from othor.core.tenancy import tenant_scope
from tests.ui.preview_v033_what_to_do import _serve
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / "v0.33.6"


def test_preview_order(night) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        bid = s.scalars(sa.select(Brief)).one().id
    pdf = env.client.get(h + f"/brief/{bid}/pdf")
    if pdf.status_code == 200 and pdf.content[:4] == b"%PDF":
        (OUT / "03-brief.pdf").write_bytes(pdf.content)
    with _serve(env.app, env.client) as (app, cookies), sync_playwright() as pw:
        br = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        for width, tag in ((1440, ""), (390, "-phone")):
            ctx = br.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            errors: list[str] = []
            pg.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            for name, path in (("01-home", "/home"), ("02-brief", f"/brief/{bid}")):
                pg.goto(app + path, wait_until="networkidle")
                pg.wait_for_timeout(400)
                pg.screenshot(path=str(OUT / f"{name}{tag}.png"), full_page=True)
            assert not errors, errors
            ctx.close()
    print("previews:", OUT)
