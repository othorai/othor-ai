# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (the drawn figures are inline CSS, one rule per line)
"""The graphics inside the 24 Nov 2026 blog posts (owner: each post its own header and 2-3 graphics
inside). Dev only, run by hand (needs `make dev`; the sample sources are seeded if empty):

    .venv/bin/python -m pytest tests/ui/preview_blog_24nov.py -s -p no:cacheprovider -o addopts=""

Screenshots of the sample company's 1 Nov night (Sweep's Suppressed tab, the Leadership brief's
top, Sweep's numbers) and two figures drawn from the sample's monthly net revenue, computed here
from the generator the way a direct SQL check would (tests/fixtures/sample_company/measures.py),
written as webp into othor_site/static/site/blog/<slug>/."""

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
from tests.fixtures.sample_company import measures
from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
BLOG = ROOT / "othor_site" / "static" / "site" / "blog"
SEASON = "is-this-drop-real-or-just-april"
NIGHT = "what-the-morning-brief-does-between-2-and-6-am"
FONT = '<link rel="stylesheet" href="{app}/static/css/fonts.css">'
INK, MUTED, RULE, CYAN, NEG, SOFT = "#1F2A37", "#5B6675", "#E6EAF0", "#2CCAFF", "#D9534F", "#C9D3DD"


def _port() -> int:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return int(so.getsockname()[1])


def _save(png: bytes, path: Path) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.open(io.BytesIO(png)).convert("RGB").save(path, "WEBP", quality=90, method=6)


def _months(company) -> list[tuple[str, float]]:  # type: ignore[no-untyped-def]
    """Net revenue by month in the book of record (erp_db, from Apr 2024), ₹ Cr, to Oct 2026."""
    s = measures.monthly_net_revenue(company)
    return [(m, float(v) / 1e7) for m, v in s.items() if "2024-04" <= m <= "2026-10"]


def _change(m: dict[str, float], a: str, b: str) -> tuple[float, float]:
    return m[b] - m[a], (m[b] / m[a] - 1) * 100


def _months_html(app: str, rows: list[tuple[str, float]]) -> str:
    """31 months as bars from zero; each March lit, each April and August marked."""
    m = dict(rows)
    lo, hi, h = 0.0, 5.4, 300
    names = "JFMAMJJASOND"
    bars = []
    for k, v in rows:
        mon = int(k[5:])
        colour = CYAN if mon == 3 else (NEG if mon in (4, 8) else SOFT)
        op = "1" if mon in (3, 4, 8) else ".9"
        bh = (v - lo) / (hi - lo) * h
        lab = names[mon - 1]
        yr = f"<i>{k[:4]}</i>" if mon == 4 else ""
        bars.append(
            f'<div class="b"><em>{v:.2f}</em><b style="height:{bh:.0f}px;background:{colour};opacity:{op}"></b><u>{lab}</u>{yr}</div>'
        )
    d25 = f"{_change(m, '2025-03', '2025-04')[1]:.1f}".replace("-", "−")
    d26 = f"{_change(m, '2026-03', '2026-04')[1]:.1f}".replace("-", "−")
    return f"""<!doctype html><html><head><meta charset="utf-8">{FONT.format(app=app)}<style>
body{{margin:0;font-family:Inter,system-ui,sans-serif;color:{INK};background:#fff}}
.f{{width:1200px;padding:40px 48px 40px;box-sizing:border-box}}
.k{{font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};margin:0 0 8px}}
.n{{font-size:15px;color:{MUTED};margin:0 0 26px}} .n span{{font-weight:600}}
.c{{display:flex;align-items:flex-end;gap:6px;height:{h + 40}px;border-bottom:1px solid {RULE}}}
.b{{position:relative;flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%}}
.b b{{width:100%;border-radius:4px 4px 0 0}} .b em{{font-style:normal;font-size:10px;color:{MUTED};margin-bottom:4px;writing-mode:vertical-rl;transform:rotate(180deg)}}
.b u{{position:absolute;bottom:-22px;text-decoration:none;font-size:12px;color:{MUTED}}}
.b i{{position:absolute;bottom:-42px;left:0;font-style:normal;font-size:12px;font-weight:600;color:{INK}}}
.lg{{margin:56px 0 0;font-size:14px;color:{MUTED}}} .lg span{{display:inline-block;width:12px;height:12px;border-radius:3px;margin:0 6px -1px 18px}}
</style></head><body><div class="f"><p class="k">Indigo Thread Co. · net revenue by month, April 2024 to October 2026 (₹ Cr)</p>
<p class="n">Every March is the year's high; every April falls from it (<span>{d25}%</span> in 2025, <span>{d26}%</span> in 2026), and every August dips.</p>
<div class="c">{"".join(bars)}</div>
<p class="lg"><span style="background:{CYAN};margin-left:0"></span>March, the year's close <span style="background:{NEG}"></span>April and August <span style="background:{SOFT}"></span>other months</p>
</div></body></html>"""


def _compare_html(app: str, rows: list[tuple[str, float]]) -> str:
    """April and August 2026, each against the month before and the same month last year."""
    m = dict(rows)
    unit = 26  # px per 1%
    zx = 360

    def row(label: str, a: str, b: str) -> str:
        cr, pct = _change(m, a, b)
        w = abs(pct) * unit
        left = zx - w if pct < 0 else zx
        colour = NEG if pct < 0 else CYAN
        sign = "−" if cr < 0 else "+"
        return (
            f'<div class="r"><span>{label}</span><div class="t"><i></i><b style="left:{left:.0f}px;width:{w:.0f}px;background:{colour}"></b></div>'
            f"<em>{sign}{abs(pct):.1f}% · {sign}₹{abs(cr):.2f} Cr</em></div>"
        )

    return f"""<!doctype html><html><head><meta charset="utf-8">{FONT.format(app=app)}<style>
body{{margin:0;font-family:Inter,system-ui,sans-serif;color:{INK};background:#fff}}
.f{{width:1200px;padding:40px 48px 44px;box-sizing:border-box}}
.k{{font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};margin:0 0 22px}}
h3{{font-size:17px;margin:18px 0 10px}}
.r{{display:flex;align-items:center;gap:20px;height:48px}} .r span{{width:300px;flex:none;font-size:16px}}
.t{{position:relative;flex:none;width:620px;height:26px}} .t b{{position:absolute;top:0;height:26px;border-radius:6px}}
.t i{{position:absolute;left:{zx}px;top:-8px;width:2px;height:42px;background:{MUTED};opacity:.5}}
.r em{{font-style:normal;font-size:15px;font-weight:600;white-space:nowrap}}
</style></head><body><div class="f"><p class="k">Indigo Thread Co. · net revenue, one month compared two ways</p>
<h3>April 2026, ₹{m["2026-04"]:.2f} Cr</h3>
{row("against March 2026", "2026-03", "2026-04")}
{row("against April 2025", "2025-04", "2026-04")}
<h3>August 2026, ₹{m["2026-08"]:.2f} Cr</h3>
{row("against July 2026", "2026-07", "2026-08")}
{row("against August 2025", "2025-08", "2026-08")}
</div></body></html>"""


def test_blog_graphics(night, sample) -> None:  # noqa: F811
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
    rows = _months(sample)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        ctx = b.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
        ctx.add_cookies(cookies)  # type: ignore[arg-type]
        pg = ctx.new_page()

        def clip(first: str, last: str, pad: int = 16) -> dict[str, float]:
            a = pg.locator(first).first.bounding_box()
            z = pg.locator(last).last.bounding_box()
            assert a and z, (first, last)
            x0, y0 = min(a["x"], z["x"]) - pad, a["y"] - pad
            x1 = max(a["x"] + a["width"], z["x"] + z["width"]) + pad
            return {"x": x0, "y": y0, "width": x1 - x0, "height": z["y"] + z["height"] - y0 + pad}

        # Sweep's Suppressed tab, both known patterns open
        pg.goto(app + "/sweep?tab=suppressed", wait_until="networkidle")
        pg.evaluate("document.querySelectorAll('main details').forEach(d => d.open = true)")
        pg.wait_for_timeout(200)
        box = pg.locator("main").first.bounding_box()
        assert box
        top = pg.get_by_text("Suppressed by your Business Model").first.bounding_box()
        end = pg.get_by_text("Receivables over 60 days").last.bounding_box()
        assert top and end
        _save(
            pg.screenshot(
                clip={
                    "x": box["x"] + 16,
                    "y": top["y"] - 56,
                    "width": 1120,
                    "height": end["y"] + end["height"] + 40 - (top["y"] - 56),
                }
            ),
            BLOG / SEASON / "suppressed.webp",
        )
        # Sweep's numbers for the night
        pg.goto(app + "/sweep", wait_until="networkidle")
        h1 = pg.locator("main h1").first.bounding_box()
        cards = pg.get_by_text("Findings today").first.bounding_box()
        assert h1 and cards
        _save(
            pg.screenshot(
                clip={
                    "x": h1["x"] - 16,
                    "y": h1["y"] - 16,
                    "width": 1120,
                    "height": cards["y"] + 150 - (h1["y"] - 16),
                }
            ),
            BLOG / NIGHT / "sweep-numbers.webp",
        )
        # the Leadership brief's top: its time, headline, headline numbers and goals
        pg.goto(app + f"/brief/{brief}", wait_until="networkidle")
        start = pg.get_by_text("Sun 1 Nov · 6:00 AM IST").first.bounding_box()
        goal = pg.get_by_text("Net revenue FY27").first.bounding_box()
        assert start and goal
        _save(
            pg.screenshot(
                clip={
                    "x": start["x"] - 24,
                    "y": start["y"] - 48,
                    "width": 1136,
                    "height": goal["y"] + 40 - (start["y"] - 48),
                }
            ),
            BLOG / NIGHT / "brief-top.webp",
        )
        # the two drawn figures
        for html, path in (
            (_months_html(app, rows), BLOG / SEASON / "months.webp"),
            (_compare_html(app, rows), BLOG / SEASON / "compare.webp"),
        ):
            pg.set_content(html, wait_until="networkidle")
            pg.evaluate("document.fonts.ready")
            _save(pg.locator(".f").screenshot(), path)
        b.close()
    server.should_exit = True
    th.join(5)
    print("graphics in", BLOG)
