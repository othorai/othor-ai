# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (the gallery is the templates' own markup, one toast per line)
"""Previews of every toast for the owner (calm toasts, 20 Nov 2026). Dev only, run by hand on each
branch to compare (needs `make dev && make seed`):

    OTHOR_PREVIEW_DIR=toasts/after .venv/bin/python -m pytest tests/ui/preview_toasts.py -s -p no:cacheprovider -o addopts=""

Shots: Morning Brief with a new brief's toast over the cards (desktop, phone), a brief card with
keyboard focus, and a gallery of every toast in the app, in the markup its template writes."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
OUT = Path.home() / "Desktop" / "othor-preview" / os.environ.get("OTHOR_PREVIEW_DIR", "toasts")

DOT, ERR = '<span class="dot accent"></span>', '<span class="dot neg"></span>'
UNDO = '<form><button class="action btn quiet" type="button">Undo</button></form>'
GALLERY = [
    (
        "A new brief",
        f'<div class="toast" role="status">{DOT}<span>Customer retention created · first brief tomorrow 6:00 AM IST</span><form><button class="btn quiet" type="button">Undo</button></form></div>',
    ),
    (
        "Metrics added (setup)",
        f'<div class="toast" role="status">{DOT}<span>3 metrics added to the Business Model<span class="sub">Business Model v7 · in tonight’s run, or refresh everything now</span></span>{UNDO}<button class="btn" type="button">Refresh everything</button></div>',
    ),
    (
        "Metrics confirmed (setup)",
        f'<div class="toast" role="status">{DOT}<span>12 metrics confirmed<span class="sub">Each one is saved with your name and the time.</span></span>{UNDO}</div>',
    ),
    (
        "A table set aside (Data sources)",
        f'<div class="toast" role="status">{DOT}<span>sales_dw.fact_order · Ignored · Not needed</span><button class="action btn quiet" type="button">Undo</button></div>',
    ),
    (
        "A setting saved (Settings)",
        f'<div class="toast" role="status">{DOT}<span>Time zone saved</span><button class="action btn quiet" type="button">Undo</button></div>',
    ),
    (
        "Logo saved (no action)",
        f'<div class="toast" role="status">{DOT}<span>Logo saved</span></div>',
    ),
    ("Copied", f'<div class="toast" role="status">{DOT}<span>Copied</span></div>'),
    (
        "Something failed (Retry)",
        f'<div class="toast err" role="status">{ERR}<span>Couldn’t save that. Nothing changed.<span class="sub">Reference 18e2502f1a89</span></span><button class="btn quiet retry" type="button">Retry</button></div>',
    ),
    ("Offline", f'<div class="toast err" role="status">{ERR}<span>You’re offline.</span></div>'),
]


_BAR = """
.toast-dock { right: auto !important; bottom: auto !important; transform: none !important; align-items: stretch !important; }
.toast-dock .toast { max-width: none; width: 100%; border-radius: 0; box-shadow: none; border-width: 0 0 1px; border-color: var(--color-accent-border);
  background: var(--color-accent-tint-2); padding-left: 32px; padding-right: 32px; animation: none; }
"""
BAR_STYLES = {
    # over the top bar: the bar's own height, the breadcrumb hidden under it while it shows
    "over-the-top-bar": _BAR
    + ".toast-dock { top: var(--bar-top) !important; } .toast-dock .toast { min-height: var(--bar-h); box-sizing: border-box; }",
    # just under the top bar, across the main panel, pushing nothing (it floats over the page's top)
    "under-the-top-bar": _BAR
    + ".toast-dock { top: calc(var(--bar-top) + var(--bar-h)) !important; }",
}


def _port() -> int:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return int(so.getsockname()[1])


def test_preview_toasts(night) -> None:  # noqa: F811
    subprocess.run([sys.executable, "scripts/build_css.py"], cwd=ROOT, check=True)
    env, _h, cid, _rid, svc = night
    from othor.briefs import service
    from othor.briefs.models import Brief

    with svc.sf() as s, tenant_scope(cid):
        base = s.scalars(sa.select(Brief).order_by(Brief.created_at)).first()
        assert base is not None
        new = service.create(
            s, {**service.current(s, base).body, "name": "Customer retention"}, actor="preview"
        )
        s.commit()
        nid = new.id
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
    cookies = [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain or f"indigothread.{BASE}",
            "path": c.path or "/",
        }
        for c in env.client.cookies.jar
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        for width, tag in ((1440, ""), (390, "-phone")):
            ctx = b.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            pg.clock.install()  # the toast stays for the shot
            pg.goto(f"{app}/brief?created={nid}", wait_until="networkidle")
            pg.wait_for_timeout(400)
            pg.screenshot(path=str(OUT / f"1-new-brief-toast{tag}.png"))
            # setup's "metrics added" toast, with Refresh everything, in the same place
            pg.evaluate(
                "h => { document.querySelector('.toast-dock').innerHTML = h; }", GALLERY[1][1]
            )
            pg.wait_for_timeout(200)
            pg.screenshot(path=str(OUT / f"5-refresh-everything{tag}.png"))
            # owner, 20 Nov 2026: two more places to try, the full width of the main panel, over the
            # top bar or just under it (a prototype injected here; nothing in the app yet)
            for style, css in BAR_STYLES.items():
                pg.goto(f"{app}/brief?created={nid}", wait_until="networkidle")
                pg.add_style_tag(content=css)
                pg.evaluate(
                    """() => { const h = document.querySelector('.app-header'); const r = h.getBoundingClientRect();
                    const d = document.querySelector('.toast-dock'); d.style.left = r.left + 'px'; d.style.width = r.width + 'px';
                    d.style.setProperty('--bar-top', r.top + 'px'); d.style.setProperty('--bar-h', r.height + 'px'); }"""
                )
                pg.wait_for_timeout(300)
                pg.screenshot(path=str(OUT / f"4-{style}{tag}.png"))
            if not tag:
                # a brief card with keyboard focus (after Undo, focus lands on a card)
                pg.keyboard.press("Tab")
                pg.locator("a.brief-card").first.focus()
                pg.wait_for_timeout(200)
                box = pg.locator("a.brief-card").first.bounding_box()
                assert box
                pg.screenshot(
                    path=str(OUT / "2-brief-card-focus.png"),
                    clip={
                        "x": box["x"] - 24,
                        "y": box["y"] - 24,
                        "width": box["width"] + 48,
                        "height": box["height"] + 48,
                    },
                )
                # every toast, in the app's own CSS
                css = pg.eval_on_selector_all('link[rel="stylesheet"]', "ls => ls.map(l => l.href)")
                links = "".join(f'<link rel="stylesheet" href="{h}">' for h in css)
                rows = "".join(
                    f'<div style="display:flex;align-items:center;gap:24px;margin:0 0 18px"><p style="width:220px;margin:0;font:500 13px Inter;color:#5B6675">{name}</p><div>{html}</div></div>'
                    for name, html in GALLERY
                )
                pg.set_content(
                    f'<!doctype html><html><head><meta charset="utf-8">{links}</head><body style="background:#F5F7FA;padding:40px"><div id="g">{rows}</div></body></html>',
                    wait_until="networkidle",
                )
                pg.wait_for_timeout(300)
                pg.locator("#g").screenshot(path=str(OUT / "3-every-toast.png"))
            ctx.close()
        b.close()
    server.should_exit = True
    th.join(5)
    print("previews in", OUT)
