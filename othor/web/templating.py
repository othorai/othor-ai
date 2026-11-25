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


def chart_of(d: dict[str, Any] | None, width: int = 400, height: int = 160) -> Markup:
    """A chart a view built as a ChartSpec dict (the dashboard's board, 0.29), drawn."""
    if not d:
        return Markup("")
    return chart(ChartSpec.from_dict(d), width, height)


templates.env.globals["chart_of"] = chart_of


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


# a core item whose icon isn't named after its key (What to do, #110)
NAV_ICONS = {"action-plan": "flag", "audit": "clock"}  # owner, 22 Nov 2026 (was check); 0.35


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
        (k, label, href, NAV_ICONS.get(k, k), None, 0) for k, label, href in core
    ]
    # Forecasts 1.0 (21 Nov 2026): an item may follow another plug-in's item (Discover after Look
    # back), so items wait until what they follow is placed; two after the same item keep the
    # plug-ins' order (Forecasts, then Look back, after Summary). What never appears goes last.
    anchor: dict[str, str | None] = {}
    pending = list(plugins.nav(group))
    while pending:
        placed = False
        for n in list(pending):
            keys = [x[0] for x in out]
            if n.after is not None and n.after not in keys:
                continue
            at = keys.index(n.after) + 1 if n.after is not None else len(out)
            while at < len(out) and n.after is not None and anchor.get(out[at][0]) == n.after:
                at += 1
            out.insert(at, (n.key, n.label, n.href, n.icon, n.tag, _badge(ctx, n)))
            anchor[n.key] = n.after
            pending.remove(n)
            placed = True
        if not placed:
            out += [(n.key, n.label, n.href, n.icon, n.tag, _badge(ctx, n)) for n in pending]
            break
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


CARDS_MAX = 3  # Home's Suggested by Othor AI: one row of the brief-card grid


@pass_context
def slot_cards(
    ctx: Any, name: str, limit: int = CARDS_MAX, lead: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """A slot that plug-ins fill with cards (Home's Suggested by Othor AI, 20 Nov 2026). Each
    fragment's context gives `items` (newest first), `total` and `more` ({label, short, href}),
    and may give a `toast` ({text, action, label}: what an action just did, with its Undo); its
    template draws one card from `item`. The cards are interleaved across plug-ins, in load order,
    up to `limit`; a plug-in with more than it got shows its `more` link. None when nothing is
    offered and nothing was just done (the section is hidden); the core knows no plug-in.

    `lead`: the core's own card, first (#76: the top number suggested for the company's size,
    {item, toast}, drawn by components/size_card.html)."""
    from othor.core import plugins

    groups: list[tuple[Any, dict[str, Any]]] = []
    if lead and (lead.get("item") or lead.get("toast")):
        groups.append(
            (
                plugins.Fragment(name, "components/size_card.html"),
                {
                    "items": [lead["item"]] if lead.get("item") else [],
                    "total": 1 if lead.get("item") else 0,
                    **({"toast": lead["toast"]} if lead.get("toast") else {}),
                },
            )
        )
    for f in plugins.fragments(name):
        try:
            extra = f.context(ctx) if f.context is not None else None
        except Exception:  # a plug-in's suggestions never break the page
            import logging

            logging.getLogger("othor.plugins").exception("cards for %s failed", name)
            extra = None
        if extra:
            groups.append((f, extra))
    picked: list[list[Any]] = [[] for _ in groups]
    cards: list[Markup] = []
    depth = 0
    while len(cards) < limit and any(depth < len(g.get("items") or []) for _, g in groups):
        for i, (f, g) in enumerate(groups):
            items = g.get("items") or []
            if depth < len(items) and len(cards) < limit:
                picked[i].append(items[depth])
                cards.append(
                    Markup(  # noqa: S704 - our templates, autoescaped
                        templates.env.get_template(f.template).render(
                            {**ctx.get_all(), **g, "item": items[depth]}
                        )
                    )
                )
        depth += 1
    more = [
        g["more"]
        for i, (_, g) in enumerate(groups)
        if g.get("more") and int(g.get("total") or 0) > len(picked[i])
    ]
    toasts = [g["toast"] for _, g in groups if g.get("toast")]
    count = sum(int(g.get("total") or 0) for _, g in groups)
    if not cards and not toasts:
        return None
    return {"cards": cards, "count": count, "more": more, "toasts": toasts}


# Each side-menu page's one-line description under its title (owner, #108, 21 Nov 2026), by its
# nav key; a plug-in's pages carry their own.
PAGE_LEDES = {
    "home": "What needs you today, and what Othor AI suggests next.",
    "action-plan": "What Othor AI suggests you do about today’s briefs, the most important first.",
    "brief": "The few things that matter each morning, how sure Othor AI is, and who to ask.",
    "sweep": "Every number and breakdown, checked every night for what no single report shows.",
    "summary": "Every number at a glance: what changed, and what moved together.",
    "ontology": "How Othor AI understands your business, and how complete it is.",
    "sources": "Where your numbers come from, and how fresh each source is.",
    "usage": "What Othor AI’s drafting and wording used this month.",
    "audit": "Who did what in Othor AI, and when. Newest first.",
    "settings": "Your company, the model Othor AI writes with, charts, delivery and your data.",
}

templates.env.globals["page_ledes"] = PAGE_LEDES
templates.env.globals["nav_items"] = nav_items
templates.env.globals["slot"] = slot
templates.env.globals["slot_cards"] = slot_cards

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
