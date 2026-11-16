# SPDX-License-Identifier: Apache-2.0
"""One brief issue from one run (docs/04 §8–9, M8). Pure: the same run outputs and brief version
give the same bytes, so the run's `brief` step, replay and the previews (New brief 4c1, Edit
brief's "Today with this change") all call this one function.

The issue: the question line, the title and line (templates, each clause only when true), the
headline tiles, up to 4 ranked items with their charts, the Watching line, Ask today, the full
detail (Situation, Since yesterday) and the counts against Sweep ("N more in Sweep"). No model:
a rewrite of the prose happens after the run, outside it, behind the numbers guard.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from othor.briefs import ahead, consequence, rank, scope, tiles, title, values
from othor.core import fmt
from othor.engine.store import Point
from othor.engine.sweep_text import money, money_tag

FORMAT = 1
HEADLINE_MAX = 6
NO_GOAL_QUESTION = "What changed in the business since yesterday, and where do I need to step in?"


# --- the default brief (decided 5 Nov 2026): created at the first run, no model ---------------


def default_version(spec: dict[str, Any]) -> dict[str, Any]:
    goals = sorted(spec.get("forecast", {}).get("goals", []), key=lambda g: (g["by"], g["key"]))
    metrics = spec.get("metrics", {})
    year = next((g for g in goals if g.get("aggregate") == "fiscal_year_total"), None)
    top = year or (goals[0] if goals else None)
    if top is None:
        question = NO_GOAL_QUESTION
    elif (
        top.get("aggregate") == "fiscal_year_total"
        and metrics.get(top["metric"], {}).get("unit") == "inr"
    ):
        question = (
            f"Are we on track for {money(top['target'])} this year, and where do I need to step in?"
        )
    else:
        question = f"Are we on track for {top['name']}, and where do I need to step in?"
    active = [k for k, m in metrics.items() if m.get("status") == "active"]
    headline: list[str] = []
    for g in ([year] if year else []) + [g for g in goals if g is not year]:
        if g["metric"] in active and g["metric"] not in headline:
            headline.append(g["metric"])
    for k in sorted(active):
        if metrics[k].get("headline") and k not in headline:
            headline.append(k)
    return {
        "name": "Leadership",
        "question": question,
        "scope": {"metrics": None, "exclude": {}},
        "headline": headline[:HEADLINE_MAX],
        "ask_owner": "top_item",
        "goals": [g["key"] for g in goals],
        "schedule": "daily",
        "time": "06:00",
        "email_format": "simple",
    }


# --- the issue ---------------------------------------------------------------------------------


def with_pinned(
    ranked: list[dict[str, Any]],
    found: list[dict[str, Any]],
    spec: dict[str, Any],
    run_date: dt.date,
    pinned: list[str],
) -> list[dict[str, Any]]:
    """A finding the owner added to this brief (10o) is always one of its items while it is open
    and in scope, in its ranked place; the lowest of the rest makes room ("item 4 moves to more
    in Sweep")."""
    from othor.engine.findings import pattern_of

    if not pinned:
        return ranked
    want = set(pinned)
    full = rank.rank(found, spec, run_date, n=len(found))
    pins = [f for f in full if pattern_of(f) in want]
    if not pins or all(f in ranked for f in pins):
        return ranked
    room = rank.ITEMS - len(pins)
    others = [f for f in full if f not in pins][: max(room, 0)]
    keep = {id(f) for f in pins + others}
    return [f for f in full if id(f) in keep][: rank.ITEMS]


def _names(spec: dict[str, Any]) -> dict[str, str]:
    return {k: str(m.get("name") or k) for k, m in spec.get("metrics", {}).items()}


def _item(
    f: dict[str, Any], n: int, spec: dict[str, Any], run_date: dt.date, stale: set[str]
) -> dict[str, Any]:
    w = f.get("text") or {}
    c = consequence.of(f, spec, run_date)
    return {
        "rank": n,
        "fingerprint": f["fingerprint"],
        "detector": f["detector"],
        "metric": f["metric"],
        "label": w.get("label", f["detector"]),
        "state": f["state"],
        "confidence": f.get("confidence"),
        "change": f.get("change", ""),
        "money": money_tag(f),
        "title": w.get("title", ""),
        "line": w.get("line", ""),
        "consequence": c,
        "chart": f.get("chart"),
        "rows": _rows(w.get("rows")) if not f.get("chart") else None,
        "link": "reasons" if f["state"] == "not_determinable" else "calc",
        "stale": f["metric"] in stale,
        "score": str(rank.score(f, spec, run_date).quantize(Decimal("0.01"))),
    }


def _rows(r: dict[str, Any] | None, n: int = 3) -> dict[str, Any] | None:
    """A card with no chart by design (missing records) shows its top rows instead (4d)."""
    if not r or not r.get("rows"):
        return None
    keep = (
        [0, 1, len(r["headers"]) - 1] if len(r["headers"]) > 3 else list(range(len(r["headers"])))
    )
    return {
        **r,
        "headers": [r["headers"][i] for i in keep],
        "rows": [[row[i] for i in keep] for row in r["rows"][:n]],
        "more": max(len(r["rows"]) - n, 0),
    }


def _breach(w: dict[str, Any]) -> Decimal:
    """How far past its threshold a breached watch is, as a share: the Watching line names the
    largest breach first (M10b; it followed watch ids before)."""
    try:
        v, t = Decimal(str(w.get("value"))), Decimal(str(w.get("threshold")))
    except (ArithmeticError, ValueError):
        return Decimal(0)
    return abs(v - t) / abs(t) if t else abs(v - t)


def _watching(
    spec: dict[str, Any], sc: dict[str, Any], watches: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out = []
    for w in sorted(watches, key=lambda x: (-_breach(x), str(x.get("id")))):
        if w.get("status") != "breached" or not scope.watch_in(spec, sc, w):
            continue
        kind = (w.get("indicator") or {}).get("kind")
        value = values.indicator_text(w["value"], kind) if w.get("value") is not None else "—"
        thr = values.indicator_text(w["threshold"], kind)
        out.append(
            {
                "id": w["id"],
                "label": w["label"],
                "text": (
                    f"{w['label']} · {value} · alert {w['direction']} {thr}"
                    + (f" · owner {w['owner_name']}" if w.get("owner_name") else "")
                ),
            }
        )
    return out


def goal_lines(
    spec: dict[str, Any],
    keys: list[str],
    goals: list[dict[str, Any]],
    sc: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The goals a brief watches (decided 6 Nov 2026): "Net revenue FY27 · ₹60 Cr target ·
    tracking ₹57.2 Cr (80% band ₹55.9–58.5 Cr)". Tone: `off` (red) when the whole band misses
    the target, `risk` (amber) when the target is inside the band, `ok` otherwise."""
    metrics = spec.get("metrics", {})
    out = []
    for g in goals:
        if g["key"] not in keys or (sc is not None and not scope.goal_in(spec, sc, g)):
            continue
        m = metrics.get(g["metric"], {})
        unit = m.get("unit")
        name = str(m.get("name") or g["metric"])
        by = dt.date.fromisoformat(str(g["by"]))
        target = values.rounded(unit, g["target"])
        tracking = values.rounded(unit, g["projection"])
        if g.get("aggregate") == "fiscal_year_total":
            head = f"{name} FY{by.year % 100:02d} · {target} target"
        else:
            head = f"{name} · {target} target by {fmt.day_month(by)}"
        text = f"{head} · tracking {tracking}"
        tone = "ok"
        if g.get("lower") is not None and g.get("upper") is not None:
            text += f" (80% band {values.band(unit, g['lower'], g['upper'])})"
            lo, hi, t = (Decimal(str(g[k])) for k in ("lower", "upper", "target"))
            miss = hi < t if g.get("direction", "at_least") == "at_least" else lo > t
            tone = "off" if miss else ("risk" if lo <= t <= hi else "ok")
        elif not g.get("on_track"):
            tone = "off"
        out.append(
            {"key": g["key"], "text": text, "tone": tone, "on_track": bool(g.get("on_track"))}
        )
    return out


class _Spec:
    """The one thing `detect.pattern_applies` reads from a run's context: a metric's record."""

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec

    def metric(self, key: str) -> dict[str, Any]:
        return dict(self.spec.get("metrics", {}).get(key) or {})


def _known(
    spec: dict[str, Any], metric: str, run_date: dt.date
) -> list[tuple[dt.date, dt.date, str, list[str]]]:
    """Confirmed known patterns that cover this metric's total (the same rule as Sweep: an
    entity's pause never explains a total), with their settle-by, to name them on a tile."""
    from othor.engine.detect import pattern_applies, windows

    out: list[tuple[dt.date, dt.date, str, list[str]]] = []
    for p in spec.get("suppressions", []):
        if p.get("confirmed", True) is False:
            continue
        if not pattern_applies(_Spec(spec), p, metric, None, []):  # type: ignore[arg-type]
            continue
        settle = dt.timedelta(days=int(p.get("settle_days") or 0))
        label = str(p.get("short") or p.get("name") or p.get("key"))
        for a, b in windows(p, range(run_date.year - 1, run_date.year + 1)):
            out.append((a, b + settle, label, []))
    return out


def _since(found: list[dict[str, Any]], run_date: dt.date) -> dict[str, int]:
    open_ = [f for f in found if f["status"] == "open"]
    return {
        "new": sum(1 for f in open_ if f.get("change") == "New"),
        "worse": sum(1 for f in open_ if f.get("change") == "Worse"),
        "better": sum(1 for f in open_ if f.get("change") == "Better"),
        # a finding that came back within the week is open again (findings.CAME_BACK)
        "open": sum(1 for f in open_ if str(f.get("change", "")).startswith(("Open", "Came back"))),
        "resolved": sum(
            1
            for f in found
            if f["status"] == "resolved"
            and (f.get("resolved") or {}).get("date") == run_date.isoformat()
        ),
    }


def _period_tag(f: dict[str, Any], was: dict[str, Any] | None) -> str:
    """A Weekly or Monthly issue's change tag (M15.7c part B): against the brief's previous
    issue, not last night. The same 10% rule as the nightly tags (`findings._tag`)."""
    from othor.engine.findings import MOVED

    if was is None:
        return "New"
    a, b = Decimal(was.get("money") or 0), Decimal(f.get("money") or 0)
    if a and b > a * (1 + MOVED):
        return "Worse"
    if a and b < a * (1 - MOVED):
        return "Better"
    return f"Open · day {fmt.num(int(f.get('days_open') or 1))}"


def _since_period(
    spec: dict[str, Any], sc: dict[str, Any], found: list[dict[str, Any]], was: dict[str, Any]
) -> dict[str, int]:
    """What changed in the period: tonight's open findings by their period tag, and those open
    at the previous issue (in this brief's scope) that are open no longer."""
    open_ = [f for f in found if f["status"] == "open"]
    now = {f["fingerprint"] for f in open_}
    tags = [str(f.get("change", "")) for f in open_]
    return {
        "new": tags.count("New"),
        "worse": tags.count("Worse"),
        "better": tags.count("Better"),
        "open": sum(1 for t in tags if t.startswith("Open")),
        "resolved": sum(
            1 for fp, w in sorted(was.items()) if fp not in now and scope.finding_in(spec, sc, w)
        ),
    }


FIRST_METRICS = 4
FIRST_METRICS_NOTE = "No headline numbers chosen yet; showing your first confirmed numbers."


def tile_metrics(spec: dict[str, Any], version: dict[str, Any], points: list[Point]) -> list[str]:
    """The numbers a brief's tiles show: its chosen headline numbers, or, when none are chosen,
    the first confirmed numbers in its scope that have data (as `_first_metrics` picks them).
    The forecast step forecasts these (v0.19 fix, 15 Nov 2026), so What's ahead has a forecast
    for every tile. Pure; from the run's sealed context, so replay agrees."""
    chosen = list(version.get("headline") or [])
    if chosen:
        return chosen
    metrics = spec.get("metrics", {})
    sc = version.get("scope") or {}
    have = {p.metric for p in points if p.value is not None}
    out: list[str] = []
    for key in spec.get("order") or sorted(metrics):
        m = metrics.get(key) or {}
        if m.get("status") != "active" or m.get("listed") is False:
            continue
        if not scope.metric_in(spec, sc, key) or key not in have:
            continue
        out.append(key)
        if len(out) == FIRST_METRICS:
            break
    return out


def _first_metrics(
    spec: dict[str, Any],
    sc: dict[str, Any],
    points: list[Point],
    record: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    run_date: dt.date,
    stale_as_of: dict[str, dt.date] | None,
) -> list[dict[str, Any]]:
    """A brief with no headline numbers shows the first confirmed metrics that have a value, in
    the Ontology's order, as the first-run screen (2o) does (owner, 7 Nov 2026; v0.11)."""
    metrics = spec.get("metrics", {})
    out: list[dict[str, Any]] = []
    for key in spec.get("order") or sorted(metrics):
        m = metrics.get(key) or {}
        if m.get("status") != "active" or m.get("listed") is False:
            continue
        if not scope.metric_in(spec, sc, key):
            continue
        t = tiles.tile(
            spec,
            key,
            points,
            record,
            goals,
            run_date,
            stale_as_of=stale_as_of,
            known=_known(spec, key, run_date),
        )
        if t:
            out.append(t)
        if len(out) == FIRST_METRICS:
            break
    return out


def assemble(
    spec: dict[str, Any],
    version: dict[str, Any],
    *,
    run_date: dt.date,
    findings: list[dict[str, Any]],
    watches: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    record: list[dict[str, Any]],
    points: list[Point],
    sweep_counts: dict[str, int],
    tests: int,
    stale_as_of: dict[str, dt.date] | None = None,
    forecast: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sc = version.get("scope") or {}
    names = _names(spec)
    stale = set(stale_as_of or {})
    found = [f for f in findings if scope.finding_in(spec, sc, f)]
    for f in found:
        f.setdefault("stale", f["metric"] in stale)
    period = version.get("period")
    if period:  # Weekly or Monthly: tagged against the brief's previous issue (M15.7c)
        was = period.get("open") or {}
        for f in found:
            if f["status"] == "open":
                f["change"] = _period_tag(f, was.get(f["fingerprint"]))
    ranked = rank.rank(found, spec, run_date, previous=version.get("previous") or [])
    ranked = with_pinned(ranked, found, spec, run_date, version.get("pinned") or [])
    items = [_item(f, i + 1, spec, run_date, stale) for i, f in enumerate(ranked)]
    ts = [
        t
        for m in version.get("headline") or []
        if (
            t := tiles.tile(
                spec,
                m,
                points,
                record,
                goals,
                run_date,
                stale_as_of=stale_as_of,
                known=_known(spec, m, run_date),
            )
        )
    ]
    shown_tiles = ts or _first_metrics(spec, sc, points, record, goals, run_date, stale_as_of)
    quiet = not items
    ask = None
    if quiet:
        head = title.quiet_title(ts, names)
        floor = money(spec.get("detectors", {}).get("materiality_inr", "500000"))
        on_plan = bool(ts) and all(t["comparison"]["text"].startswith(title.ON_PLAN) for t in ts)
        lead = ""
        if on_plan:
            lead = (
                "The headline number is on plan. "
                if len(ts) == 1
                else f"All {fmt.num(len(ts))} headline numbers are on plan. "
            )
        sub = lead + (
            f"{fmt.num(tests)} tests found nothing above the {floor} floor in this brief’s scope."
        )
    else:
        head = title.lead_headline(ranked)
        sub = title.line(ts, names)
        if version.get("ask_owner", "top_item") == "top_item":
            ask = title.ask_today(ranked, spec.get("owners") or [])
        else:
            got = title.ask_today(ranked, spec.get("owners") or [])
            ask = {**got, "to": version["ask_owner"], "role": ""} if got else None
    shown = {i["fingerprint"] for i in items}
    in_sweep = sweep_counts.get("findings", 0)
    drill = ((ranked[0].get("slots") or {}).get("drill") or {}) if ranked else {}
    pause = title.pause_sentence(drill)
    # a known pause the title had no room for goes first (decided 6 Nov 2026)
    first = pause if pause and title.paused_note(drill) not in head else ""
    situation = " ".join(
        x
        # the title is already above the Situation: it isn't said twice (4d1, M12b)
        for x in [
            first,
            title.headline(ranked) if ranked and title.headline(ranked) != head else "",
            *[i["line"] for i in items],
        ]
        if x
    )
    # What's ahead follows the tiles the brief shows: its chosen headline numbers, or the first
    # confirmed numbers when none are chosen (v0.19 fix, 15 Nov 2026)
    on_tiles = {**version, "headline": [x["metric"] for x in shown_tiles if x.get("metric")]}
    ahead_rows = ahead.rows(spec, on_tiles, forecast or {}, points, today=run_date)
    issue = {
        "format": FORMAT,
        "brief": {"name": version.get("name", ""), "version": version.get("number", 1)},
        "date": run_date.isoformat(),
        "question": version.get("question", ""),
        "time": version.get("time", "06:00"),
        "quiet": quiet,
        "title": head,
        "line": sub,
        "tiles": shown_tiles,
        "items": items,
        "more_in_sweep": max(in_sweep - len(shown & {f["fingerprint"] for f in findings}), 0),
        "since": _since_period(spec, sc, found, period.get("open") or {})
        if period
        else _since(found, run_date),
        "watching": _watching(spec, sc, watches),
        "ask": ask,
        "situation": situation,
        "goals": goal_lines(spec, version.get("goals") or [], goals, sc),
        # v0.19 (M19.4): where each headline number is heading, in plain words
        "ahead": ahead_rows,
        "ahead_note": ahead.note(spec, forecast or {}, ahead_rows),
        "stale": sorted(stale),
        "checks": {
            "title": [] if quiet else title.check_title(head, ranked),
            "ask": title.check_ask(ask) if ask else [],
        },
        "tests": tests,
        "prose": {"source": "template"},
    }
    if period:  # only then, so a daily issue keeps its bytes
        issue["period"] = {"from": period["from"], "word": period["word"]}
    if shown_tiles and not ts:
        # no headline numbers chosen: the first confirmed metrics with a value (v0.11); only then,
        # so every other issue keeps its bytes
        issue["tiles_note"] = FIRST_METRICS_NOTE
    return issue
