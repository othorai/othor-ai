# SPDX-License-Identifier: Apache-2.0
"""Edit brief (4d2–4d6) and New brief (4c1): a draft version, checked against the spec, with
what it does today computed before it is saved. Nothing changes until Save, which writes a new
version (and an event); it applies from the next run.

The draft travels with the form, so every request re-checks it here: unknown metrics, breakdown
values, owners or goals are dropped, never trusted.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from othor.briefs.schedule import SCHEDULES, clean_on
from othor.engine.store import Point

HEADLINE_MAX = 6
TABS = ("question", "scope", "numbers", "delivery")
FORMATS = ("simple", "full")
TIME_RE = r"^([01]\d|2[0-3]):[0-5]\d$"


def scope_options(spec: dict[str, Any], points: Iterable[Point]) -> dict[str, list[str]]:
    """The values each low-cardinality breakdown has in the run (Region: South, West, …)."""
    return scope_options_from(spec, month_values(points))


def month_values(points: Iterable[Point]) -> dict[tuple[str, str], frozenset[str]]:
    """(metric, breakdown) → the breakdown's values at the month grain (pages cache this per
    run file, M15.1)."""
    out: dict[tuple[str, str], set[str]] = {}
    for p in points:
        if p.breakdown and p.breakdown_value and p.grain == "month":
            out.setdefault((p.metric, p.breakdown), set()).add(p.breakdown_value)
    return {k: frozenset(v) for k, v in out.items()}


def scope_options_from(
    spec: dict[str, Any], values: dict[tuple[str, str], frozenset[str]]
) -> dict[str, list[str]]:
    keep = {k for k, b in (spec.get("breakdowns") or {}).items() if not b.get("entity")}
    flows = {k for k, m in spec.get("metrics", {}).items() if m.get("kind", "flow") == "flow"}
    out: dict[str, set[str]] = {}
    for (metric, b), vals in values.items():
        if b in keep and metric in flows:
            out.setdefault(b, set()).update(vals)
    first = ("region", "segment", "plant")  # the order the boards use (4d3)
    order = sorted(out, key=lambda k: (first.index(k) if k in first else len(first), k))
    return {k: sorted(out[k]) for k in order}


def clean(
    body: dict[str, Any], spec: dict[str, Any], options: dict[str, list[str]]
) -> dict[str, Any]:
    import re

    metrics = spec.get("metrics", {})
    active = {k for k, m in metrics.items() if m.get("status") == "active"}
    owners = {o["name"] for o in spec.get("owners") or []}
    goals = {g["key"] for g in spec.get("forecast", {}).get("goals", [])}
    sc = body.get("scope") or {}
    ms = sc.get("metrics")
    exclude = {
        b: sorted({v for v in vals if v in options.get(b, [])})
        for b, vals in (sc.get("exclude") or {}).items()
        if b in options
    }
    headline = [m for m in dict.fromkeys(body.get("headline") or []) if m in active][:HEADLINE_MAX]
    ask = body.get("ask_owner") or "top_item"
    time = str(body.get("time") or "06:00")
    sched = str(body.get("schedule")) if body.get("schedule") in SCHEDULES else "daily"
    on = clean_on(sched, body.get("on"))
    out = {
        "name": str(body.get("name") or "Brief").strip()[:120] or "Brief",
        "question": str(body.get("question") or "").strip()[:300],
        "scope": {
            "metrics": None if ms is None else [m for m in ms if m in metrics],
            "exclude": {b: v for b, v in exclude.items() if v},
        },
        "headline": headline,
        "ask_owner": ask if ask == "top_item" or ask in owners else "top_item",
        "goals": [g for g in body.get("goals") or [] if g in goals],
        "schedule": sched,
        "time": time if re.match(TIME_RE, time) else "06:00",
        "email_format": body.get("email_format")
        if body.get("email_format") in FORMATS
        else "simple",
        # findings the owner added (10o): their patterns, as findings.pattern_of writes them
        "pinned": [
            str(x)
            for x in dict.fromkeys(body.get("pinned") or [])
            if re.match(r"^[0-9a-f]{32}$", str(x))
        ][:20],
    }
    if on is not None:  # Weekly or Monthly only: a daily brief's body keeps its bytes
        out["on"] = on
    return out


def apply_form(tab: str, form: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    """One tab's fields into the draft. Lists come as `getlist` values."""
    d = {**draft, "scope": {**(draft.get("scope") or {})}}
    if tab == "question":
        d["name"] = form.get("name", d.get("name"))
        d["question"] = form.get("question", d.get("question"))
        d["goals"] = list(form.get("goals") or [])
        d["ask_owner"] = form.get("ask_owner") or "top_item"
    elif tab == "scope":
        # each breakdown's values are sent as the ones kept; the rest are left out
        kept: dict[str, list[str]] = form.get("kept") or {}
        options: dict[str, list[str]] = form.get("options") or {}
        d["scope"]["exclude"] = {
            b: [v for v in vals if v not in (kept.get(b) or [])] for b, vals in options.items()
        }
    elif tab == "numbers":
        head = list(d.get("headline") or [])
        if form.get("remove"):
            head = [m for m in head if m != form["remove"]]
        if form.get("add") and form["add"] not in head and len(head) < HEADLINE_MAX:
            head.append(form["add"])
        if form.get("up") and form["up"] in head:
            i = head.index(form["up"])
            if i:
                head[i - 1], head[i] = head[i], head[i - 1]
        d["headline"] = head
    elif tab == "delivery":
        d["schedule"] = form.get("schedule", d.get("schedule"))
        d["on"] = form.get("on", d.get("on"))
        d["time"] = form.get("time", d.get("time"))
        d["email_format"] = form.get("email_format", d.get("email_format"))
    return d


def in_scope_totals(
    spec: dict[str, Any], points: list[Point], body: dict[str, Any]
) -> list[dict[str, Any]]:
    """What the scope keeps of each ₹ monthly headline number, last complete month (4d3)."""
    exclude = (body.get("scope") or {}).get("exclude") or {}
    out = []
    for m in body.get("headline") or []:
        meta = spec.get("metrics", {}).get(m) or {}
        if meta.get("unit") != "inr" or (meta.get("grain") or "month") != "month":
            continue
        tot = sorted(
            (p.period, p.value)
            for p in points
            if p.metric == m
            and p.grain == "month"
            and p.breakdown == ""
            and p.complete
            and p.value is not None
        )
        if not tot:
            continue
        period, total = tot[-1]
        left = Decimal(0)
        has = {p.breakdown for p in points if p.metric == m and p.grain == "month" and p.breakdown}
        for b, vals in exclude.items():
            if b not in has:
                continue
            left += sum(
                (
                    p.value
                    for p in points
                    if p.metric == m
                    and p.grain == "month"
                    and p.breakdown == b
                    and p.breakdown_value in vals
                    and p.period == period
                    and p.value is not None
                ),
                Decimal(0),
            )
            break  # one breakdown's values: adding two would count a value twice
        out.append(
            {
                "metric": m,
                "name": str(meta.get("name") or m),
                "period": period,
                "total": total,
                "kept": total - left,
                "share": (total - left) / total if total else Decimal(0),
            }
        )
    return out


def effect(before: dict[str, Any], after: dict[str, Any]) -> str:
    """'Headline unchanged · 4 items → 3' (4d3)."""
    if not before or not after:
        return ""
    head = "Headline unchanged" if before.get("title") == after.get("title") else "Headline changes"
    a, b = len(before.get("items") or []), len(after.get("items") or [])
    items = f"{a} items" if a == b else f"{a} items → {b}"
    return f"{head} · {items}"
