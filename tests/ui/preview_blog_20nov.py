# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (the drawn figures are inline CSS, one rule per line)
"""The graphics inside the 20 Nov 2026 blog posts (owner: each post its own header and 2-3 graphics
inside). Dev only, run by hand (needs `make dev && make seed`, the 1 Nov night):

    .venv/bin/python -m pytest tests/ui/preview_blog_20nov.py -s -p no:cacheprovider -o addopts=""

Screenshots of the sample company (Sweep's tags, What's ahead, the Leadership card, the finding
row) and two figures drawn from the posts' computed figures, written as webp into
othor_site/static/site/blog/<slug>/."""

from __future__ import annotations

import io
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
BLOG = ROOT / "othor_site" / "static" / "site" / "blog"
ODDS = "words-of-estimative-probability"
FLAT = "a-flat-month-can-hide-a-fall"
FONT = '<link rel="stylesheet" href="{app}/static/css/fonts.css">'
INK, MUTED, RULE, CYAN, NEG = "#1F2A37", "#5B6675", "#E6EAF0", "#2CCAFF", "#D9534F"


def _port() -> int:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return int(so.getsockname()[1])


def _save(png: bytes, path: Path) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.open(io.BytesIO(png)).convert("RGB").save(path, "WEBP", quality=90, method=6)


def _odds_html(app: str) -> str:
    """Kent's five words as bands of odds, and 1951's 'serious possibility' read as 20-80%."""
    rows = [
        ("Almost certain", 93, 6),
        ("Probable", 75, 12),
        ("Chances about even", 50, 10),
        ("Probably not", 30, 10),
        ("Almost certainly not", 7, 5),
    ]
    w = 680

    def bar(c: float, h: float, colour: str, dashed: bool = False) -> str:
        left, width = (c - h) / 100 * w, 2 * h / 100 * w
        style = (
            f"border:2px dashed {MUTED};background:transparent"
            if dashed
            else f"background:{colour}"
        )
        tick = "" if dashed else f'<i style="left:{c / 100 * w - 2:.0f}px"></i>'
        return f'<b style="left:{left:.0f}px;width:{width:.0f}px;{style}"></b>{tick}'

    body = "".join(
        f'<div class="r"><span>{name}</span><div class="t">{bar(c, h, "rgba(44,202,255,.35)")}</div>'
        f"<em>{c}% ± {h}</em></div>"
        for name, c, h in rows
    )
    body += (
        '<div class="r last"><span>“A serious possibility” (1951)</span><div class="t">'
        f"{bar(50, 30, '', dashed=True)}</div><em>read as 20% to 80%</em></div>"
    )
    ticks = "".join(f'<u style="left:{v / 100 * w:.0f}px">{v}%</u>' for v in (0, 25, 50, 75, 100))
    return f"""<!doctype html><html><head><meta charset="utf-8">{FONT.format(app=app)}<style>
body{{margin:0;font-family:Inter,system-ui,sans-serif;color:{INK};background:#fff}}
.f{{width:1200px;padding:40px 48px 44px;box-sizing:border-box}}
.k{{font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};margin:0 0 22px}}
.r{{display:flex;align-items:center;gap:20px;height:46px}} .r span{{width:240px;flex:none;font-size:16px;font-weight:500}}
.r em{{width:140px;flex:none;font-style:normal;font-size:14px;color:{MUTED}}}
.t{{position:relative;flex:none;width:{w}px;height:18px}} .t b{{position:absolute;top:0;height:18px;border-radius:9px;box-sizing:border-box}}
.t i{{position:absolute;top:-3px;width:4px;height:24px;border-radius:2px;background:#10B5A0}}
.last{{margin-top:10px;border-top:1px solid {RULE};padding-top:10px;height:52px}} .last span{{color:{MUTED}}}
.ax{{position:relative;margin-left:260px;width:{w}px;height:22px;border-top:1px solid {RULE};margin-top:8px}}
.ax u{{position:absolute;top:6px;transform:translateX(-50%);text-decoration:none;font-size:12px;color:{MUTED}}}
</style></head><body><div class="f"><p class="k">Sherman Kent, 1964: words and the odds they stand for</p>
{body}<div class="ax">{ticks}</div></div></body></html>"""


def _net_gross_html(app: str) -> str:
    """The post's computed figures: the total moved ₹0.19 Cr; underneath, ₹2.61 Cr moved."""
    unit = 240  # px per ₹1 Cr
    return f"""<!doctype html><html><head><meta charset="utf-8">{FONT.format(app=app)}<style>
body{{margin:0;font-family:Inter,system-ui,sans-serif;color:{INK};background:#fff}}
.f{{width:1200px;padding:40px 48px 44px;box-sizing:border-box}}
.k{{font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};margin:0 0 26px}}
.r{{display:flex;align-items:center;gap:20px;margin-bottom:22px}} .r span{{width:260px;flex:none;font-size:16px;font-weight:500}}
.r .v{{width:auto}}
.bars{{display:flex;height:34px}} .bars b{{height:34px}} .v{{font-size:16px;font-weight:600;margin-left:14px}}
.n{{font-size:14px;color:{MUTED};margin:6px 0 0 280px}}
</style></head><body><div class="f"><p class="k">Indigo Thread Co., September to October (₹ Cr)</p>
<div class="r"><span>What the total shows</span><div class="bars"><b style="width:{0.19 * unit:.0f}px;background:#9AA5B1;border-radius:6px"></b></div><span class="v">₹0.19 Cr down</span></div>
<div class="r"><span>What moved underneath</span><div class="bars"><b style="width:{1.40 * unit:.0f}px;background:{NEG};border-radius:6px 0 0 6px"></b><b style="width:{1.21 * unit:.0f}px;background:{CYAN};border-radius:0 6px 6px 0"></b></div><span class="v">₹2.61 Cr</span></div>
<p class="n"><span style="color:{NEG}">■</span> South −₹1.40 Cr &nbsp; <span style="color:{CYAN}">■</span> West +₹1.21 Cr &nbsp; · about 14 times the change in the total</p>
</div></body></html>"""


def test_blog_graphics(night) -> None:  # noqa: F811
    subprocess.run([sys.executable, "scripts/build_css.py"], cwd=ROOT, check=True)
    env, _h, cid, _rid, svc = night
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
    cookies = [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain or f"indigothread.{BASE}",
            "path": c.path or "/",
        }
        for c in env.client.cookies.jar
    ]
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        ctx = b.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
        ctx.add_cookies(cookies)  # type: ignore[arg-type]
        pg = ctx.new_page()

        def clip(first: str, last: str, pad: int = 16) -> dict[str, float]:
            a = pg.locator(first).first.bounding_box()
            z = pg.locator(last).first.bounding_box()
            assert a and z, (first, last)
            x0, y0 = min(a["x"], z["x"]) - pad, a["y"] - pad
            x1 = max(a["x"] + a["width"], z["x"] + z["width"]) + pad
            return {"x": x0, "y": y0, "width": x1 - x0, "height": z["y"] + z["height"] - y0 + pad}

        # Sweep: the four numbers and the first findings with their tags
        pg.goto(app + "/sweep", wait_until="networkidle")
        _save(
            pg.screenshot(clip=clip(".det-kpi", ".finding-row:nth-of-type(3)")),
            BLOG / ODDS / "sweep-tags.webp",
        )
        # the net-hiding-gross finding row on its own
        _save(
            pg.screenshot(clip=clip(".finding-row", ".finding-row")),
            BLOG / FLAT / "sweep-finding.webp",
        )
        # the brief's What's ahead, its likely ranges and confidence words
        pg.goto(app + f"/brief/{brief}", wait_until="networkidle")
        pg.evaluate("document.querySelector('section.ahead').scrollIntoView()")
        _save(
            pg.screenshot(clip=clip("section.ahead", "section.ahead .ahead-row:nth-of-type(2)")),
            BLOG / ODDS / "whats-ahead.webp",
        )
        # the Leadership card on Morning Brief, its region chart
        pg.goto(app + "/brief", wait_until="networkidle")
        _save(pg.locator(".brief-card").first.screenshot(), BLOG / FLAT / "leadership-card.webp")
        # the two drawn figures
        for html, path in (
            (_odds_html(app), BLOG / ODDS / "odds-bands.webp"),
            (_net_gross_html(app), BLOG / FLAT / "net-vs-gross.webp"),
        ):
            pg.set_content(html, wait_until="networkidle")
            pg.evaluate("document.fonts.ready")
            _save(pg.locator(".f").screenshot(), path)
        b.close()
    server.should_exit = True
    th.join(5)
    print("graphics in", BLOG)
