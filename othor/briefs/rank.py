# SPDX-License-Identifier: Apache-2.0
"""Ranking (docs/04 §8), one function: money at stake × urgency × state weight.

Urgency from the consequence date (1.0 within 30 days, 0.6 within 90, 0.3 beyond or no date);
state weight Measured 1.0, Assessed High 1.0, Medium 0.7, Not determinable 0.8. Scores within
10% are a tie (decided 6 Nov 2026): Measured over Assessed over Not determinable, then the
earlier consequence date, then the larger money. Yesterday's items keep their place unless
their score moved by more than 10%. Needs confirmation never enters; an item from a stale
source is never ranked first (4h1 C). Up to 4 items. Decimal throughout: the order is part of
the run.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from othor.briefs import consequence

ITEMS = 4
STATE = {
    ("measured", None): Decimal("1.0"),
    ("assessed", "high"): Decimal("1.0"),
    ("assessed", "medium"): Decimal("0.7"),
    ("assessed", "low"): Decimal("0.5"),
    ("not_determinable", None): Decimal("0.8"),
}


def urgency(run_date: dt.date, when: dt.date | None) -> Decimal:
    if when is None:
        return Decimal("0.3")
    days = (when - run_date).days
    if days <= 30:
        return Decimal("1.0")
    if days <= 90:
        return Decimal("0.6")
    return Decimal("0.3")


def state_weight(f: dict[str, Any]) -> Decimal:
    conf = f.get("confidence") if f.get("state") == "assessed" else None
    return STATE.get((str(f.get("state")), conf), Decimal("0.5"))


def score(f: dict[str, Any], spec: dict[str, Any], run_date: dt.date) -> Decimal:
    c = consequence.of(f, spec, run_date)
    when = dt.date.fromisoformat(c["date"]) if c["date"] else None
    money = Decimal(str(f.get("money") or 0))
    return money * urgency(run_date, when) * state_weight(f)


def eligible(f: dict[str, Any]) -> bool:
    return f.get("status") == "open" and f.get("state") != "needs_confirmation"


TIE = Decimal("0.10")  # decided 6 Nov 2026: scores within 10% are a tie
STATE_ORDER = {"measured": 0, "assessed": 1, "not_determinable": 2}


def rank(
    findings: list[dict[str, Any]],
    spec: dict[str, Any],
    run_date: dt.date,
    n: int = ITEMS,
    previous: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Up to `n` items, in order:

    1. by score (money × urgency × state weight), newer first, then the fingerprint;
    2. scores within 10% of the top of their group are a tie, broken by the state (Measured,
       Assessed, Not determinable), then the earlier consequence date, then the larger money;
    3. an item in yesterday's brief keeps its place unless its score moved by more than 10%;
    4. an item from a stale source is never first.
    """
    scores = {f["fingerprint"]: score(f, spec, run_date) for f in findings if eligible(f)}

    def key(f: dict[str, Any]) -> tuple[Any, ...]:
        first = dt.date.fromisoformat(f["first_seen"]["date"])
        return (-scores[f["fingerprint"]], -first.toordinal(), f["fingerprint"])

    ordered = sorted((f for f in findings if eligible(f)), key=key)
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(ordered):
        top = scores[ordered[i]["fingerprint"]]
        j = i + 1
        while j < len(ordered) and scores[ordered[j]["fingerprint"]] >= top * (1 - TIE):
            j += 1
        out += sorted(ordered[i:j], key=lambda f: _tie(f, spec, run_date, ordered.index(f)))
        i = j
    out = _stable(out, scores, previous or [], n)
    if out and out[0].get("stale"):
        fresh = next((k for k, f in enumerate(out) if not f.get("stale")), None)
        if fresh is not None:
            out.insert(0, out.pop(fresh))
    return out[:n]


def _tie(f: dict[str, Any], spec: dict[str, Any], run_date: dt.date, at: int) -> tuple[Any, ...]:
    c = consequence.of(f, spec, run_date)["date"]
    return (
        STATE_ORDER.get(str(f.get("state")), 3),
        c or "9999-12-31",
        -Decimal(str(f.get("money") or 0)),
        at,
    )


def _stable(
    ordered: list[dict[str, Any]],
    scores: dict[str, Decimal],
    previous: list[dict[str, Any]],
    n: int,
) -> list[dict[str, Any]]:
    """Yesterday's items keep their rank when their score moved by 10% or less."""
    keep: dict[int, dict[str, Any]] = {}
    by_fp = {p["fingerprint"]: p for p in previous}
    for f in ordered:
        p = by_fp.get(f["fingerprint"])
        if p is None or not p.get("score") or int(p["rank"]) > n:
            continue
        before = Decimal(str(p["score"]))
        if before and abs(scores[f["fingerprint"]] - before) <= abs(before) * TIE:
            slot = int(p["rank"]) - 1
            # a place past tonight's shorter list doesn't exist: the item is ranked afresh
            # (13 Nov 2026: reserving it left too few items for the places before it)
            if slot < len(ordered) and slot not in keep:
                keep[slot] = f
    rest = [f for f in ordered if f not in keep.values()]
    out: list[dict[str, Any]] = []
    for slot in range(len(ordered)):
        out.append(keep[slot] if slot in keep else rest.pop(0))
    return out
