# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (the drawn figures are inline CSS and SVG, one rule per line)
"""The graphics inside the 22 Nov 2026 long read "The most dangerous equation" (owner: publish the
GTM draft in the blog's own style). Dev only, run by hand; needs no database. Every branch figure
comes from the seeded simulation in tests/ui/blog_sim_dangerous.py (illustration, not data), run
first; the Othor AI card from the code it names:

    .venv/bin/python tests/ui/blog_sim_dangerous.py
    .venv/bin/python tests/ui/preview_blog_long_read_22nov.py

Written as webp into othor_site/static/site/blog/the-most-dangerous-equation/."""

from __future__ import annotations

import io
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "othor" / "web" / "static"
SLUG = "the-most-dangerous-equation"
OUT = ROOT / "othor_site" / "static" / "site" / "blog" / SLUG
SIM = json.loads((Path(__file__).resolve().parent / "blog_sim_dangerous.json").read_text())
ROWS, S = SIM["rows"], SIM["summary"]
INK, INK2, MUTED, MUTED2, RULE, CYAN, NEG = (
    "#1F2A37",
    "#3A4654",
    "#5B6675",
    "#9AA5B1",
    "#E6EAF0",
    "#2CCAFF",
    "#D9534F",
)
MID = "#8FA3B8"
NOTE = "Illustration, not data · 60 simulated branches · seed 20261122"
HEAD = """<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="http://art.local/static/css/fonts.css"><style>
body{{margin:0;font-family:Inter,system-ui,sans-serif;color:{INK};background:#fff}}
.f{{width:1200px;padding:40px 48px 36px;box-sizing:border-box}}
.k{{font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};margin:0 0 20px}}
.n{{font-size:13px;color:{MUTED};margin:14px 0 0}}
svg text{{font-family:Inter,system-ui,sans-serif}}
{extra}</style></head><body><div class="f">"""


def _page(kicker: str, body: str, extra: str = "", note: str = NOTE) -> str:
    foot = f'<p class="n">{note}</p>' if note else ""
    return (
        HEAD.format(INK=INK, MUTED=MUTED, extra=extra)
        + f'<p class="k">{kicker}</p>{body}{foot}</div></body></html>'
    )


def _third(r: dict) -> str:
    by_n = sorted(ROWS, key=lambda x: (x["n"], x["id"]))
    i = next(k for k, x in enumerate(by_n) if x["id"] == r["id"])
    return "small" if i < 20 else "middle" if i < 40 else "large"


COLOUR = {"small": CYAN, "middle": MID, "large": INK}


def _pct(v: float, d: int = 0) -> str:
    return f"{v * 100:.{d}f}%"


# --- 1. the equation: the same 20% chance, a small and a large branch ---------------------------
def equation() -> str:
    """σ = √(0.2 × 0.8) = 0.4; σ/√15 ≈ 0.10 and σ/√1,500 ≈ 0.01: the usual range of a month."""
    x0, w = 300, 780

    def xv(p: float) -> float:
        return x0 + (p - 0.0) / 0.55 * w

    rows = []
    for i, (label, n) in enumerate((("15 invoices a month", 15), ("1,500 invoices a month", 1500))):
        se = 0.4 / math.sqrt(n)
        y = 46 + i * 104
        lo, hi = max(0.0, 0.2 - 2 * se), 0.2 + 2 * se
        rows.append(
            f'<text x="0" y="{y + 26}" font-size="17" font-weight="600" fill="{INK}">{label}</text>'
            f'<text x="0" y="{y + 50}" font-size="14" fill="{MUTED}">σ/√n ≈ {se:.2f}</text>'
            f'<rect x="{xv(lo):.0f}" y="{y + 8}" width="{xv(hi) - xv(lo):.0f}" height="36" rx="8" fill="{CYAN}" fill-opacity="{0.22 if n == 15 else 0.6}"/>'
            f'<line x1="{xv(0.2):.0f}" x2="{xv(0.2):.0f}" y1="{y}" y2="{y + 52}" stroke="{INK}" stroke-width="3"/>'
            f'<text x="{xv(hi) + 12:.0f}" y="{y + 32}" font-size="15" fill="{INK2}">{_pct(lo)} to {_pct(hi)} most months</text>'
        )
    ticks = "".join(
        f'<line x1="{xv(t):.0f}" x2="{xv(t):.0f}" y1="30" y2="250" stroke="{RULE}"/>'
        f'<text x="{xv(t):.0f}" y="276" font-size="13" fill="{MUTED}" text-anchor="middle">{_pct(t)}</text>'
        for t in (0, 0.1, 0.2, 0.3, 0.4)
    )
    svg = f'<svg width="1104" height="290" viewBox="0 0 1104 290">{ticks}{"".join(rows)}</svg>'
    return _page(
        "One chance in five of paying late, in every branch",
        svg,
        note="The same 20% chance; how far a month's late rate usually lands from it (± two σ/√n). A hundred times the invoices, a tenth of the noise.",
    )


# --- 2. the league table ------------------------------------------------------------------------
def league() -> str:
    order = sorted(ROWS, key=lambda r: (-r["r1"], r["id"]))
    bw, gap, h = 14, 3, 300
    top = 0.42
    bars = []
    for i, r in enumerate(order):
        x = 40 + i * (bw + gap)
        bh = r["r1"] / top * h
        fill = NEG if r["planted"] else COLOUR[_third(r)]
        bars.append(
            f'<rect x="{x}" y="{20 + h - bh:.1f}" width="{bw}" height="{bh:.1f}" rx="2" fill="{fill}"/>'
        )

    def lab(name: str, text: str, dy: int = -10) -> str:
        i = next(k for k, r in enumerate(order) if r["name"] == name)
        r = order[i]
        x = 40 + i * (bw + gap) + bw / 2
        y = 20 + h - r["r1"] / top * h + dy
        anchor = "start" if i < 40 else "end"
        return f'<text x="{x + (6 if anchor == "start" else -6):.0f}" y="{y:.0f}" font-size="14" font-weight="600" fill="{INK}" text-anchor="{anchor}">{text}</text>'

    b01 = next(r for r in ROWS if r["name"] == "B01")
    b02 = next(r for r in ROWS if r["name"] == "B02")
    p = S["planted"]
    labels = (
        lab("B01", f"B01 · {b01['n']} invoices · {_pct(b01['r1'])}")
        + lab("B55", f"B55 · {p['n']:,} invoices · {_pct(p['r1'])} · the real problem", dy=-34)
        + lab("B02", f"B02 · {b02['n']} invoices · {_pct(b02['r1'])}", dy=-128)
    )
    axis = "".join(
        f'<line x1="34" x2="1120" y1="{20 + h - t / top * h:.0f}" y2="{20 + h - t / top * h:.0f}" stroke="{RULE}"/>'
        f'<text x="26" y="{24 + h - t / top * h:.0f}" font-size="12" fill="{MUTED}" text-anchor="end">{_pct(t)}</text>'
        for t in (0, 0.1, 0.2, 0.3, 0.4)
    )
    svg = f'<svg width="1104" height="{h + 40}" viewBox="-20 0 1124 {h + 40}">{axis}{"".join(bars)}{labels}</svg>'
    lr = S["long_run"]["extreme_share"]
    key = "".join(
        f'<div class="kk"><i style="background:{c}"></i><b>{_pct(lr[t])}</b><span>{name}</span></div>'
        for t, c, name in (
            ("small", CYAN, "smallest third"),
            ("middle", MID, "middle third"),
            ("large", INK, "largest third"),
        )
    )
    extra = f""".row{{display:flex;gap:28px;align-items:flex-start;margin-top:8px}} .kk{{display:flex;align-items:baseline;gap:8px;font-size:15px;color:{INK2}}}
.kk i{{width:12px;height:12px;border-radius:3px;display:inline-block}} .kk b{{font-size:22px;color:{INK}}} .lead{{font-size:15px;color:{MUTED};margin:22px 0 10px}}"""
    body = (
        svg
        + f'<p class="lead">Places in the 10 highest and 10 lowest, over 2,000 simulated months (a fair share is 33% each):</p><div class="row">{key}</div>'
    )
    return _page(
        "One month's league table · late-paid invoices · 60 branches, highest first", body, extra
    )


# --- 3. the funnel ------------------------------------------------------------------------------
def _fx(n: float) -> float:
    return 70 + (math.log10(n) - 1) / (math.log10(2000) - 1) * 980


def _fy(r: float) -> float:
    return 30 + (0.45 - r) / 0.45 * 340


def funnel() -> str:
    pool = S["pool"]
    curves = []
    for z, dash, label in ((1.96, "6 6", "95%"), (3.09, "", "99.8%")):
        for sign in (1, -1):
            pts = []
            n = 10.0
            while n <= 2000:
                r = pool + sign * z * math.sqrt(pool * (1 - pool) / n)
                if 0 <= r <= 0.45:
                    pts.append(f"{_fx(n):.1f},{_fy(r):.1f}")
                n *= 1.04
            curves.append(
                f'<polyline points="{" ".join(pts)}" fill="none" stroke="{INK2 if not dash else MUTED2}" stroke-width="{2.5 if not dash else 2}" stroke-dasharray="{dash}"/>'
            )
        r_at = pool + z * math.sqrt(pool * (1 - pool) / 60)
        curves.append(
            f'<text x="{_fx(60) + 6:.0f}" y="{_fy(r_at) - 8:.0f}" font-size="13" fill="{MUTED}">{label}</text>'
        )
    dots = []
    for r in ROWS:
        fill = NEG if r["planted"] else COLOUR[_third(r)]
        rad = 9 if r["planted"] else 6
        dots.append(
            f'<circle cx="{_fx(r["n"]):.1f}" cy="{_fy(r["r1"]):.1f}" r="{rad}" fill="{fill}" fill-opacity="{1 if r["planted"] else 0.85}"/>'
        )
    b01 = next(r for r in ROWS if r["name"] == "B01")
    p = next(r for r in ROWS if r["planted"])
    notes = (
        f'<text x="{_fx(b01["n"]) + 14:.0f}" y="{_fy(b01["r1"]) + 5:.0f}" font-size="14" font-weight="600" fill="{INK}">B01 · {_pct(b01["r1"])}</text>'
        f'<text x="{_fx(p["n"]) - 14:.0f}" y="{_fy(p["r1"]) - 16:.0f}" font-size="14" font-weight="600" fill="{NEG}" text-anchor="end">B55 · {_pct(p["r1"])} · outside the 99.8% line</text>'
    )
    axis = (
        "".join(
            f'<line x1="{_fx(n):.0f}" x2="{_fx(n):.0f}" y1="30" y2="370" stroke="{RULE}"/><text x="{_fx(n):.0f}" y="394" font-size="13" fill="{MUTED}" text-anchor="middle">{n:,}</text>'
            for n in (10, 30, 100, 300, 1000)
        )
        + "".join(
            f'<text x="58" y="{_fy(t) + 4:.0f}" font-size="12" fill="{MUTED}" text-anchor="end">{_pct(t)}</text>'
            for t in (0, 0.1, 0.2, 0.3, 0.4)
        )
        + (
            f'<line x1="70" x2="1050" y1="{_fy(pool):.1f}" y2="{_fy(pool):.1f}" stroke="{INK}" stroke-width="1.5" stroke-dasharray="2 5"/>'
            f'<text x="560" y="420" font-size="13" fill="{MUTED}" text-anchor="middle">Invoices a month (log scale)</text>'
        )
    )
    svg = f'<svg width="1104" height="430" viewBox="0 0 1104 430">{axis}{"".join(curves)}{"".join(dots)}{notes}</svg>'
    out95 = len(S["out95"])
    return _page(
        f"The same month as a funnel plot · {out95} of 60 outside the 95% line, 1 outside 99.8%",
        svg,
        note=f"{NOTE} · dotted line: all branches, {_pct(pool, 1)} · limits: that rate ± z·√(p(1−p)/n), z = 1.96 and 3.09",
    )


# --- 4. shrinkage -------------------------------------------------------------------------------
def shrinkage() -> str:
    pool = S["pool"]
    out = []
    for r in sorted(ROWS, key=lambda x: x["n"]):
        x = _fx(r["n"])
        y0, y1 = _fy(r["r1"]), _fy(r["shrunk"])
        col = NEG if r["planted"] else CYAN
        out.append(
            f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y0:.1f}" y2="{y1:.1f}" stroke="{MUTED2}" stroke-width="1.5"/>'
            f'<circle cx="{x:.1f}" cy="{y0:.1f}" r="5" fill="#fff" stroke="{MUTED2}" stroke-width="2"/>'
            f'<circle cx="{x:.1f}" cy="{y1:.1f}" r="{8 if r["planted"] else 5.5}" fill="{col}"/>'
        )
    b01 = next(r for r in ROWS if r["name"] == "B01")
    p = S["planted"]
    notes = (
        f'<text x="{_fx(b01["n"]) + 14:.0f}" y="{_fy(b01["r1"]) + 5:.0f}" font-size="14" font-weight="600" fill="{INK}">B01 · {_pct(b01["r1"])} → {_pct(b01["shrunk"], 1)}</text>'
        f'<text x="{_fx(p["n"]) - 14:.0f}" y="{_fy(p["r1"]) - 14:.0f}" font-size="14" font-weight="600" fill="{NEG}" text-anchor="end">B55 · {_pct(p["r1"], 1)} → {_pct(p["shrunk"], 1)} · first</text>'
    )
    axis = (
        "".join(
            f'<line x1="{_fx(n):.0f}" x2="{_fx(n):.0f}" y1="30" y2="370" stroke="{RULE}"/><text x="{_fx(n):.0f}" y="394" font-size="13" fill="{MUTED}" text-anchor="middle">{n:,}</text>'
            for n in (10, 30, 100, 300, 1000)
        )
        + "".join(
            f'<text x="58" y="{_fy(t) + 4:.0f}" font-size="12" fill="{MUTED}" text-anchor="end">{_pct(t)}</text>'
            for t in (0, 0.1, 0.2, 0.3, 0.4)
        )
        + (
            f'<line x1="70" x2="1050" y1="{_fy(pool):.1f}" y2="{_fy(pool):.1f}" stroke="{INK}" stroke-width="1.5" stroke-dasharray="2 5"/>'
            f'<text x="560" y="420" font-size="13" fill="{MUTED}" text-anchor="middle">Invoices a month (log scale) · open circle: as measured · filled: after shrinkage</text>'
        )
    )
    svg = f'<svg width="1104" height="430" viewBox="0 0 1104 430">{axis}{"".join(out)}{notes}</svg>'
    return _page(
        "Shrinkage · each branch pulled toward the average by how little its figure can be trusted",
        svg,
    )


# --- 5. next month ------------------------------------------------------------------------------
def next_month() -> str:
    rows = (
        ("The 10 highest in month one", S["worst10_m1_avg"], S["worst10_m2_avg"], NEG),
        ("The 10 lowest in month one", S["best10_m1_avg"], S["best10_m2_avg"], CYAN),
    )

    def y(v: float) -> float:
        return 30 + (0.30 - v) / 0.18 * 260

    out = [
        f'<line x1="320" x2="320" y1="20" y2="300" stroke="{RULE}" stroke-width="2"/><line x1="780" x2="780" y1="20" y2="300" stroke="{RULE}" stroke-width="2"/>'
        f'<text x="320" y="326" font-size="14" fill="{MUTED}" text-anchor="middle">Month one</text><text x="780" y="326" font-size="14" fill="{MUTED}" text-anchor="middle">Month two · nobody did anything</text>'
        f'<line x1="300" x2="800" y1="{y(S["pool"]):.0f}" y2="{y(S["pool"]):.0f}" stroke="{INK}" stroke-dasharray="2 5"/>'
        f'<text x="810" y="{y(S["pool"]) + 5:.0f}" font-size="13" fill="{MUTED}">all branches {_pct(S["pool"], 1)}</text>'
    ]
    for label, a, b, c in rows:
        out.append(
            f'<line x1="320" x2="780" y1="{y(a):.0f}" y2="{y(b):.0f}" stroke="{c}" stroke-width="4"/>'
            f'<circle cx="320" cy="{y(a):.0f}" r="9" fill="{c}"/><circle cx="780" cy="{y(b):.0f}" r="9" fill="{c}"/>'
            f'<text x="300" y="{y(a) + 5:.0f}" font-size="15" font-weight="600" fill="{INK}" text-anchor="end">{label} · {_pct(a, 1)}</text>'
            f'<text x="800" y="{y(b) + (-10 if c == NEG else 20):.0f}" font-size="15" font-weight="600" fill="{INK}">{_pct(b, 1)}</text>'
        )
    svg = f'<svg width="1104" height="340" viewBox="0 0 1104 340">{"".join(out)}</svg>'
    return _page(
        f"Next month · only {S['worst10_still_worst10_m2']} of the 10 highest stay in the 10 highest",
        svg,
    )


# --- 6. what Othor AI does -----------------------------------------------------------------------
def othor() -> str:
    cards = [
        (
            "01",
            "Its own past",
            "A part's move is scored against its own month-on-month changes over 24 months. With fewer than 6 past changes, it is never called unusual.",
            False,
        ),
        (
            "02",
            "Money, not %",
            "A finding must put the owner's floor at stake, ₹5 lakh by default. A 40% swing on a 15-invoice branch rarely does.",
            False,
        ),
        (
            "03",
            "One correction",
            "Every test of the night in one false-discovery correction at q = 0.01. More small parts, a higher bar for each.",
            False,
        ),
        (
            "04",
            "Ranked by ₹",
            "Brief items: money at stake × urgency × how sure. No percentage enters the score.",
            False,
        ),
        (
            "05",
            "Plain updates",
            "On a quiet day, moves are scored against each number's usual variation; best and weakest part by ₹ change.",
            False,
        ),
        (
            "06",
            "Not built",
            "No funnel plot, no shrinkage. Groups of very different sizes: check how many sit behind a “best”.",
            True,
        ),
    ]
    body = "".join(
        f'<div class="c{" x" if x else ""}"><p class="h"><span>{n}</span>{t}</p><p class="t">{d}</p></div>'
        for n, t, d, x in cards
    )
    extra = f""".g{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}
.c{{border:1px solid {RULE};border-radius:12px;padding:20px 22px;min-height:150px;box-sizing:border-box}}
.c.x{{border:1.5px dashed {MUTED2};background:#FAFBFC}}
.h{{margin:0 0 10px;font-size:18px;font-weight:600;display:flex;gap:10px;align-items:baseline}} .h span{{font-family:'Geist Mono',monospace;font-size:13px;color:{CYAN};font-weight:600}}
.c.x .h span{{color:{MUTED}}} .t{{margin:0;font-size:15px;line-height:1.5;color:{INK2}}}"""
    return _page(
        "Small units in Othor AI · what the nightly run does, and doesn't",
        f'<div class="g">{body}</div>',
        extra,
        note="From the code: othor/engine/detectors/net_hiding_gross.py · othor/engine/detect.py · othor/briefs/rank.py · othor/briefs/ordinary.py · othor_ee/look_back/analyses/library.py",
    )


def _serve(html: str):  # type: ignore[no-untyped-def]
    """A route handler serving `html`; one argument, so Playwright doesn't pass the request in."""

    def handler(route) -> None:  # type: ignore[no-untyped-def]
        route.fulfill(body=html, content_type="text/html")

    return handler


def main() -> None:
    from PIL import Image
    from playwright.sync_api import Route, sync_playwright

    def static(route: Route) -> None:
        rel = route.request.url.split("http://art.local/static/", 1)[1].split("?")[0]
        path = STATIC / rel
        types = {".css": "text/css", ".woff2": "font/woff2"}
        route.fulfill(
            body=path.read_bytes(), content_type=types.get(path.suffix, "application/octet-stream")
        )

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1200, "height": 800}, device_scale_factor=2)
        pg.route("http://art.local/static/**", static)
        for name, html in (
            ("equation", equation()),
            ("league", league()),
            ("funnel", funnel()),
            ("shrinkage", shrinkage()),
            ("next-month", next_month()),
            ("othor", othor()),
        ):
            pg.route("http://art.local/", _serve(html))
            pg.goto("http://art.local/")
            pg.evaluate("document.fonts.ready")
            pg.wait_for_timeout(200)
            png = pg.locator(".f").screenshot()
            out = OUT / f"{name}.webp"
            Image.open(io.BytesIO(png)).convert("RGB").save(out, "WEBP", quality=90, method=6)
            pg.unroute("http://art.local/")
            print(out.relative_to(ROOT))
        b.close()


if __name__ == "__main__":
    main()
