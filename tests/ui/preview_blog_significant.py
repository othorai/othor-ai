# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (the drawn figures are inline CSS, one rule per line)
"""The graphics inside the long read "Significant is not material" (owner, 24 Nov 2026: each post
its own header and 2-3 graphics inside). Dev only, run by hand (needs `make dev`; the sample
sources are seeded if empty):

    .venv/bin/python -m pytest tests/ui/preview_blog_significant.py -s -p no:cacheprovider -o addopts=""

Figure 2 counts the sample company's 1 Nov night from the run's own detection.json (the core
eleven checks only), by the first reason that stopped each result; figure 1 (the four boxes) and
figure 3 (three ways to a floor) are illustrations with the post's own arithmetic. A screenshot of
the floor's line on the Business Model page. Written as webp into
othor_site/static/site/blog/<slug>/; the counts into tests/ui/blog_significant.json."""

from __future__ import annotations

import io
import json
import socket
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, cast

from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
SLUG = "significant-is-not-material"
OUT = ROOT / "othor_site" / "static" / "site" / "blog" / SLUG
FONT = '<link rel="stylesheet" href="{app}/static/css/fonts.css">'
INK, MUTED, RULE, CYAN, SOFT = "#1F2A37", "#5B6675", "#E6EAF0", "#2CCAFF", "#C9D3DD"
CORE = {
    "bimodal_average",
    "broken_relationship",
    "changepoint",
    "concentration",
    "cross_source",
    "forecast_breach",
    "lead_lag",
    "missing_records",
    "net_hiding_gross",
    "process_artefact",
    "rising_variance",
}


def _port() -> int:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return int(so.getsockname()[1])


def _save(png: bytes, path: Path) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.open(io.BytesIO(png)).convert("RGB").save(path, "WEBP", quality=90, method=6)


def stage(r: dict[str, Any]) -> str:
    """The gate that stopped a result (detect._gate's order), or "finding"."""
    reason = r.get("reason") or ""
    if r.get("passed"):
        return "finding"
    if not r.get("eligible"):
        return "not eligible"
    if r.get("p_value") is None or reason == "no signal" or float(r["p_value"]) >= 0.01:
        return "no signal"
    if reason == "below materiality":
        return "below materiality"
    if reason == "goal at risk":
        return "goal"
    if reason.startswith("not significant after FDR"):
        return "not significant after correction"
    if reason.startswith("suppressed"):
        return "known pattern"
    return "held or repeated"


def funnel(results: list[dict[str, Any]]) -> dict[str, Any]:
    core = [r for r in results if r["detector"] in CORE]
    c = Counter(stage(r) for r in core)
    money = Counter(
        r["detector"] for r in core if stage(r) == "below materiality" and r.get("money") is None
    )
    return {
        "results": len(core),
        "by_stage": dict(sorted(c.items())),
        "below_materiality_with_no_money": sum(money.values()),
        "below_materiality_no_money_by_test": dict(sorted(money.items())),
        "no_money_metrics": sorted(
            {
                f"{r['detector']}:{r['metric']}"
                for r in core
                if stage(r) == "below materiality" and r.get("money") is None
            }
        ),
        "below_floor_with_money": sorted(
            f"{r['detector']}:{r['metric']}:{r['money']}"
            for r in core
            if stage(r) == "below materiality" and r.get("money") is not None
        ),
        "findings": sorted(
            f"{r['detector']}:{r['metric']}:{r.get('money')}" for r in core if r.get("passed")
        ),
        "tests_in_correction": sum(
            1
            for r in core
            if r.get("eligible")
            and r.get("p_value") is not None
            and (r.get("slots") or {}).get("kind") != "goal"
        ),
    }


def _boxes_html(app: str) -> str:
    cells = [
        (
            "Real, but small",
            "Below the floor",
            "A small line's weekly sales stepped down ₹42,000 six weeks ago. Certain, and about ₹2.5 L so far.",
            False,
        ),
        (
            "Real, and it matters",
            "A finding",
            "A customer worth about ₹21,000 a day has been silent for 34 days, far outside its own rhythm: ₹7.14 L at stake.",
            True,
        ),
        ("Neither", "Nothing to say", "A slow week at one branch, inside its normal range.", False),
        (
            "Large, but maybe chance",
            "No signal",
            "A big account's orders swung by ₹38 L, but swings like it are ordinary for that account.",
            False,
        ),
    ]

    def cell(t: str, tag: str, body: str, lit: bool) -> str:
        style = (
            f"border:2px solid {CYAN};background:#F2FBFF"
            if lit
            else f"border:1px solid {RULE};background:#fff"
        )
        return f'<div class="c" style="{style}"><p class="g" style="color:{"#0A7FB0" if lit else MUTED}">{tag}</p><h3>{t}</h3><p>{body}</p></div>'

    return f"""<!doctype html><html><head><meta charset="utf-8">{FONT.format(app=app)}<style>
body{{margin:0;font-family:Inter,system-ui,sans-serif;color:{INK};background:#fff}}
.f{{width:1200px;padding:40px 48px 44px;box-sizing:border-box}}
.k{{font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};margin:0 0 20px}}
.w{{display:grid;grid-template-columns:150px 1fr 1fr;grid-template-rows:auto 1fr 1fr;gap:14px}}
.h{{font-size:14px;font-weight:600;color:{MUTED};align-self:end}} .v{{font-size:14px;font-weight:600;color:{MUTED};align-self:center}}
.c{{border-radius:12px;padding:20px 22px;box-sizing:border-box;min-height:150px}}
.c h3{{margin:4px 0 8px;font-size:20px;font-weight:600;letter-spacing:-.01em}} .c p{{margin:0;font-size:15px;line-height:1.45;color:{INK}}}
.c p.g{{font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase}}
</style></head><body><div class="f"><p class="k">Two questions, asked separately · an illustration, figures invented</p>
<div class="w"><span></span><span class="h">Money at stake under the floor (₹5 L)</span><span class="h">Money at stake at or over the floor</span>
<span class="v">Unlikely to be chance</span>{cell(*cells[0])}{cell(*cells[1])}
<span class="v">Could be chance</span>{cell(*cells[2])}{cell(*cells[3])}</div></div></body></html>"""


def _funnel_html(app: str, f: dict[str, Any]) -> str:
    s = f["by_stage"]
    total = f["results"]
    steps = [
        ("Results from the eleven checks", total, None),
        ("Had enough history to test", total - s.get("not eligible", 0), "not eligible"),
        ("A signal (p under 0.01)", None, "no signal"),
        ("Worth at least ₹5 L", None, "below materiality"),
        ("Still there after the night-wide correction", None, "not significant after correction"),
        ("Not a pattern the owner named", None, "known pattern"),
    ]
    left = total
    rows = []
    for name, n, drop in steps:
        if drop and n is None:
            left -= s.get(drop, 0)
            if drop == "below materiality":
                left -= s.get("goal", 0)
            n = left
        elif drop:
            left = n
        rows.append((name, n))
    found = s.get("finding", 0)
    held = s.get("held or repeated", 0)
    scale = 640 / max(1, total)
    body = ""
    for name, n in rows:
        body += f'<div class="r"><span>{name}</span><div class="t"><b style="width:{max(4, n * scale):.0f}px"></b><em>{n}</em></div></div>'
    body += f'<div class="r end"><span>Findings that night</span><div class="t"><b style="width:{max(4, found * scale):.0f}px;background:#10B5A0"></b><em>{found}</em></div></div>'
    note = (
        f"{s.get('below_materiality', s.get('below materiality', 0))} results stopped at the money gate, "
        f"{f['below_materiality_with_no_money']} of them with no money figure at all."
    )
    if held:
        note += f" {held} more were the same problem already in another finding."
    return f"""<!doctype html><html><head><meta charset="utf-8">{FONT.format(app=app)}<style>
body{{margin:0;font-family:Inter,system-ui,sans-serif;color:{INK};background:#fff}}
.f{{width:1200px;padding:40px 48px 40px;box-sizing:border-box}}
.k{{font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};margin:0 0 22px}}
.r{{display:flex;align-items:center;gap:18px;height:44px}} .r span{{width:330px;flex:none;font-size:15px;font-weight:500}}
.t{{display:flex;align-items:center;gap:12px}} .t b{{display:block;height:22px;border-radius:5px;background:{CYAN}}}
.t em{{font-style:normal;font-size:15px;font-weight:600}}
.end{{border-top:1px solid {RULE};margin-top:8px;padding-top:8px;height:48px}}
.n{{font-size:14px;color:{MUTED};margin:14px 0 0}}
</style></head><body><div class="f"><p class="k">Indigo Thread Co., the night of 1 November 2026 · results left after each gate</p>
{body}<p class="n">{note}</p></div></body></html>"""


def _floors_html(app: str) -> str:
    rows = [
        ("1% of a year's revenue", "₹60 Cr × 1% · the not-for-profit example", 60.0),
        ("5% of profit before tax", "₹4.8 Cr × 5% · the manufacturer example", 24.0),
        ("1% of a month's revenue", "₹5 Cr × 1%", 5.0),
    ]
    unit = 11  # px per ₹1 L, from zero
    body = "".join(
        f'<div class="r"><span>{a}<small>{b}</small></span><div class="t"><b style="width:{v * unit:.0f}px;background:{CYAN if v == 5.0 else SOFT}"></b><em>₹{v:.0f} L</em></div></div>'
        for a, b, v in rows
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">{FONT.format(app=app)}<style>
body{{margin:0;font-family:Inter,system-ui,sans-serif;color:{INK};background:#fff}}
.f{{width:1200px;padding:40px 48px 40px;box-sizing:border-box}}
.k{{font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};margin:0 0 22px}}
.r{{display:flex;align-items:center;gap:18px;height:64px}} .r span{{width:330px;flex:none;font-size:16px;font-weight:500}}
.r small{{display:block;font-size:13px;font-weight:400;color:{MUTED};margin-top:2px}}
.t{{display:flex;align-items:center;gap:12px;border-left:2px solid {SOFT};height:40px}} .t b{{display:block;height:26px;border-radius:0 6px 6px 0}}
.t em{{font-style:normal;font-size:16px;font-weight:600}}
.n{{font-size:14px;color:{MUTED};margin:16px 0 0}}
</style></head><body><div class="f"><p class="k">Three ways to a floor · an illustrative company: ₹60 Cr a year, 8% profit before tax</p>
{body}<p class="n">The first two apply the examples in the auditing standard ISA 320 to a year's accounts. The third is the scale of a month, where a morning brief works, and is what Othor AI suggests.</p></div></body></html>"""


def test_blog_graphics(night, sample) -> None:  # noqa: F811
    subprocess.run([sys.executable, "scripts/build_css.py"], cwd=ROOT, check=True)
    env, _h, _cid, rid, svc = night
    root = Path(svc.settings.data_dir)
    files = sorted(p for p in root.glob("**/detection.json") if str(rid) in str(p)) or sorted(
        root.glob("**/detection.json")
    )
    assert files, "detection.json for the night"
    counts = funnel(json.loads(files[-1].read_text()))
    (ROOT / "tests" / "ui" / "blog_significant.json").write_text(
        json.dumps(counts, indent=2) + "\n"
    )
    print(json.dumps(counts, indent=2))
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
        # the floor's line on the Business Model page, with its suggestion
        pg.goto(app + "/ontology", wait_until="networkidle")
        row = pg.locator("#floor-row").first
        row.get_by_role("button", name="Change").first.click()
        pg.wait_for_timeout(300)
        pg.evaluate("document.activeElement && document.activeElement.blur()")
        box = row.bounding_box()
        assert box
        pad = 14
        _save(
            pg.screenshot(
                clip={
                    "x": box["x"] - pad,
                    "y": box["y"] - pad,
                    "width": box["width"] + 2 * pad,
                    "height": box["height"] + 2 * pad,
                }
            ),
            OUT / "floor-line.webp",
        )
        for html, name in (
            (_boxes_html(app), "four-boxes.webp"),
            (_funnel_html(app, counts), "night-gates.webp"),
            (_floors_html(app), "three-floors.webp"),
        ):
            pg.set_content(html, wait_until="networkidle")
            pg.evaluate("document.fonts.ready")
            _save(pg.locator(".f").screenshot(), OUT / name)
        b.close()
    server.should_exit = True
    th.join(5)
    print("graphics in", OUT)
