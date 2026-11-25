# SPDX-License-Identifier: Apache-2.0
"""Previews of the built #76 screens (numbers suggested for the company's size). Dev only, run by
hand (needs `make dev && make seed`):

    .venv/bin/python -m pytest tests/ui/preview_size_76.py -s -p no:cacheprovider -o addopts=""

The sample's 1 Nov night, served for real; nothing is laid over the page. Desktop (1440) and
phone (390) into ~/Desktop/othor-preview/next-1126/built-76/:
- 076-a-business-model: To review, with the size line and the "New number" rows;
- 076-b-home-suggested: the top one on Home's Suggested by Othor AI;
- 076-c-levels-checklist: the Business Model checklist. The sample has every item, so for this
  shot only its named customers are set aside first (a company without them), and the
  suggestion that fits "Named customers" shows under it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from othor.core.tenancy import tenant_scope
from othor.ontology import service, size_numbers
from tests.ui.preview_v032_chat13_bm24 import _serve
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / "next-1126" / "built-76"


def _without_named_customers(svc: Any, cid: Any) -> None:
    with svc.sf() as s, tenant_scope(cid):
        changes = [service.retire("customer", r.key) for r in service.active(s, ["customer"])]
        for r in service.active(s, ["breakdown"]):
            if (r.body or {}).get("entity"):
                body = {k: v for k, v in r.body.items() if k != "entity"}
                changes.append(service.put("breakdown", r.key, body))
        service.apply(s, changes, actor="preview", reason="preview: no named customers")
        s.commit()


def _shoot(app: str, cookies: list[dict[str, str]], shots: list[tuple[str, str, str]]) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        br = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        for width, tag in ((1440, ""), (390, "-phone")):
            ctx = br.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            errors: list[str] = []
            pg.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            for name, path, at in shots:
                pg.goto(app + path, wait_until="networkidle")
                loc = pg.locator(at).first
                assert loc.count(), (name, at)
                loc.screenshot(path=str(OUT / f"{name}{tag}.png"))
            assert not errors, errors
            ctx.close()


def test_preview_size_76(night) -> None:  # noqa: F811
    env, _h, cid, _rid, svc = night
    OUT.mkdir(parents=True, exist_ok=True)
    with svc.sf() as s, tenant_scope(cid):
        print("suggested:", [(p.text, p.evidence.get("line")) for p in size_numbers.open_rows(s)])
    with _serve(env.app, env.client) as (app, cookies):
        _shoot(
            app,
            cookies,
            [
                ("076-a-business-model", "/ontology", "#proposals"),
                ("076-b-home-suggested", "/home", "#suggested"),
            ],
        )
        _without_named_customers(svc, cid)
        _shoot(
            app,
            cookies,
            [
                (
                    "076-c-levels-checklist",
                    "/ontology?tab=level",
                    'section[aria-label="Business Model level"]',
                )
            ],
        )
    print("previews:", OUT)
