# SPDX-License-Identifier: Apache-2.0
"""Goals suggested by code (0.28): whole goals built from the owner's parts and the targets
`goal_targets` computes, shared by the goal chat and the nightly question "What are you aiming
for this year?". Pure: no session, no model (the night may use it; non-negotiable 4)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

MAX_OPTIONS = 3


def _headline(spec: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for k in (spec.get("forecast") or {}).get("headline") or []:
        if k not in seen:
            seen.append(k)
    return seen


# words in a number's name that say lower is better: code alone can't tell which way such a
# number should go, so it never suggests a goal for one (the owner can still ask for it in words)
_LOWER_GOOD = ("cost", "expense", "return", "refund", "discount", "write", "loss", "churn", "dso")


def summable(m: dict[str, Any]) -> bool:
    """A year's total means something only for a flow counted in ₹, units or metres (net
    revenue, even worked out from others); days, ratios, % and ₹ per metre are goals on their
    value by the date, and so is a level (a bank balance)."""
    return m.get("kind", "flow") == "flow" and m.get("unit") in ("inr", "count", "metres", None)


def plainly_higher_good(key: str, m: dict[str, Any]) -> bool:
    """A number code may suggest a goal for without a model: a ₹ total whose name doesn't say
    lower is better (net revenue, collections)."""
    words = f"{key} {m.get('name') or ''}".lower()
    return summable(m) and m.get("unit") == "inr" and not any(w in words for w in _LOWER_GOOD)


def goal_key(metric: str, by: dt.date) -> str:
    return f"{metric}_by_{by:%Y_%m_%d}"


def _option(
    n: int,
    spec: dict[str, Any],
    metric: str,
    target: Decimal,
    by: dt.date,
    direction: str,
    aggregate: str,
    note: str,
    replaces: str | None,
) -> dict[str, Any]:
    from othor.ontology.goal_targets import goal_text
    from othor.ontology.types import validate_body

    m = spec["metrics"][metric]
    body = validate_body(
        "goal",
        {
            "name": str(m.get("name") or metric),
            "metric": metric,
            "target": f"{target:f}",
            "direction": direction,
            "by": by.isoformat(),
            "aggregate": aggregate,
        },
    )
    names = {k: str(v.get("name") or k) for k, v in spec.get("metrics", {}).items()}
    return {
        "key": f"o{n}",
        "label": goal_text(body, m.get("unit"), names),
        "note": note,
        "tag": None,
        "change": {"op": "put", "type": "goal", "key": goal_key(metric, by), "body": body},
        "replaces": replaces if replaces and replaces != goal_key(metric, by) else None,
    }


def build_options(
    spec: dict[str, Any],
    points: Any,
    rows: list[dict[str, Any]],
    fy_start_month: int,
    got: dict[str, Any],
    today: dt.date,
    replaces: str | None = None,
    prefer: str | None = None,
) -> list[dict[str, Any]]:
    """Whole goals: the owner's parts as given, the rest suggested by code; the first is the
    card's suggestion (owner, 19 Nov 2026: one suggestion, Apply and Suggest edits). Pure: the
    nightly question and the chat use the same."""
    from othor.engine.detectors.base import SeriesIndex
    from othor.ontology import goal_targets

    fy_end = goal_targets.fiscal_year_end(today, int(fy_start_month or 4))
    by = got.get("by") or fy_end
    by_note = "" if got.get("by") else "by the end of this financial year"
    all_m = spec.get("metrics", {})
    metrics = (
        [got["metric"]]
        if got.get("metric")
        else [k for k in _headline(spec) if plainly_higher_good(k, all_m.get(k) or {})][
            :MAX_OPTIONS
        ]
    )
    idx = SeriesIndex(points)
    out: list[dict[str, Any]] = []
    for metric in metrics:
        m = spec.get("metrics", {}).get(metric)
        if m is None:
            continue
        direction = got.get("direction") or "at_least"
        # a worked-out number or a level is never summed over a year, whoever asked
        aggregate = (got.get("aggregate") or "fiscal_year_total") if summable(m) else "value"
        if got.get("target") is not None:
            picks = [(got["target"], "")]
        else:
            chips = goal_targets.candidates(
                idx, rows, metric, aggregate=aggregate, direction=direction, by=by
            )
            order = {"heading": 0, "stretch": 1, "last_year": 2, "file": 3}
            # the owner asked for one of the computed targets in words ("the stretch")
            chips.sort(key=lambda c: (c.kind != prefer, order.get(c.kind, 9)))
            picks = [(c.value, f"target: {c.label.lower()}") for c in chips]
            if not got.get("metric"):
                picks = picks[:1]  # one goal per suggested number
        for target, why in picks:
            note = " · ".join(x for x in (why, by_note) if x)
            out.append(
                _option(
                    len(out) + 1, spec, metric, target, by, direction, aggregate, note, replaces
                )
            )
            if len(out) >= MAX_OPTIONS:
                return out
    return out


# --- 0.28 (M28.2): "What are you aiming for this year?" while there's no goal -----------------

QUESTION = "What are you aiming for this year?"
QUESTION_WHY = "A goal shows every night how likely you are to reach it, in your briefs too."
KIND = "goal_missing"


def question_candidate(
    spec: dict[str, Any],
    points: Any,
    rows: list[dict[str, Any]],
    fy_start_month: int,
    today: dt.date,
) -> dict[str, Any] | None:
    """Raised while the company has no goal: its suggestion is a whole goal on the briefs' lead
    number, the target computed by code (three to choose from by words); no model needed."""
    from othor.ontology import goal_targets
    from othor.ontology.questions import _fingerprint

    if (spec.get("forecast") or {}).get("goals"):
        return None
    ms = spec.get("metrics", {})
    lead = [k for k in _headline(spec) if plainly_higher_good(k, ms.get(k) or {})][:1]
    if not lead:
        return None
    cards = build_options(spec, points, rows, fy_start_month, {"metric": lead[0]}, today)
    if not cards:
        return None
    fy_end = goal_targets.fiscal_year_end(today, int(fy_start_month or 4))
    return {
        "fingerprint": _fingerprint(KIND, fy_end.isoformat()),
        "text": QUESTION,
        "why": QUESTION_WHY,
        "unblocks": {},
        "origin": {"rule": KIND},
        "quick_replies": [],
        "kind": KIND,
        "subject": {"cards": cards},
        "rank": "3|",
    }
