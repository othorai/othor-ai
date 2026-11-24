# SPDX-License-Identifier: Apache-2.0
"""Business Model page (owner, 23 Nov 2026): To review always on top (the proposals moved here from
Home), then tabs The business & goals · Numbers & words · Patterns · the level; the level's
checklist always open. Desktop and phone, every tab. Dev only, run by hand
(needs `make dev && make seed`):

    .venv/bin/python -m pytest tests/ui/preview_v0338_business_model.py -s -p no:cacheprovider \\
        -o addopts=""

Writes ~/Desktop/othor-preview/v0.33.8-business-model/."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

from tests.ui.preview_v033_what_to_do import _serve  # type: ignore[attr-defined]
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def _keep(out: list[str], e: Any) -> None:
    out.append(str(e))


OUT = Path.home() / "Desktop" / "othor-preview" / "v0.33.8-business-model"


def test_preview_business_model(night: Any) -> None:  # noqa: F811
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
            pg.goto(app + "/ontology", wait_until="networkidle")
            pg.wait_for_timeout(400)
            pg.screenshot(path=str(OUT / f"01-business{tag}.png"), full_page=True)
            for i, key in enumerate(("numbers", "patterns", "level"), start=2):
                pg.click(f"#tab-{key}")
                pg.wait_for_timeout(300)
                pg.screenshot(path=str(OUT / f"0{i}-{key}{tag}.png"), full_page=True)
            assert not errors, errors
            ctx.close()
        br.close()
    print("previews:", OUT)
