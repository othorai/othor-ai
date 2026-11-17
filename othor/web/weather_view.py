# SPDX-License-Identifier: Apache-2.0
"""The weather banner on Home (v0.12 part 1a): the scene, the line and the why, read from the run's
stored `weather.json` (computed at its brief step, never here), and the scene's icon inline."""

from __future__ import annotations

import datetime as dt
import functools
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from markupsafe import Markup

from othor.briefs import weather
from othor.core import fmt
from othor.core.config import Settings
from othor.engine.store import RunFiles
from othor.engine.sweep_text import money

ICONS = Path(__file__).resolve().parent / "static" / "weather" / "icons"
_ID = re.compile(r'\bid="([^"]+)"')

SHOW_PICKED = False  # the banner's "How this was picked" link (owner, 12 Nov 2026: hide for now)


@functools.cache
def _svg(file: str) -> str:
    return (ICONS / f"{file}.svg").read_text().strip()


def icon(file: str, suffix: str) -> Markup:
    """The scene's 48 px icon (currentColor), its ids made unique on the page (No signal's mask):
    several icons on one page never share an id, given a different `suffix` each."""
    svg = _svg(file)
    tag = suffix
    for i in sorted(set(_ID.findall(svg))):
        svg = svg.replace(f'id="{i}"', f'id="{i}-{tag}"').replace(f"#{i})", f"#{i}-{tag})")
    svg = svg.replace("<svg ", '<svg aria-hidden="true" focusable="false" ', 1)
    return Markup(svg)  # noqa: S704 — the owner's own static file, no data in it


def banner(settings: Settings, run: Any) -> dict[str, Any] | None:
    """What the banner shows for a sealed run; None before the first run or for a run sealed
    before v0.12 (no stored weather)."""
    got = _stored(settings, run)
    if got is None:
        return None
    # "How this was picked" is hidden on the banner for now (owner, 12 Nov 2026); the panel
    # stays at its address
    return _banner(got[1], run, "home", "/home?panel=weather" if SHOW_PICKED else "")


def _banner(doc: dict[str, Any], run: Any, suffix: str, picked: str) -> dict[str, Any]:
    s = weather.SCENES[doc["scene"]]
    return {
        "key": s.key,
        "file": s.file,
        "name": s.label,
        "line": doc["line"],
        "icon": icon(s.file, suffix),
        "why": f"From run #{run.number:04d} · " + ", ".join(doc.get("why") or []),
        "picked": picked,
    }


# --- the outlook (part 1b) ---------------------------------------------------------------------

HOME = "/home"


def _stored(
    settings: Settings, run: Any, brief: Any = None
) -> tuple[RunFiles, dict[str, Any]] | None:
    """The company's stored weather (Home), or with `brief` that brief's own (part 1c), with its
    scope as `brief_scope`."""
    if run is None:
        return None
    files = RunFiles(settings.runs_dir(run.company_id), run.id)
    if brief is not None:
        got = weather.stored_brief(files, brief)
        return None if got is None else (files, {**got["weather"], "brief_scope": got["scope"]})
    doc = weather.stored(files)
    return None if doc is None or doc.get("scene") not in weather.SCENES else (files, doc)


def outlook(settings: Settings, run: Any) -> dict[str, Any] | None:
    """The four week cards and the Ahead line, as stored with the run's weather (computed at its
    brief step, never here). None for a run sealed before part 1b."""
    got = _stored(settings, run)
    return _outlook(got[1] if got else None, f"{HOME}?", "wk")


def _outlook(doc: dict[str, Any] | None, here: str | None, suffix: str) -> dict[str, Any] | None:
    """`here`: the page's address with its query, ending in "?" or "&", that the panel links
    add to; None on a read-only page (the recipients' view: no panel links)."""
    o = (doc or {}).get("outlook")
    if not o or not o.get("weeks"):
        return None
    weeks = [
        {
            "week": w["week"],
            "key": w["scene"],
            "name": w["name"],
            "dates": w["dates"],
            "label": w["label"],
            "line": w["line"],
            "icon": icon(w["file"], f"{suffix}{w['week']}"),
            "worst": o.get("worst") == w["week"],
            "href": f"{here}panel=forecast&week={w['week']}" if here is not None else None,
        }
        for w in o["weeks"]
    ]
    link = o.get("ahead") and here is not None
    return {
        "title": f"Ahead · next {fmt.num(len(weeks))} weeks",
        "hint": "from forecasts, goals and deadlines",
        "weeks": weeks,
        "ahead": o.get("ahead_line"),
        "href": f"{here}panel=forecast&week=ahead" if link else None,
    }


# --- each brief's own (part 1c) ----------------------------------------------------------------


def brief_weather(
    settings: Settings, run: Any, brief: Any, here: str | None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """A brief's banner and outlook, from its run's stored per-brief weather (computed at the
    brief step, never here). `here`: the brief page's address the panel links add to; None for
    the read-only recipients' view. (None, None) for a run sealed before part 1c."""
    got = _stored(settings, run, brief)
    if got is None:
        return None, None
    return _banner(got[1], run, "brief", ""), _outlook(got[1], here, "bwk")


def _item(o: dict[str, Any], week: str | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The week asked for and the item its panel shows: the week's top item, or for "ahead" the
    Ahead line's item."""
    weeks = {str(w["week"]): w for w in o["weeks"]}
    if week == "ahead" and o.get("ahead"):
        w = weeks[str(o["ahead"]["week"])]
        ref = o["ahead"]["item"]
    else:
        w = weeks.get(week or "") or o["weeks"][0]
        ref = w.get("top")
    item = next((x for x in w["items"] if f"{x['kind']}:{x['key']}" == ref), None)
    return w, item


def _params(row: dict[str, Any], unit: str | None) -> str:
    from othor.briefs import values

    p = row.get("params") or {}
    parts = []
    if p.get("sigma") is not None:
        parts.append(f"one-week error σ {values.text(unit, p['sigma'])}")
    if row.get("aicc") is not None:
        parts.append(f"AICc {fmt.num(Decimal(str(row['aicc'])), decimals=1)}")
    parts.append("seasonal" if p.get("seasonal") else "no seasonality")
    return ", ".join(parts)


def forecast_panel(
    s: Any, settings: Settings, run: Any, week: str | None, brief: Any = None
) -> dict[str, Any] | None:
    """The forecast panel (part 1b), for a week card or the Ahead line: the item and its line,
    the chart (a ChartSpec from the stored forecast), what's driving it, how it is calculated,
    and the actions. A Limited-view week gets the short explanation instead. With `brief`, that
    brief's outlook (part 1c): what's driving it is only what's in the brief's scope."""
    import sqlalchemy as sa

    from othor.briefs import outlook as ol
    from othor.briefs import scope
    from othor.engine.store import read_json, read_points_cached
    from othor.ontology.models import Spec

    got = _stored(settings, run, brief)
    o = got[1].get("outlook") if got else None
    if got is None or not o or not o.get("weeks"):
        return None
    files = got[0]
    sc = got[1].get("brief_scope") or {}
    w, item = _item(o, week)
    n = run.number
    head = {
        "kicker": f"{w['name']} · {w['dates']} · {w['label']}",
        "title": (item or {}).get("name") or (item or {}).get("title") or w["label"],
        "line": o["ahead_line"] if week == "ahead" and o.get("ahead") else w["line"],
    }
    if item is None:
        return {**head, "kind": "empty", "text": w["line"]}
    if item.get("verdict") == "wide":
        return {**head, "kind": "wide", "text": item.get("why") or w["line"]}
    row = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).first()
    spec = dict(row.compiled) if row else {}
    rows = [r for r in (read_json(files.outputs / weather.AHEAD) or {}).get("rows") or []]
    chart = ol.chart(spec, item, rows, list(read_points_cached(files.series)), 12)
    metric = item.get("metric")
    findings = [
        f for f in read_json(files.outputs / "findings.json") or [] if scope.finding_in(spec, sc, f)
    ]
    driving = [
        i
        for i in weather.items(findings, spec, run.run_date)
        if (item["kind"] == "finding" and i.f.get("fingerprint") == item["key"])
        or (metric and i.f.get("metric") == metric)
    ][:4]
    mine = [r for r in rows if r["metric"] == metric]
    fr = next((r for r in mine if r["horizon"] == item.get("horizon")), mine[0] if mine else None)
    prov = []
    if fr is not None:
        prov = [
            ("Method", f"{fr['method']} · exponential smoothing, {fmt.pct(ol.LEVEL)} band"),
            ("Parameters", _params(fr, (spec.get("metrics", {}).get(metric) or {}).get("unit"))),
            ("Made", f"Run #{n:04d} · data to {fmt.day_month(dt.date.fromisoformat(fr['as_of']))}"),
            ("Weeks of history", fmt.num(int((fr.get("params") or {}).get("n") or 0))),
        ]
    plan = (o.get("plans") or {}).get(metric or "")
    if plan and item["kind"] == "forecast":
        prov.append(("Plan", plan["how"]))
    prov += [
        ("Spec version", f"Ontology v{spec.get('ontology_version', '')}"),
        ("Run", f"#{n:04d} · {fmt.day_month(run.run_date)}"),
    ]
    goals = [g for g in spec.get("forecast", {}).get("goals", []) if g.get("metric") == metric]
    top_fp = driving[0].f.get("fingerprint") if driving else None
    return {
        **head,
        "kind": "forecast",
        "chart": chart,
        "driving": [
            {
                "title": weather._title(i.f),
                "money": money(i.money) if i.money else "",
                "href": f"/sweep/finding/{i.f.get('fingerprint')}?run={n}",
            }
            for i in driving
        ],
        "prov": prov,
        "sweep": f"/sweep/finding/{top_fp}?run={n}" if top_fp else None,
        "goal": f"/ontology?metric={metric}" if goals and metric else None,
    }


def picked_panel(settings: Settings, run: Any) -> dict[str, Any] | None:
    """ "How this was picked" (part 1b): the rule that picked today's scene in plain words, and
    the facts it used as links (the items → Sweep's Found tab, the nearest date → that finding,
    the run → its provenance)."""
    got = _stored(settings, run)
    if got is None:
        return None
    doc = got[1]
    f = doc.get("facts") or {}
    n = run.number
    near = f.get("nearest") or None
    facts: list[dict[str, Any]] = []
    if f.get("items"):
        facts.append(
            {
                "k": "Open items",
                "v": fmt.num(int(f["items"])),
                "href": f"/sweep?run={n}&tab=findings",
            }
        )
    if Decimal(str(f.get("at_stake") or 0)) > 0:
        facts.append({"k": "At stake", "v": money(f["at_stake"]), "href": None})
    if near:
        facts.append(
            {
                "k": "Nearest date",
                "v": fmt.day_month(dt.date.fromisoformat(near["date"])),
                "href": f"/sweep/finding/{near['fingerprint']}?run={n}",
            }
        )
    if f.get("checks"):
        facts.append(
            {
                "k": "Checks",
                "v": f"{fmt.num(int(f.get('checks_ran') or 0))} of {fmt.num(int(f['checks']))} ran",
                "href": None,
            }
        )
    fp = (near or {}).get("fingerprint") or f.get("top")
    facts.append(
        {
            "k": "Run",
            "v": f"#{n:04d} · {fmt.day_month(run.run_date)}",
            "href": f"/sweep/finding/{fp}?run={n}&tab=run" if fp else f"/sweep?run={n}",
        }
    )
    s = weather.SCENES[doc["scene"]]
    return {
        "kicker": f"Today · {s.label}",
        "title": "How this was picked",
        "line": doc["line"],
        "rule": doc.get("rule") or weather.rule(doc["scene"], doc.get("settings") or {}),
        "why": doc.get("why") or [],
        "facts": facts,
    }
