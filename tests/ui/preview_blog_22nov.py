# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (the drawn figures are inline CSS, one rule per line)
"""The graphics inside the 22 Nov 2026 blog posts (owner: each post its own header and 2-3 graphics
inside). Dev only, run by hand; needs no database, every figure is drawn from the posts' own numbers:

    .venv/bin/python tests/ui/preview_blog_22nov.py

- the-german-tank-problem: August 1942's three figures (Ruggles and Brodie, 1947), the worked
  serial-number example (19, 40, 42, 60 → 74), and the sample company's counted finding (1 Nov night);
- why-othor-ai-says-what-it-cant-tell-you: the four state tags (docs/04-engine.md §6), the
  Ravira Exports finding's reasons (Missing records, 1 Nov night), and the Leadership brief's question.
Written as webp into othor_site/static/site/blog/<slug>/."""

from __future__ import annotations

import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "othor" / "web" / "static"
BLOG = ROOT / "othor_site" / "static" / "site" / "blog"
TANK = "the-german-tank-problem"
GAP = "why-othor-ai-says-what-it-cant-tell-you"
INK, INK2, MUTED, RULE, CYAN, NEG, OK = (
    "#1F2A37",
    "#3A4654",
    "#5B6675",
    "#E6EAF0",
    "#2CCAFF",
    "#D9534F",
    "#10B5A0",
)
HEAD = """<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="http://art.local/static/css/fonts.css"><style>
body{{margin:0;font-family:Inter,system-ui,sans-serif;color:{INK};background:#fff}}
.f{{width:1200px;padding:40px 48px 44px;box-sizing:border-box}}
.k{{font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:{MUTED};margin:0 0 24px}}
{extra}</style></head><body><div class="f">"""


def _page(extra: str, body: str) -> str:
    return HEAD.format(INK=INK, MUTED=MUTED, extra=extra) + body + "</div></body></html>"


def estimates() -> str:
    """August 1942: intelligence 1,550; from serial numbers 327; German records 342."""
    unit = 0.48  # px per tank
    rows = [
        ("Intelligence estimate", 1550, "#9AA5B1"),
        ("From serial numbers", 327, CYAN),
        ("German records", 342, INK),
    ]
    body = "".join(
        f'<div class="r"><span>{n}</span><b style="width:{v * unit:.0f}px;background:{c}"></b><em>{v:,}</em></div>'
        for n, v, c in rows
    )
    css = f""".r{{display:flex;align-items:center;gap:18px;margin-bottom:20px}} .r span{{width:230px;flex:none;font-size:16px;font-weight:500}}
.r b{{height:34px;border-radius:6px}} .r em{{font-style:normal;font-size:16px;font-weight:600}}
.n{{font-size:14px;color:{MUTED};margin:10px 0 0 248px}}"""
    return _page(
        css,
        f'<p class="k">German tanks built in August 1942, per month</p>{body}'
        '<p class="n">Source: Ruggles and Brodie, Journal of the American Statistical Association, 1947</p>',
    )


def serials() -> str:
    """Four serials seen (19, 40, 42, 60) on a line to 80; the estimate 60 + 60/4 − 1 = 74."""
    w, total, seen, est = 1000, 80, (19, 40, 42, 60), 74

    def x(n: float) -> float:
        return (n - 1) / (total - 1) * w

    ticks = "".join(
        f'<i style="left:{x(n):.0f}px;height:{14 if n % 10 == 0 else 8}px;top:{-7 if n % 10 == 0 else -4}px"></i>'
        for n in range(1, total + 1)
    )
    labels = "".join(f'<u style="left:{x(n):.0f}px">{n}</u>' for n in (1, 20, 40, 60, 80))
    dots = "".join(
        f'<b style="left:{x(n) - 9:.0f}px"></b><s style="left:{x(n):.0f}px">{n}</s>' for n in seen
    )
    band = f'<em style="left:{x(60):.0f}px;width:{x(est) - x(60):.0f}px"></em>'
    mark = f'<strong style="left:{x(est) - 2:.0f}px"></strong><q style="left:{x(est):.0f}px">74</q>'
    css = f""".line{{position:relative;margin:70px 0 70px 50px;width:{w}px;height:2px;background:{RULE}}}
.line i{{position:absolute;width:2px;background:#C7CFD8}} .line u{{position:absolute;top:22px;transform:translateX(-50%);text-decoration:none;font-size:12px;color:{MUTED}}}
.line b{{position:absolute;top:-9px;width:18px;height:18px;border-radius:9px;background:{CYAN}}}
.line s{{position:absolute;top:-40px;transform:translateX(-50%);text-decoration:none;font-size:15px;font-weight:600}}
.line em{{position:absolute;top:-22px;height:44px;border-radius:8px;background:rgba(44,202,255,.12);border:2px dashed {CYAN};box-sizing:border-box}}
.line strong{{position:absolute;top:-30px;width:4px;height:60px;border-radius:2px;background:{OK}}}
.line q{{position:absolute;top:36px;transform:translateX(-50%);font-size:15px;font-weight:700;color:{OK}}} .line q::before,.line q::after{{content:none}}
.n{{font-size:15px;color:{INK2};margin:0}} .n b{{font-weight:600}}"""
    return _page(
        css,
        f'<p class="k">Four tanks seen: the highest, plus the average gap, less one</p>'
        f'<div class="line">{ticks}{labels}{band}{dots}{mark}</div>'
        '<p class="n">Estimate = 60 + 60 ÷ 4 − 1 = <b>74 tanks</b></p>',
    )


def counted() -> str:
    """The sample company's finding, 1 Nov night: 14 customers, ₹3.6 Cr, Measured."""
    css = f""".card{{border:1px solid {RULE};border-radius:12px;padding:26px 28px;display:flex;justify-content:space-between;gap:24px}}
.tag{{display:inline-flex;height:24px;align-items:center;padding:0 9px;border-radius:4px;background:{INK};color:#fff;font-size:13px;font-weight:600}}
.t{{font-size:22px;font-weight:600;margin:14px 0 6px}} .l{{font-size:16px;color:{INK2};margin:0}} .m{{font-size:22px;font-weight:600;white-space:nowrap}}
.src{{margin:18px 0 0;padding-top:14px;border-top:1px solid {RULE};font-size:14px;color:{MUTED}}}"""
    return _page(
        css,
        '<p class="k">Indigo Thread Co., the morning of 1 November</p><div class="card"><div>'
        '<span class="tag">Measured</span><p class="t">14 customers still marked active haven’t invoiced in 90+ days</p>'
        '<p class="l">Together ₹3.6 Cr over the last 12 months.</p>'
        '<p class="src">Counted from the invoices: each active customer’s last invoice date</p></div>'
        '<span class="m">₹3.6 Cr</span></div>',
    )


def tags() -> str:
    """The four states as the app draws them (docs/04-engine.md §6), one plain line each."""
    rows = [
        (
            '<span class="tg m">Measured</span>',
            "A fact counted from your records: a value, a share, a gap, a date.",
        ),
        (
            '<span class="tg a">Assessed · High</span>',
            "A pattern that passed a statistical test, with how sure it is.",
        ),
        (
            '<span class="tg n">Not determinable</span>',
            "The fact is measured, but the reason isn’t in the data Othor AI reads.",
        ),
        (
            '<span class="tg c">Needs confirmation</span>',
            "Two of your systems disagree. It becomes a question on Home.",
        ),
    ]
    body = "".join(
        f'<div class="r"><div class="c1">{t}</div><p>{line}</p></div>' for t, line in rows
    )
    css = f""".r{{display:flex;align-items:center;gap:24px;padding:16px 0;border-top:1px solid {RULE}}} .r:last-child{{border-bottom:1px solid {RULE}}}
.c1{{width:220px;flex:none}} .r p{{margin:0;font-size:17px;color:{INK2}}}
.tg{{display:inline-flex;height:28px;align-items:center;padding:0 11px;border-radius:4px;font-size:14px;font-weight:600;box-sizing:border-box}}
.tg.m{{background:{INK};color:#fff}} .tg.a{{border:1.5px solid {INK};color:{INK}}}
.tg.n{{border:1.5px dashed #9AA5B1;color:{MUTED};font-style:italic;font-weight:400}} .tg.c{{border:1.5px solid #E0A526;background:#FFF6E0;color:#8A5A00}}"""
    return _page(css, f'<p class="k">How sure each finding is, in four tags</p>{body}')


def reasons() -> str:
    """Ravira Exports, Missing records, 1 Nov night (checked on production, 22 Nov): two reasons
    still possible, "billing moved to another entity" ruled out (no customer under another name)."""
    css = f""".g{{display:grid;grid-template-columns:1fr 1.5fr 1fr;gap:20px}} .b{{border:1px solid {RULE};border-radius:12px;padding:22px 24px}}
.b h3{{margin:0 0 14px;font-size:13px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:{MUTED}}}
.b p{{margin:0 0 8px;font-size:17px;color:{INK2}}} .b strong{{font-size:22px;color:{INK}}}
.b ul{{margin:0;padding:0;list-style:none}} .b li{{font-size:17px;color:{INK2};padding:10px 0;border-top:1px dashed {RULE}}} .b li:first-child{{border-top:0;padding-top:0}}
.b li i{{display:block;font-style:normal;font-size:13px;font-weight:600;margin-top:4px;color:{OK}}}
.b li.out{{color:{MUTED}}} .b li.out span{{text-decoration:line-through}} .b li.out i{{color:{MUTED};font-weight:400}}
.b.s{{background:#F2FBFF;border-color:#BFE9F9}}"""
    return _page(
        css,
        '<p class="k">Ravira Exports, the morning of 1 November</p><div class="g">'
        '<div class="b"><h3>What is measured</h3><p><strong>Last invoice 9 Oct</strong></p><p>₹0.36 Cr at stake</p></div>'
        '<div class="b"><h3>Possible reasons</h3><ul>'
        "<li><span>Moved to a competitor</span><i>Still possible</i></li>"
        "<li><span>On hold</span><i>Still possible</i></li>"
        '<li class="out"><span>Billing moved to another company</span><i>Ruled out: no other customer found under another name</i></li>'
        "</ul></div>"
        '<div class="b s"><h3>What would settle it</h3><p><strong>Deal activity, or a call</strong></p></div></div>',
    )


def ask() -> str:
    """The Leadership brief's question on 1 Nov: one question, one named person."""
    css = f""".a{{display:flex;flex-direction:column;gap:10px;background:#F2FBFF;border:1px solid #BFE9F9;border-radius:12px;padding:24px 28px}}
.w{{font-size:14px;color:{MUTED}}} .w b{{color:{INK};font-weight:600}} .q{{font-size:24px;font-weight:600;line-height:1.35;margin:0}}"""
    return _page(
        css,
        '<p class="k">The Leadership brief’s question, 1 November</p><div class="a">'
        '<span class="w">Ask today · <b>Priya Menon</b>, Head of Sales</span>'
        '<p class="q">Have Tessora EU and Ravira Exports moved orders elsewhere, or are they on hold?</p></div>',
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

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1200, "height": 800}, device_scale_factor=2)
        pg.route("http://art.local/static/**", static)
        for slug, name, html in (
            (TANK, "estimates", estimates()),
            (TANK, "serials", serials()),
            (TANK, "counted", counted()),
            (GAP, "tags", tags()),
            (GAP, "reasons", reasons()),
            (GAP, "ask", ask()),
        ):
            pg.route("http://art.local/", _serve(html))
            pg.goto("http://art.local/")
            pg.evaluate("document.fonts.ready")
            pg.wait_for_timeout(200)
            out = BLOG / slug / f"{name}.webp"
            out.parent.mkdir(parents=True, exist_ok=True)
            png = pg.locator(".f").screenshot()
            Image.open(io.BytesIO(png)).convert("RGB").save(out, "WEBP", quality=90, method=6)
            pg.unroute("http://art.local/")
            print(out.relative_to(ROOT))
        b.close()


if __name__ == "__main__":
    main()
