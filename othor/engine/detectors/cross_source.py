# SPDX-License-Identifier: Apache-2.0
"""Cross-source contradiction (docs/04 §3, detector 10): two systems, the same thing, disagree.
Always Needs confirmation; never in a brief; it raises a Home question. Needs a `reconcile` rule.

Two kinds of check, from the reconciliation probes:
- **keys** (P10): records on the left (dispatched orders, older than the rule's lag) with no
  matching key on the right (an invoice for that order). Money: the unmatched value.
- **periods** (P9, P16): the same measure from two sources, period by period (the last two
  complete periods: a period still running is behind in one system by nature). It fires when
  they differ by more than the tolerance (spec, 0.5%) and the difference is at least the
  materiality floor. Money: the difference.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from othor.engine.detectors.base import Context, Coverage, Result, Spec, dec, pval, register

NAME = "cross_source"
VERSION = "1"
KEY_REASONS = (
    "late",
    "cancelled after the event, and the first system wasn't updated",
    "the two systems mean different things",
)


def _result(ctx: Context, pr: dict[str, Any], kind: str) -> Result:
    return Result(
        detector=NAME,
        version=VERSION,
        series=[f"reconcile|{pr['key']}|{kind}"],
        metric=pr.get("metric") or pr["key"],
        breakdown="",
        breakdown_value=None,
        window=(ctx.run_date, ctx.run_date),
        statistic=None,
        p_value=None,
        effect=None,
        strong=True,
        direction="none",
        money=None,
        money_basis="",
        state_hint="needs_confirmation",
        family=f"reconcile:{pr['key']}",
    )


def run(ctx: Context) -> tuple[list[Result], Coverage]:
    probes = ctx.probes.get("reconcile") or []
    if not probes:
        return [], Coverage(NAME, "could_not_run", 0, 0, "checks", needs="reconcile")
    tol = float(ctx.config(NAME).get("tolerance", "0.005"))
    floor = float(ctx.spec.get("detectors", {}).get("materiality_inr", "500000"))
    out: list[Result] = []
    for pr in probes:
        if pr["kind"] == "keys":
            out.append(_keys(ctx, pr))
        else:
            out.append(_periods(ctx, pr, tol, floor))
    return out, Coverage(NAME, "ran", len(out), len(out), "checks")


def _keys(ctx: Context, pr: dict[str, Any]) -> Result:
    r = _result(ctx, pr, "keys")
    unmatched = sorted(pr.get("unmatched", []), key=lambda u: (-float(u[2] or 0), str(u[0])))
    as_of = ctx.run_date - dt.timedelta(days=1)
    r.window = (as_of - dt.timedelta(days=int(pr.get("lag_days", 30))), as_of)
    value = sum(float(u[2] or 0) for u in unmatched)
    r.statistic, r.effect = dec(len(unmatched)), dec(len(unmatched))
    r.p_value = pval(0.0 if unmatched else 1.0)
    r.money = dec(value, places=Decimal("0.01")) if unmatched else None
    r.money_basis = (
        f"value of {pr.get('left_label', 'left')} with no match in {pr.get('right_label', 'right')}"
    )
    r.slots = {
        "count": len(unmatched),
        "lag_days": int(pr.get("lag_days", 30)),
        "left": pr.get("left_label", ""),
        "right": pr.get("right_label", ""),
        "checked": int(pr.get("left_count", 0)),
        "noun": pr.get("noun", "records"),
        "left_source": pr.get("left_source", ""),
        "right_source": pr.get("right_source", ""),
        "right_table": pr.get("right_table", ""),
    }
    r.evidence = [
        {
            "key": str(u[0]),
            "date": u[1],
            "value": dec(float(u[2] or 0), places=Decimal("0.01")),
            "days": (as_of - dt.date.fromisoformat(u[1])).days if u[1] else None,
            **({"entity": u[3]} if len(u) > 3 else {}),
        }
        for u in unmatched[:50]
    ]
    r.entities = sorted({str(u[3]) for u in unmatched if len(u) > 3 and u[3]})[:10]
    r.reasons = [
        {"key": f"r{i + 1}", "text": t, "ruled_out": False, "check": None}
        for i, t in enumerate(KEY_REASONS)
    ]
    if not unmatched:
        r.reason = "every record matched"
    return r


def _periods(ctx: Context, pr: dict[str, Any], tol: float, floor: float) -> Result:
    r = _result(ctx, pr, "periods")
    rows = sorted(pr.get("rows", []))[-2:]  # the last two complete periods
    worst: tuple[float, Any] | None = None
    diffs = []
    for period, a, b in rows:
        if a is None or b is None:
            continue
        a, b = float(a), float(b)
        d = a - b
        rel = abs(d) / max(abs(a), abs(b), 1e-9)
        diffs.append(
            {"period": period, "a": dec(a), "b": dec(b), "difference": dec(d), "share": dec(rel)}
        )
        if rel > tol and abs(d) >= floor and (worst is None or abs(d) > abs(worst[0])):
            worst = (d, period)
    r.evidence = diffs
    grain = pr.get("grain") or "month"
    r.slots = {
        "a": pr.get("a_label", ""),
        "b": pr.get("b_label", ""),
        "tolerance": dec(tol),
        "grain": grain,
    }
    if worst is None:
        r.p_value, r.statistic = pval(1.0), dec(0)
        r.reason = "the two sources agree within tolerance"
        return r
    d, period = worst
    r.window = (dt.date.fromisoformat(period), ctx.run_date - dt.timedelta(days=1))
    r.p_value, r.statistic, r.effect = pval(0.0), dec(d), dec(d)
    r.direction = "up" if d > 0 else "down"
    r.money = dec(abs(d), places=Decimal("0.01"))
    r.money_basis = (
        f"{pr.get('a_label', 'a')} minus {pr.get('b_label', 'b')} "
        f"for the last complete {grain} (from {period})"
    )
    r.slots["period"] = period
    r.reasons = [
        {
            "key": "late",
            "text": "one system hasn't posted it yet",
            "ruled_out": False,
            "check": None,
        },
        {
            "key": "rule",
            "text": "the two follow different rules",
            "ruled_out": False,
            "check": None,
        },
        {
            "key": "error",
            "text": "an entry is wrong in one of them",
            "ruled_out": False,
            "check": None,
        },
    ]
    return r


register(Spec(NAME, VERSION, "Cross-source contradiction", run, needs=("reconcile",)))
