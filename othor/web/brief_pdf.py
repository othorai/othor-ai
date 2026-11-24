# SPDX-License-Identifier: Apache-2.0
"""One brief as a PDF (M15.7, roadmap item 3): rendered from the stored issue and its run's stored
findings, the same data as the brief page and the email, so it can never show another number.
Nothing is recomputed and no model is called. WeasyPrint (HTML and CSS to PDF, no browser, no
network) with the bundled fonts only; the same issue exports byte for byte twice.

Paper follows the currency: A4 for INR, US Letter for USD. Every page's footer carries the run,
the spec version and "replayable"; the appendix, if asked for, each finding's test and metric."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from sqlalchemy.orm import Session

from othor.briefs.schedule import since_word
from othor.charts.svg import render_svg
from othor.core import fmt
from othor.core.config import Settings
from othor.core.models import Company
from othor.engine import finding_charts
from othor.mail import delivery, messages
from othor.web import brief_view as bvw
from othor.web import sweep_view as sv

HERE = Path(__file__).parent / "pdf"
FONTS = Path(__file__).parent / "static" / "fonts" / "ttf"


def _fetcher_base() -> type:
    from weasyprint.urls import URLFetcher  # type: ignore[import-untyped]

    return URLFetcher  # type: ignore[no-any-return]


class LocalOnlyFetcher(_fetcher_base()):  # type: ignore[misc]
    """WeasyPrint fetches nothing from outside (15 Nov 2026, the SSRF and EPS advisories): only
    `data:` URIs and files inside the bundled template and font folders, no redirects. The HTML is
    our own escaped template, so nothing should ask for more; this makes sure nothing can."""

    ROOTS = (HERE.resolve(), FONTS.resolve())

    def __init__(self) -> None:
        super().__init__(allowed_protocols=("data", "file"), allow_redirects=False)

    def fetch(self, url: str, headers: Any = None) -> Any:
        from urllib.parse import unquote, urlsplit

        parts = urlsplit(url)
        if parts.scheme == "file":
            path = Path(unquote(parts.path)).resolve()
            if not any(path.is_relative_to(root) for root in self.ROOTS):
                raise ValueError(f"the PDF fetches only its bundled files, not {path.name!r}")
        elif parts.scheme != "data":
            raise ValueError(f"the PDF fetches nothing from outside: {parts.scheme}")
        return super().fetch(url, headers)


STATES = {
    "measured": "Measured",
    "assessed": "Assessed",
    "not_determinable": "Not determinable",
    "needs_confirmation": "Needs confirmation",
}

env = Environment(
    loader=FileSystemLoader(str(HERE)),
    autoescape=select_autoescape(["html"]),
    undefined=StrictUndefined,
)
env.globals["fmt"] = fmt


def _svg(spec: Any, size: tuple[int, int]) -> str | None:
    return render_svg(spec, size) if spec is not None else None


def _state(state: str, confidence: str | None) -> str:
    out = STATES.get(state, state)
    return out + (f" · {confidence.capitalize()}" if state == "assessed" and confidence else "")


def file_name(company: Company, brief_name: str, date: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", brief_name.lower()).strip("-") or "brief"
    return f"{company.slug}-{slug}-{date.isoformat()}.pdf"


def context(
    s: Session,
    settings: Settings,
    company: Company,
    bv: bvw.BriefView,
    m: messages.BriefMail,
    *,
    charts: bool,
    appendix: bool,
) -> dict[str, Any]:
    """What the template needs, read from the stored issue and run; call inside the run's money
    style and the company's zone (`build` does)."""
    assert bv.issue is not None
    rv = sv.load(s, settings, bv.run, bv.issue.date) if bv.run is not None else None
    findings = []
    for i in bvw.items(bv):
        f = (
            next((x for x in rv.stored if x["fingerprint"] == i["fingerprint"]), None)
            if rv
            else None
        )
        if f is None or rv is None:
            continue
        w = f.get("text") or {}
        story = sv.panel(rv, s, f, "finding")["body"]
        chart = story.get("chart") if charts else None
        findings.append(
            {
                "rank": i.get("rank"),
                "label": w.get("label", f["detector"]),
                "title": i["title"],
                "money": i.get("money", ""),
                "state": _state(f["state"], f.get("confidence")),
                "change": f.get("change") or "",
                "consequence": (i.get("consequence") or {}).get("text"),
                "story": story,
                "chart": _svg(chart, (560, 200)),
                "test": sv.test_tab(rv, s, f) if appendix else None,
                "metric": sv.metric_tab(rv, s, f) if appendix else None,
            }
        )
    for f in findings:
        t = f["test"]
        if t is not None:
            t["chart_svg"] = _svg(t.get("chart"), (560, 200)) if charts else None
    from othor.briefs.ahead import range_chart as range_spec

    b = m.body
    ahead = [
        {
            **r,
            "chart_svg": _svg(
                finding_charts.load(r["chart"]), (300, 130) if r.get("card") else (560, 120)
            )
            if charts and r.get("chart")
            else None,
            # the forecast card's range bar (owner, 16 Nov 2026)
            "range_svg": _svg(range_spec(r["card"]), (260, 56))
            if charts and r.get("card")
            else None,
        }
        for r in b.get("ahead") or []
    ]
    return {
        "m": m,
        "b": b,
        "company": company,
        "logo": "data:image/png;base64," + base64.b64encode(m.logo).decode() if m.logo else None,
        "kicker": " · ".join(
            (
                m.brief_name,
                f"{fmt.date_short(m.date)} {m.date.year}",
                f"{fmt.time_hm(b.get('time') or '06:00')} "
                + messages.tz_label(m.timezone, m.date, str(b.get("time") or "06:00")),
            )
        ),
        "tiles": [{**t, "spark": None} for t in bvw.tiles(bv)],
        "items": bvw.items(bv),
        # What to do (Morning Brief 2.5, #110): the issue's next steps, as the page and email
        "actions": m.actions,
        "since": bvw.since_line(bv),
        "since_word": since_word(bv.body),
        "findings": findings,
        # 0.28.5: "Since last week", with its chart when charts are on
        "week": b.get("week"),
        "week_svg": _svg(finding_charts.load(b["week"]["chart"]), (560, 150))
        if charts and (b.get("week") or {}).get("chart")
        else None,
        "ahead": ahead,
        "appendix": appendix,
        "help_url": settings.help_url.rstrip("/"),  # the Learn link (M20.2)
        "charts": charts,
        "footer": messages.footer_text(m),
        "sources": m.sources,
        "run_footer": sv.footer(rv, s) if rv is not None else "",
        "paper": "A4" if m.currency == "INR" else "letter",
        "fonts": {p.stem: p.as_uri() for p in sorted(FONTS.glob("*.ttf"))},
        "state_text": _state,
    }


def build(
    s: Session,
    settings: Settings,
    company: Company,
    bv: bvw.BriefView,
    *,
    charts: bool = True,
    appendix: bool = True,
) -> bytes:
    import weasyprint  # type: ignore[import-untyped]

    assert bv.issue is not None
    # the run's currency (its spec's company settings), as the email and the stored words have it
    m = delivery.brief_mail(s, company, bv.brief, bv.issue, settings)
    with (
        fmt.display_tz(company.timezone),
        fmt.money_style(m.currency),
        fmt.chart_style(fmt.style_of(company)),  # Settings › Charts (v0.25)
    ):
        ctx = context(s, settings, company, bv, m, charts=charts, appendix=appendix)
        html = env.get_template("brief.html").render(**ctx)
    ident = hashlib.sha256(html.encode()).digest()[:16]
    with fonts_dated(bv.issue.date):
        return bytes(
            weasyprint.HTML(
                string=html, base_url=str(HERE), url_fetcher=LocalOnlyFetcher()
            ).write_pdf(pdf_identifier=ident, uncompressed_pdf=False)
        )


_DATED = threading.Lock()


@contextmanager
def fonts_dated(day: dt.date) -> Iterator[None]:
    """The subset fonts' timestamps are the issue's date, not the clock's: fontTools stamps a
    subset font with `SOURCE_DATE_EPOCH`, else now, so two exports a second apart differed on
    Linux (CI, 14 Nov 2026). Held under a lock: the variable is the process's."""
    epoch = str(int(dt.datetime.combine(day, dt.time(), tzinfo=dt.UTC).timestamp()))
    with _DATED:
        was = os.environ.get("SOURCE_DATE_EPOCH")
        os.environ["SOURCE_DATE_EPOCH"] = epoch
        try:
            yield
        finally:
            if was is None:
                os.environ.pop("SOURCE_DATE_EPOCH", None)
            else:
                os.environ["SOURCE_DATE_EPOCH"] = was
