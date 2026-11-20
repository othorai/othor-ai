# SPDX-License-Identifier: Apache-2.0
"""Jinja2 environment shared by pages and HTMX partials: autoescape on, one formatter, charts
rendered from ChartSpec only (never drawn in a template)."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader, pass_context
from markupsafe import Markup

from othor.charts.spec import ChartSpec
from othor.charts.svg import render_svg
from othor.core import contact, features, fmt
from othor.web.ui import ui

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.globals["fmt"] = fmt
# the install's outward links (M20.2); create_app sets them from the settings
templates.env.globals["install"] = {
    "help_url": "https://othor.ai",
    "legal_url": "",
    "support_email": "",
    "trial_days": 0,
}
templates.env.globals["features"] = features
# Contact us (owner, 17 Nov 2026): its topics and team sizes, one list for the site and the app
templates.env.globals["contact"] = {"topics": contact.TOPICS, "sizes": contact.TEAM_SIZES}


def chart(spec: ChartSpec, width: int = 400, height: int = 160, legend: bool = False) -> Markup:
    """`legend`: the series names go in a legend the page draws above (Big Picture, M19.5)."""
    return Markup(render_svg(spec, (width, height), legend=legend))  # noqa: S704 - our own SVG


templates.env.globals["chart"] = chart


def stored_chart(text: str, width: int = 400, height: int = 160) -> Markup:
    """A chart stored in an issue body as canonical JSON (finding_charts.dump), drawn."""
    from othor.engine.finding_charts import load

    return chart(load(text), width, height)


templates.env.globals["stored_chart"] = stored_chart


def forecast_range(card: dict[str, object], width: int = 304, height: int = 64) -> Markup:
    """A forecast card's range bar (Big Picture's, and What's ahead's from 16 Nov 2026), drawn
    from the card's computed positions."""
    from othor.briefs.ahead import range_chart as range_spec

    return chart(range_spec(card), width, height)


templates.env.globals["forecast_range"] = forecast_range

# --- plug-ins (M20.1, v0.20) ----------------------------------------------------------------------


def use_plugins(dirs: list[Path]) -> None:
    """Plug-in template folders after the core's: a plug-in can't replace a core template."""
    templates.env.loader = ChoiceLoader(
        [FileSystemLoader(str(HERE / "templates")), *(FileSystemLoader(str(d)) for d in dirs)]
    )


@pass_context
def nav_items(
    ctx: Any, group: str, core: list[tuple[str, str, str]]
) -> list[tuple[str, str, str, str, str | None, int]]:
    """A side-menu group: the core's items (key, label, href; the icon is the key), with each
    plug-in item after the core item it names, or at the group's end (owner, 16 Nov 2026:
    enterprise items sit in the company group, above Setup). The last field is the item's
    badge count (a plug-in's `NavItem.badge`, Advanced Intelligence Phase 0; 0 = none)."""
    from othor.core import plugins

    out: list[tuple[str, str, str, str, str | None, int]] = [
        (k, label, href, k, None, 0) for k, label, href in core
    ]
    for n in plugins.nav(group):
        at = next((i + 1 for i, x in enumerate(out) if x[0] == n.after), len(out))
        out.insert(at, (n.key, n.label, n.href, n.icon, n.tag, _badge(ctx, n)))
    return out


def _badge(ctx: Any, n: Any) -> int:
    """A plug-in item's count, from its own query; any failure shows no badge."""
    if n.badge is None:
        return 0
    company, request = ctx.get("company"), ctx.get("request")
    if company is None or request is None or getattr(company, "id", None) is None:
        return 0
    try:
        from othor.core.tenancy import tenant_scope
        from othor.web.context import services

        with services(request).sf() as s, tenant_scope(company.id):
            return max(int(n.badge(s, company) or 0), 0)
    except Exception:
        import logging

        logging.getLogger("othor.plugins").exception("nav badge %s failed", n.key)
        return 0


@pass_context
def slot(ctx: Any, name: str) -> Markup:
    """A named place inside an open feature that a plug-in may fill (`Fragment`); empty when
    none does, so a page without plug-ins is byte-for-byte as before."""
    from othor.core import plugins

    parts = []
    for f in plugins.fragments(name):
        extra = f.context(ctx) if f.context is not None else {}
        if extra is None:
            continue
        parts.append(templates.env.get_template(f.template).render({**ctx.get_all(), **extra}))
    return Markup("".join(parts))  # noqa: S704 - our templates, autoescaped


templates.env.globals["nav_items"] = nav_items
templates.env.globals["slot"] = slot

MANIFEST = HERE / "static" / "css" / "manifest.json"
_manifest: dict[str, object] = {"mtime": None, "map": {}}


def asset(name: str) -> str:
    """The built file for a logical asset name (`app.css` → `/static/css/app.<sha8>.css`), from
    the manifest written by scripts/build_css.py. Re-read when the manifest changes (dev)."""
    import json

    if not name.endswith(".css"):  # a vendored file: cache-busted by its pinned SHA-256
        return _vendored(name)
    try:
        mtime = MANIFEST.stat().st_mtime
    except OSError:
        return f"/static/css/{name}"  # not built: `make css`
    if _manifest["mtime"] != mtime:
        _manifest["map"] = json.loads(MANIFEST.read_text())
        _manifest["mtime"] = mtime
    table: dict[str, str] = _manifest["map"]  # type: ignore[assignment]
    return "/static/" + table.get(name, f"css/{name}")


@functools.cache
def _vendored(name: str) -> str:
    import json

    pinned = json.loads((HERE / "static" / "MANIFEST.json").read_text()).get(name, {})
    sha = str(pinned.get("sha256", ""))[:8]
    return f"/static/{name}" + (f"?v={sha}" if sha else "")


templates.env.globals["asset"] = asset
templates.env.globals["ui"] = ui


def _mark(text: str, q: str) -> Markup:
    """Highlight a search term (2kd), escaping everything else."""
    import re

    if not q:
        return Markup.escape(text)
    parts = re.split(f"({re.escape(q)})", text, flags=re.IGNORECASE)
    return Markup("").join(
        Markup("<mark>{}</mark>").format(p) if p.lower() == q.lower() else Markup.escape(p)
        for p in parts
    )


templates.env.filters["mark"] = _mark


def _prose(text: str) -> Markup:
    """An Othor AI answer as formatted text (0.28.4, owner 19 Nov 2026): paragraphs at blank
    lines, **bold**, and lines starting "- " as a list. Everything is escaped first; nothing else
    a model writes becomes markup (no links, no HTML)."""
    import re

    out: list[Markup] = []
    for block in re.split(r"\n\s*\n", (text or "").strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue

        def inline(s: str) -> Markup:
            parts = re.split(r"\*\*(.+?)\*\*", s)  # odd parts were in **bold**
            return Markup("").join(
                Markup("<b>{}</b>").format(p) if i % 2 else Markup.escape(p)
                for i, p in enumerate(parts)
            )

        if all(ln.startswith(("- ", "• ")) for ln in lines):
            items = Markup("").join(Markup("<li>{}</li>").format(inline(ln[2:])) for ln in lines)
            out.append(Markup("<ul>{}</ul>").format(items))
        else:
            out.append(Markup("<p>{}</p>").format(inline(" ".join(lines))))
    return Markup("").join(out)


templates.env.filters["prose"] = _prose


# the website's `fill` and `site_links` filters moved with it into the othor_site plug-in (M20.2)
