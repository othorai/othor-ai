# SPDX-License-Identifier: Apache-2.0
"""Previews of the main screens for the owner (v0.25 M25.3, typography). Dev only, run by hand:

    OTHOR_PREVIEW_DIR=v0.25/type/before .venv/bin/python -m pytest tests/ui/preview_screens.py \
        -s -p no:cacheprovider -o addopts=""

Needs `make dev && make seed` (the 1 Nov night). Builds the CSS first, then photographs each screen
signed in, desktop (1440) and phone (390), into ~/Desktop/othor-preview/<OTHOR_PREVIEW_DIR>/."""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / os.environ.get("OTHOR_PREVIEW_DIR", "screens")
ROOT = Path(__file__).resolve().parents[2]


def _port() -> int:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return int(so.getsockname()[1])


def test_preview(night) -> None:  # noqa: F811
    import sys

    subprocess.run([sys.executable, "scripts/build_css.py"], cwd=ROOT, check=True)
    env, _h, cid, _rid, svc = night
    svc.settings.public_site = True  # the website on the bare host, as othor_site's tests do
    shot = os.environ.get("OTHOR_PREVIEW_SHOT")  # a What's new cover's shot: one page, in USD
    if shot:
        from othor.core.models import Company
        from othor.core.tenancy import system_scope

        with svc.sf() as s, system_scope("preview"):
            co = s.scalars(sa.select(Company).where(Company.id == cid)).one()
            co.currency, co.timezone = "USD", "America/New_York"
            s.commit()
    OUT.mkdir(parents=True, exist_ok=True)
    from othor.briefs.models import Brief

    with svc.sf() as s, tenant_scope(cid):
        brief = s.scalars(sa.select(Brief.id).order_by(Brief.created_at)).first()
    import uvicorn

    port = _port()
    server = uvicorn.Server(
        uvicorn.Config(cast(Any, env.app), host="127.0.0.1", port=port, log_level="warning")
    )
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.05)
    app = f"http://indigothread.{BASE}:{port}"
    site = f"http://{BASE}:{port}"
    cookies = [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain or f"indigothread.{BASE}",
            "path": c.path or "/",
        }
        for c in env.client.cookies.jar
    ]
    shots = [
        ("home", app + "/home"),
        ("briefs", app + "/brief"),
        ("brief", app + f"/brief/{brief}"),
        ("sweep", app + "/sweep"),
        ("summary", app + "/summary"),
        ("business-model", app + "/ontology"),
        ("sources", app + "/sources"),
        ("settings", app + "/settings"),
        ("site-home", site + "/"),
        ("site-pricing", site + "/pricing"),
        ("site-whats-new", site + "/whats-new"),
        ("site-security", site + "/security"),
        ("sign-in", site + "/login"),
    ]
    if shot:  # OTHOR_PREVIEW_SHOT=<page path>:<file>, e.g. /brief/{brief}:smooth-lines.png
        path, fname = shot.split(":", 1)
        shots = [(fname.removesuffix(".png"), app + path.replace("{brief}", str(brief)))]
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        for width, tag in ((1280, ""),) if shot else ((1440, ""), (390, "-phone")):
            ctx = b.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            for name, url in shots:
                pg.goto(url, wait_until="networkidle")
                pg.screenshot(path=str(OUT / f"{name}{tag}.png"), full_page=not shot)
            ctx.close()
        b.close()
    server.should_exit = True
    th.join(5)
    print("previews in", OUT)
