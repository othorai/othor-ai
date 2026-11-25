# SPDX-License-Identifier: Apache-2.0
"""What to do (Morning Brief 2.5, #110; docs/plans/what-to-do.md § A): a brief's next steps,
picked and worded by code from what the issue already holds, stored in the issue body so a
replay gives the same bytes (rule 2).

Sources, in order: an off-track goal (the gap to its target), the brief's items in their rank
(each finding once; a finding whose named entities all appear in a higher action joins it); on a
quiet night (no items), the largest finding still open for a week (one at most); then a breached
watch on one of the brief's numbers that no action already covers (a watch following an item's
number folds into that item: 0.33.1). At most `MAX` per brief.

Every figure in a sentence is copied from the stored item, goal or watch (rule 1): a finding's
own title, money and names; a goal's target and projection, and the gap between them (computed
here, by code). The template sentences are the words when no model writes (rule 4); the brief's
rewrite step may smooth them, under the numbers guard (`rewrite.py`)."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from othor.briefs import scope, values
from othor.core import fmt
from othor.engine.watches import can_draft, covers

MAX = 3
EXPLAINED = Decimal("0.70")  # the drill's share that names the accounts (title.EXPLAINED)

# one plain lead-in per check: what to do about a finding of that kind; the finding's own title
# follows it, so every figure in the sentence is the finding's (rule 1)
LEADS: dict[str, str] = {
    "changepoint": "Find out what changed",
    "rising_variance": "Find out why it became uneven",
    "net_hiding_gross": "Find out what is behind the fall",
    "missing_records": "Call them to find out why",
    "concentration": "Win orders beyond your biggest customers",
    "cross_source": "Reconcile the two sources",
    "forecast_breach": "Plan for it now",
    "broken_relationship": "Check why these two stopped moving together",
    "lead_lag": "Watch the earlier number: it moves first",
    "bimodal_average": "Look at the two groups separately",
    "process_artefact": "Check how it is recorded",
}
GENERIC = "Look into it"
# a finding's entities are names that can join a higher action (customers, regions), not two
# numbers of a pair
NO_FOLD = {"broken_relationship", "lead_lag"}


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _accounts(f: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(the accounts behind a fall, those on a known pause) from the finding's drill slot."""
    d = (f.get("slots") or {}).get("drill") or {}
    if Decimal(str(d.get("share", 0) or 0)) < EXPLAINED:
        return [], []
    acc = d.get("accounts") or []
    return [a["name"] for a in acc if not a.get("paused")], [
        a["name"] for a in acc if a.get("paused")
    ]


def _proper(f: dict[str, Any]) -> set[str]:
    """The names a title may start with that keep their capital after a colon: the finding's
    entities, its part, the regions or groups its slots name."""
    sl = f.get("slots") or {}
    out = {str(x) for x in f.get("entities") or []}
    if f.get("breakdown_value"):
        out.add(str(f["breakdown_value"]))
    for k in ("fall", "rise"):
        if isinstance(sl.get(k), dict) and sl[k].get("name"):
            out.add(str(sl[k]["name"]))
    return out


def _low(text: str, keep: set[str]) -> str:
    """A title after a colon, mid-sentence (0.33.1: "…customers: Four customers…"): its first
    letter lower-cased, unless it opens with a name, an abbreviation (DSO) or a number."""
    if not text or not text[0].isupper():
        return text
    if any(text.startswith(n) for n in keep if n):
        return text
    return fmt.lower_first(text)


# the 0.33.1 sentences (the 100-night run): a finding's own title reworded into an action, every
# figure still the title's own (rule 1); each falls back to "<lead>: <title>" when its title
# isn't in the shape the check writes (sweep_text)
_STEP = re.compile(r"^(?P<what>.+?) (?P<verb>stepped (?:up|down)) in (?P<when>the week of .+)$")
_SPREAD = re.compile(
    r"^(?P<what>.+?) spread at (?P<where>.+?) (?P<how>\w+) from (?P<date>\d{1,2} \w+)(?P<rest>.*)$"
)
_SILENT = re.compile(
    # the entity's own plural from the Business Model ("partner banks"), else the usual nouns
    r"^(?P<n>[\d,]+) (?P<noun>customers|accounts|suppliers|partners|[a-z][a-z ]*?(?= still ))"
    r" (?P<mid>.+?) "
    r"(?P<verb>haven’t|have not|stopped|aren’t)(?P<tail>.*)$"
)
_FALL = re.compile(r"^(?P<a>.+?) fell (?P<m>[^;]+); (?P<b>.+?)’s rise hid it$")


def finding_text(f: dict[str, Any]) -> str:
    """The template sentence for one finding: what to do about it, in its own words."""
    title = str((f.get("text") or {}).get("title") or "").rstrip(".")
    det = str(f.get("detector") or "")
    keep = _proper(f)
    low = _low(title, keep)
    lead = LEADS.get(det, GENERIC)
    if det == "net_hiding_gross":
        open_, paused = _accounts(f)
        m = _FALL.match(title)
        if open_ and m:
            out = (
                f"Call {_and(open_)}: they carry most of {m['a']}’s {m['m']} fall, which "
                f"{m['b']}’s rise hid"
            )
            if paused:
                out += (
                    f" ({_and(paused)} is on a known pause)"
                    if len(paused) == 1
                    else f" ({_and(paused)} are on known pauses)"
                )
            return out + "."
    if det == "missing_records":
        ents = list(f.get("entities") or [])
        if len(ents) == 1:
            rest = title[len(ents[0]) :].strip() if title.startswith(ents[0]) else ""
            if rest:
                return f"Call {ents[0]} to find out why they {rest}."
            return f"Call {ents[0]} to find out why: {low}."
        m = _SILENT.match(title)
        if m:
            return (
                f"Have someone call the {m['n']} {m['noun']} {m['mid']} who {m['verb']}{m['tail']}."
            )
        return f"Have someone call these customers: {low}."
    if det == "changepoint":
        m = _STEP.match(title)
        if m:
            return (
                f"Find out what changed in {m['when']}, when {_low(m['what'], keep)} {m['verb']}."
            )
    if det == "rising_variance":
        m = _SPREAD.match(title)
        if m:
            return (
                f"Ask what changed at {m['where']} from {m['date']}: the spread of "
                f"{_low(m['what'], keep)} {m['how']}{m['rest']}."
            )
    return f"{lead}: {low}."


def _names(f: dict[str, Any]) -> set[str]:
    open_, paused = _accounts(f)
    return set(f.get("entities") or []) | set(open_) | set(paused)


def _who(f: dict[str, Any], ask: dict[str, Any] | None, top: bool) -> dict[str, str] | None:
    if top and ask and ask.get("to"):
        return {"name": str(ask["to"]), "role": str(ask.get("role") or "")}
    q = (f.get("text") or {}).get("question") or {}
    to = (q.get("to") or [None])[0] if isinstance(q, dict) else None
    return {"name": str(to["name"]), "role": str(to.get("role") or "")} if to else None


def _goal_action(spec: dict[str, Any], g: dict[str, Any], line: dict[str, Any]) -> dict[str, Any]:
    m = spec.get("metrics", {}).get(g["metric"], {})
    unit = m.get("unit")
    name = str(m.get("name") or g["metric"])
    low = fmt.lower_first(name)
    target, proj = Decimal(str(g["target"])), Decimal(str(g["projection"]))
    by = fmt.day_month(dt.date.fromisoformat(str(g["by"])))
    up = g.get("direction", "at_least") == "at_least"
    # the gap between the two figures as shown (₹60 Cr − ₹56.7 Cr = ₹3.3 Cr), computed here, by
    # code (rule 1); money in crore to one decimal, as `values.rounded` shows a goal
    step = Decimal("1000000") if unit == "inr" else Decimal("0.1")
    shown_t = (target / step).quantize(Decimal(1)) * step
    shown_p = (proj / step).quantize(Decimal(1)) * step
    gap = values.rounded(unit, abs(shown_t - shown_p))
    t, p = values.rounded(unit, target), values.rounded(unit, proj)
    text = (
        f"Find {gap} more {low} by {by}: at today’s pace it ends at {p} against the {t} goal."
        if up
        else f"Bring {low} down by {gap} by {by}: at today’s pace it ends at {p} against the "
        f"{t} goal."
    )
    return {
        "kind": "goal",
        "key": f"goal:{g['key']}",
        "fingerprints": [],
        "metric": g["metric"],
        "text": text,
        "why": [{"title": str(line["text"]), "goal": g["key"]}],
        "status": "Off track",
        "who": None,
        "by": f"By {by}",
        "money": gap,
        "money_note": "short of the goal" if up else "over the goal",
    }


def _ind(v: Any, kind: str | None, unit: str | None, decimals: int | None = None) -> str:
    """A watch's figure in its number's unit (as `watches.label` shows it): days in days, money
    in ₹; `decimals` adds precision when a value and its limit would round to the same text."""
    d = Decimal(str(v))
    if unit == "days" and kind in ("value", "spread"):
        return f"{fmt.num(d, decimals=1 if decimals is None else decimals)} days"
    if unit == "inr" and kind == "value":
        return fmt.inr(d) if decimals is None else fmt.inr(d, decimals=decimals)
    if decimals is not None and kind == "value" and abs(d) < 1:
        return fmt.pct(d, decimals=decimals)
    if decimals is not None and kind not in ("vs_average", "finding_slot"):
        return fmt.num(d, decimals=decimals)
    return values.indicator_text(d, kind)


def _watch_figures(w: dict[str, Any], unit: str | None) -> tuple[str, str]:
    """(today, the limit) as shown; never the same text for a watch past its limit (0.33.1:
    "12%, above its 12% limit"): one more decimal at a time until they differ."""
    kind = (w.get("indicator") or {}).get("kind")
    now, thr = _ind(w["value"], kind, unit), _ind(w["threshold"], kind, unit)
    base = 1 if unit == "days" else 0
    for extra in (1, 2, 3):
        if _shown(now) != _shown(thr):
            break
        now = _ind(w["value"], kind, unit, base + extra)
    return now, thr


def _shown(text: str) -> Decimal | None:
    """The number a figure shows ("12.0%" and "12%" are the same number)."""
    digits = re.sub(r"[^0-9.]", "", text.replace(",", ""))
    try:
        return Decimal(digits) if digits else None
    except ArithmeticError:
        return None


def _lower_first(name: str) -> str:
    return fmt.lower_first(name)  # DSO and TiO2 stay as written


def _watch_name(label: str) -> str:
    """A watch's label as a phrase mid-sentence, its first part only (before " · "), first letter
    lower-cased unless it's an abbreviation (DSO). A comma is read out (24 Nov 2026, the 100-night
    test): a capitalised part after it is a place or a name ("Dispatch time spread, Coimbatore" →
    "dispatch time spread for Coimbatore"), a lower-case one is a measure ("Receivables over 60
    days, share" → "the share of receivables over 60 days")."""
    name = label.split(" · ")[0].strip()
    head, sep, tail = name.partition(", ")
    if not sep or not tail.strip():
        return _lower_first(name)
    if tail[:1].isupper():
        return f"{_lower_first(head)} for {tail}"
    return f"the {tail} of {_lower_first(head)}"


def _watch_action(spec: dict[str, Any], w: dict[str, Any]) -> dict[str, Any]:
    metric = (w.get("indicator") or {}).get("metric")
    unit = (spec.get("metrics", {}).get(metric or "") or {}).get("unit")
    now, thr = _watch_figures(w, unit)
    owner = str(w.get("owner_name") or "")
    word = "above" if w.get("direction") != "below" else "below"
    name = _watch_name(str(w["label"]))
    lead = f"Ask {owner} why" if owner else "Look into why"
    return {
        "kind": "watch",
        "key": f"watch:{w['id']}",
        "fingerprints": [str(w["from_finding"])] if w.get("from_finding") else [],
        "metric": metric,
        "text": f"{lead} {name} reads {now}, {word} your limit of {thr}.",
        "why": [{"title": str(w["label"]), "watch": str(w["id"])}],
        "status": f"{word.capitalize()} the limit",
        # the sentence names who to ask; the row doesn't say it twice (0.33.1)
        "who": None,
        "by": None,
        "money": None,
        "money_note": None,
    }


def _finding_action(
    it: dict[str, Any], f: dict[str, Any], ask: dict[str, Any] | None, top: bool, status: str
) -> dict[str, Any]:
    c = it.get("consequence") or {}
    return {
        "kind": "finding",
        "key": "finding:" + it["fingerprint"],
        "fingerprints": [it["fingerprint"]],
        "metric": it.get("metric") or f.get("metric"),
        "detector": it.get("detector") or f.get("detector"),
        # what a Watch button needs, without the night's findings (plan § D)
        "watch": {
            "can": can_draft(f),
            "entities": list(f.get("entities") or []),
            "breakdown_value": f.get("breakdown_value"),
        },
        "text": finding_text(f),
        "why": [{"title": it.get("title", ""), "fingerprint": it["fingerprint"]}],
        "status": status,
        "who": _who(f, ask, top),
        # a dated consequence only ("November close in 29 days"), never a general line
        "by": str(c.get("text") or "") or None if c.get("date") else None,
        "money": it.get("money") or None,
        "money_note": None,
    }


def build(
    spec: dict[str, Any],
    sc: dict[str, Any],
    *,
    items: list[dict[str, Any]],
    findings: dict[str, dict[str, Any]],
    goals: list[dict[str, Any]],
    goal_lines: list[dict[str, Any]],
    watches: list[dict[str, Any]],
    ask: dict[str, Any] | None,
    still_open: list[dict[str, Any]] | None = None,
    watch_ok: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    """`still_open`: the brief's findings unchanged for a week, largest first (assemble's
    still_open); `watch_ok`: whether a watch is on one of this brief's numbers (`scope.
    watch_in_brief`; the scope's metrics only when not given)."""
    ok = watch_ok or (lambda w: scope.watch_in(spec, sc, w))
    out: list[dict[str, Any]] = []
    # 1 · an off-track goal: the gap to its target
    by_key = {g["key"]: g for g in goals}
    for line in goal_lines:
        g = by_key.get(line["key"])
        if g is not None and line.get("tone") == "off" and g.get("projection") is not None:
            out.append(_goal_action(spec, g, line))
    # 2 · the items, in the brief's rank; a finding named in a higher action joins it
    names: list[set[str]] = []
    for n, it in enumerate(items):
        f = findings.get(it["fingerprint"])
        if f is None:
            continue
        mine = set(f.get("entities") or [])
        host = next(
            (
                a
                for a, seen in zip([a for a in out if a["kind"] == "finding"], names, strict=False)
                if mine and f.get("detector") not in NO_FOLD and mine <= seen
            ),
            None,
        )
        if host is not None:
            host["fingerprints"].append(it["fingerprint"])
            host["why"].append({"title": it.get("title", ""), "fingerprint": it["fingerprint"]})
            continue
        out.append(_finding_action(it, f, ask, n == 0, str(it.get("change") or "")))
        names.append(_names(f))
    # 3 · a quiet night (no items): the largest finding still open for a week, one at most
    # (0.33.1: the title named what was still open, What to do offered only the goal)
    if not items:
        for r in still_open or []:
            f = findings.get(r["fingerprint"])
            if f is None:
                continue
            it = {"fingerprint": r["fingerprint"], "title": r.get("title", ""), "money": None}
            out.append(
                _finding_action(
                    it, f, None, False, f"Still open · {fmt.num(r.get('days') or 0)} days"
                )
            )
            break
    # 4 · a breached watch on one of the brief's numbers that no action covers; a watch that
    # follows an action's finding folds into it (0.33.1)
    acted = [findings[fp] for a in out for fp in a["fingerprints"] if fp in findings]
    metrics = {a.get("metric") for a in out if a.get("metric")}
    for w in watches:
        if w.get("status") != "breached" or not ok(w):
            continue
        ind = w.get("indicator") or {}
        if ind.get("metric") in metrics and not ind.get("breakdown_value"):
            continue
        if any(covers(ind, w.get("from_finding"), f) for f in acted):
            continue
        out.append(_watch_action(spec, w))
    return out[:MAX]
