# SPDX-License-Identifier: Apache-2.0
"""Net hiding gross (docs/04 §3, detector 1): the headline is flat while offsetting moves sit
underneath it.

For each ₹ flow metric and each low-cardinality breakdown, the last complete month against the
one before: contributions c_i = v_i(t) − v_i(t − 1), net = Σ c_i. It fires when the gross offsets
are at least 3 × |net| (spec `offset_ratio`) and both the largest fall and the largest rise are
unusual for their own breakdown value: each is compared with that value's own month-on-month
changes over the past 24 months (z), and p is the larger of the two one-sided p-values (both
moves must be real).

Money: the largest fall.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
from scipy import stats

from othor.engine.detectors import common
from othor.engine.detectors.base import (
    PAISA,
    Context,
    Coverage,
    Result,
    Series,
    Spec,
    dec,
    pval,
    register,
)

NAME = "net_hiding_gross"
VERSION = "1"
HISTORY = 24
STRONG_Z = 4.0


def run(ctx: Context) -> tuple[list[Result], Coverage]:
    cfg = ctx.config(NAME)
    ratio = common.config_num(cfg, "offset_ratio", 3)
    min_periods = int(cfg.get("min_periods", 2))
    results: list[Result] = []
    for metric in ctx.active_metrics():
        if common.unit(ctx, metric) != "inr" or common.is_snapshot(ctx, metric):
            continue
        for b in common.low_card_breakdowns(ctx, metric, "month"):
            parts = ctx.series.values_of(metric, "month", b)
            if len(parts) < 2:
                continue
            r = _test(ctx, metric, b, parts, ratio, min_periods)
            if r is not None:
                results.append(r)
    eligible = sum(1 for r in results if r.eligible)
    status = (
        "ran"
        if results and eligible == len(results)
        else ("partly" if eligible else "could_not_run")
    )
    return results, Coverage(NAME, status, eligible, len(results), "breakdowns")


def _test(
    ctx: Context, metric: str, b: str, parts: list[Series], ratio: float, min_periods: int
) -> Result:
    periods = sorted({p for s in parts for p in s.periods})
    base = Result(
        detector=NAME,
        version=VERSION,
        series=[f"{metric}|month|{b}|"],
        metric=metric,
        breakdown=b,
        breakdown_value=None,
        window=(periods[0], periods[-1]) if periods else (ctx.run_date, ctx.run_date),
        statistic=None,
        p_value=None,
        effect=None,
        strong=False,
        direction="none",
        money=None,
        money_basis="",
        family=common.family(ctx, metric),
    )
    if len(periods) < max(min_periods, 3):
        base.eligible, base.reason = False, "insufficient history"
        return base
    t, t0 = periods[-1], periods[-2]
    contrib: dict[str, float] = {}
    z: dict[str, float] = {}
    for s in parts:
        v = dict(zip(s.periods, s.values, strict=True))
        a, b0 = v.get(t, np.nan), v.get(t0, np.nan)
        if not (np.isfinite(a) and np.isfinite(b0)):
            continue
        name = s.breakdown_value or ""
        contrib[name] = a - b0
        hist = np.diff([v[p] for p in periods[-HISTORY - 2 : -1] if p in v and np.isfinite(v[p])])
        sd = float(np.std(hist, ddof=1)) if len(hist) >= 6 else np.nan
        z[name] = (a - b0) / sd if sd and np.isfinite(sd) and sd > 0 else 0.0
    if len(contrib) < 2:
        base.eligible, base.reason = False, "insufficient history"
        return base
    net = sum(contrib.values())
    gross = sum(abs(c) for c in contrib.values())
    offsets = gross - abs(net)
    fall = min(contrib, key=lambda k: (contrib[k], k))
    rise = max(contrib, key=lambda k: (contrib[k], k))
    p_fall = float(stats.norm.cdf(z[fall])) if contrib[fall] < 0 else 1.0
    p_rise = float(stats.norm.sf(z[rise])) if contrib[rise] > 0 else 1.0
    hides = abs(net) > 0 and offsets >= ratio * abs(net)
    p = max(p_fall, p_rise) if hides else 1.0
    # the change covers both periods, to the end of the current one (Sep 1 – Oct 31)
    base.window = (t0, next_month(t) - dt.timedelta(days=1))
    base.statistic = dec(offsets / abs(net) if net else 0.0)
    base.p_value = pval(p)
    base.effect = dec(min(abs(z[fall]), abs(z[rise])))
    base.strong = min(abs(z[fall]), abs(z[rise])) >= STRONG_Z
    base.direction = "down" if net < 0 else "up"
    base.entities = [fall, rise]
    base.money = dec(abs(contrib[fall]), places=PAISA) if contrib[fall] < 0 else None
    base.money_basis = f"the fall in {fall}, {t0.isoformat()[:7]} to {t.isoformat()[:7]}"
    base.params = {"offset_ratio": str(ratio), "history_months": HISTORY}
    base.slots = {
        "period": t.isoformat(),
        "previous": t0.isoformat(),
        "net": dec(net),
        "gross": dec(gross),
        "fall": {"name": fall, "change": dec(contrib[fall]), "z": dec(z[fall])},
        "rise": {"name": rise, "change": dec(contrib[rise]), "z": dec(z[rise])},
        "contributions": {k: dec(v) for k, v in sorted(contrib.items())},
    }
    base.evidence = [
        {"breakdown_value": k, "change": dec(v)}
        for k, v in sorted(contrib.items(), key=lambda kv: kv[1])
    ]
    if contrib[fall] < 0:
        drill = _drill(ctx, metric, b, fall, t0, t, contrib[fall])
        if drill is not None:
            base.slots["drill"] = drill
    if not hides:
        base.reason = "the headline moved with its parts"
    return base


DRILL_SHARE = 0.70  # the fewest accounts that carry this much of the fall, up to DRILL_MAX
DRILL_MAX = 5
DRILL_ROWS = 50


def _drill(
    ctx: Context, metric: str, b: str, value: str, t0: dt.date, t: dt.date, fall: float
) -> dict[str, Any] | None:
    """The falling breakdown value by entity (decided 5 Nov 2026): each account's change from
    the month before, from the metric's own entity series and the entity's breakdown value
    (the customer probe's attributes). Aggregates only, at most 50 rows. The top accounts are
    the fewest (up to 5) that carry 70% of the fall; their last invoice dates come from the
    entity's daily activity."""
    for key, e in sorted((ctx.probes.get("entities") or {}).items()):
        where = (e.get("attributes") or {}).get(b)
        if not where:
            continue
        members = {n for n, v in where.items() if v == value}
        rows = []
        for s in ctx.series.values_of(metric, "month", key):
            if s.breakdown_value not in members:
                continue
            v = dict(zip(s.periods, s.values, strict=True))
            a, z = v.get(t, 0.0), v.get(t0, 0.0)
            a = 0.0 if a != a else float(a)
            z = 0.0 if z != z else float(z)
            if a - z:
                rows.append((a - z, str(s.breakdown_value)))
        rows.sort()
        falls = [(c, n) for c, n in rows if c < 0]
        if not falls:
            return None
        top: list[tuple[float, str]] = []
        for c, n in falls[:DRILL_MAX]:
            top.append((c, n))
            if sum(x for x, _ in top) <= DRILL_SHARE * fall:
                break
        if sum(x for x, _ in top) > DRILL_SHARE * fall:
            top = falls[: min(3, len(falls))]
        last: dict[str, str] = {}
        for n, day, _amount in e.get("events") or []:
            if (
                n in {x for _, x in top}
                and day <= (next_month(t) - dt.timedelta(days=1)).isoformat()
            ):
                last[n] = max(last.get(n, ""), day)
        paused = _pauses(ctx, [x for _, x in top], t)
        return {
            "breakdown": b,
            "value": value,
            "entity": key,
            "fall": dec(fall),
            "share": dec(sum(x for x, _ in top) / fall),
            "to": (next_month(t) - dt.timedelta(days=1)).isoformat(),
            "accounts": [
                {
                    "name": n,
                    "change": dec(c),
                    "last": last.get(n),
                    **({"paused": paused[n]} if n in paused else {}),
                }
                for c, n in top
            ],
            "rows": [{"name": n, "change": dec(c)} for c, n in rows[:DRILL_ROWS]],
        }
    return None


def _pauses(ctx: Context, names: list[str], t: dt.date) -> dict[str, dict[str, Any]]:
    """Accounts under a confirmed known pause during the month (decided 6 Nov 2026): the
    split stays computed; the pause is named next to it, never subtracted from it."""
    from othor.engine.detect import windows

    end = next_month(t) - dt.timedelta(days=1)
    out: dict[str, dict[str, Any]] = {}
    for p in ctx.spec.get("suppressions", []):
        who = str(p.get("entity") or "").lower()
        if not who or p.get("confirmed", True) is False:
            continue
        for n in names:
            if n.lower() != who:
                continue
            for a, b in windows(p, range(t.year - 1, t.year + 2)):
                if a <= end and t <= b:
                    out[n] = {
                        "until": b.isoformat(),
                        "answered_on": p.get("answered_on"),
                        "pattern": p.get("key"),
                    }
    return out


def next_month(d: dt.date) -> dt.date:
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


register(Spec(NAME, VERSION, "Net hiding gross", run))
