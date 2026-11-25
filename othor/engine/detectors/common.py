# SPDX-License-Identifier: Apache-2.0
"""Helpers the detectors share: which series to test, families, money at stake."""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np

from othor.engine.detectors.base import Context, Series

LOW_CARDINALITY = 10  # breakdowns tested value by value (regions, plants, segments, banks)
RECENT_WEEKS = 13  # a change older than a quarter isn't "open" (P2 is 11 weeks old on 1 Nov)


def family(ctx: Context, metric: str) -> str:
    """Metrics of one family (net revenue and its components; dispatch time and its spread) give
    one finding, not one per metric. A derived metric without a family of its own belongs to
    its components' family when they share one (average order value → orders)."""
    m = ctx.metric(metric)
    if m.get("family"):
        return str(m["family"])
    fams = {ctx.metric(c).get("family") for c in m.get("components") or []}
    if len(fams) == 1 and None not in fams:
        return str(fams.pop())
    for f, members in _families(ctx).items():
        if metric in members:
            return f
    return metric


def _families(ctx: Context) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for k, m in ctx.spec.get("metrics", {}).items():
        if m.get("family"):
            out.setdefault(str(m["family"]), set()).add(k)
    return out


def masked(ctx: Context, s: Series) -> np.ndarray:
    """The series' values with known-pattern windows (shutdowns, festivals, quarter-ends) for its
    metric, plant or entity masked out (NaN): expected variation never builds a baseline step."""
    from othor.engine.detect import pattern_applies, windows

    x = s.values.copy()
    if not len(s.periods):
        return x
    years = range(s.periods[0].year - 1, s.periods[-1].year + 1)
    span = {"week": 6, "month": 30, "day": 0}.get(s.grain, 0)
    for p in ctx.spec.get("suppressions", []):
        if not p.get("confirmed", True) or not pattern_applies(
            ctx, p, s.metric, s.breakdown_value, []
        ):
            continue
        for a, b in windows(p, years):
            for i, d in enumerate(s.periods):
                if a <= d + dt.timedelta(days=span) and d <= b:
                    x[i] = np.nan
    return x


def unit(ctx: Context, metric: str) -> str:
    return str(ctx.metric(metric).get("unit") or "inr")


def is_snapshot(ctx: Context, metric: str) -> bool:
    m = ctx.metric(metric)
    if m.get("kind") == "snapshot":
        return True
    comps = m.get("components") or []
    return bool(comps) and all(ctx.metric(c).get("kind") == "snapshot" for c in comps)


def low_card_breakdowns(ctx: Context, metric: str, grain: str) -> list[str]:
    out = []
    for b in ctx.series.breakdowns(metric, grain):
        if ctx.spec.get("breakdowns", {}).get(b, {}).get("entity"):
            continue  # entities (customers) are Missing records' and Concentration's
        if len(ctx.series.values_of(metric, grain, b)) <= LOW_CARDINALITY:
            out.append(b)
    return out


def candidates(ctx: Context, grain: str) -> list[Series]:
    """Totals and low-cardinality breakdown values of every active metric at a grain."""
    out: list[Series] = []
    for metric in ctx.active_metrics():
        s = ctx.series.get(metric, grain)
        if s is not None:
            out.append(s)
        for b in low_card_breakdowns(ctx, metric, grain):
            out += ctx.series.values_of(metric, grain, b)
    return out


def inr_component(ctx: Context, metric: str) -> str | None:
    """For a ratio or share, the ₹ metric its money comes from (the numerator, first ₹ part)."""
    if unit(ctx, metric) == "inr":
        return metric
    for c in ctx.metric(metric).get("components") or []:
        if unit(ctx, c) == "inr":
            return str(c)
    return None


def at_stake(
    ctx: Context, s: Series, start: dt.date, end: dt.date
) -> tuple[float | None, str | None]:
    """₹ flowing through what the series measures, over [start, end): the metric's own value for
    a ₹ flow, else its declared `at_stake` metric (e.g. dispatch time → line revenue) for the same
    breakdown value. Returns (money, the ₹ metric used)."""
    proxy = ctx.metric(s.metric).get("at_stake") or {}
    target = proxy.get("metric") or (s.metric if unit(ctx, s.metric) == "inr" else None)
    if target is None:
        return None, None
    for grain in ("day", "week", "month"):
        t = ctx.series.get(target, grain, s.breakdown, s.breakdown_value)
        if t is None:
            continue
        v = [x for p, x in zip(t.periods, t.values, strict=True) if start <= p < end]
        v = [x for x in v if np.isfinite(x)]
        if v:
            return float(np.sum(v)), str(target)
    return None, None


# #138 (owner, 25 Nov 2026): a change in a number with no ₹ of its own puts at risk the share of
# its linked ₹ that the size of the move says, never more than this share of it
MAX_AT_RISK = 0.5
SHARE_UNITS = ("percent", "ratio")


def at_risk_fraction(unit: str, before: float, after: float) -> tuple[float | None, str]:
    """The share of the linked ₹ a move from `before` to `after` puts at risk, uncapped, and how
    it was read: a share of a whole (a percent or ratio with both levels inside 0–1) moves in
    points of the whole ("share": 3.2% → 3.8% is 0.6% of the linked ₹); anything else (a count,
    metres, a duration, a ratio over 1) by its move as a share of where it was ("relative":
    1,000 → 800 orders is 20%). From a level of 0 there is no share to read: None, the money
    can't be sized (owner, 25 Nov 2026)."""
    move = abs(after - before)
    if unit in SHARE_UNITS and 0 <= before <= 1 and 0 <= after <= 1:
        return move, "share"
    if before == 0:
        return (None if move > 0 else 0.0), "relative"
    return move / abs(before), "relative"


def good_direction(ctx: Context, metric: str) -> tuple[str, list[str]] | None:
    """The way the owner wants `metric` to move, where it is known: its goals' direction (at
    least: up; at most: down) and the goals' keys, when every goal on it agrees; else None. (No
    metric carries a direction setting of its own yet.)"""
    goals = [g for g in ctx.spec.get("goals") or [] if g.get("metric") == metric]
    ways = {"up" if g.get("direction", "at_least") == "at_least" else "down" for g in goals}
    if len(ways) != 1:
        return None
    return ways.pop(), sorted(str(g.get("key") or "") for g in goals)


def capped(fraction: float) -> float:
    return min(fraction, MAX_AT_RISK)


def share_text(fraction: float) -> str:
    """'0.6%', '23.4%', '50%': one decimal, a whole number without it."""
    from decimal import Decimal

    t = str((Decimal(str(fraction)) * 100).quantize(Decimal("0.1")))
    return (t[:-2] if t.endswith(".0") else t) + "%"


def at_stake_of_change(
    ctx: Context,
    s: Series,
    start: dt.date,
    end: dt.date,
    before: float,
    after: float,
    *,
    scaled: bool = True,
) -> tuple[float | None, str, dict[str, Any]]:
    """The ₹ a change in a non-₹ number puts at risk, over [start, end): its linked `at_stake` ₹
    (`at_stake`) times the share the move says (`at_risk_fraction`), capped at MAX_AT_RISK; with
    its basis and the parameters provenance shows (the linked ₹, the scaling and the cap). A move
    the way the number's goals want puts none at risk; a move from 0 can't be sized (None, with
    `at_risk_scaling` "from_zero"; owner, 25 Nov 2026). Not `scaled` (changepoint version 2, runs
    made before #138): the linked ₹ in full, whatever the direction."""
    from othor.engine.detectors.base import PAISA, dec

    got, target = at_stake(ctx, s, start, end)
    if got is None or target is None:
        return None, "", {}
    since = f"{s.label()} since {start.isoformat()}"
    if not scaled:
        return got, f"{target} at risk for {since}", {}
    params: dict[str, Any] = {"linked_metric": target, "linked_money": dec(got, places=PAISA)}
    good = good_direction(ctx, s.metric)
    way = "up" if after > before else "down"
    if good is not None and after != before:
        keys = ", ".join(good[1])
        if way == good[0]:
            params["at_risk_direction"] = f"good: {way}, as the goal {keys} wants"
            return None, "", params
        params["at_risk_direction"] = f"bad: {way}, against the goal {keys}"
    raw, how = at_risk_fraction(unit(ctx, s.metric), before, after)
    if raw is None:
        params["at_risk_scaling"] = "from_zero"
        return None, f"the money at risk can't be sized: {s.label()} started from 0", params
    used = capped(raw)
    params |= {
        "at_risk_scaling": how,
        "at_risk_fraction": dec(raw),
        "at_risk_cap": str(MAX_AT_RISK),
        "at_risk_fraction_used": dec(used),
    }
    return got * used, f"{share_text(used)} of {target} at risk for {since}", params


def weeks_between(a: dt.date, b: dt.date) -> int:
    return max(0, (b - a).days // 7)


def config_num(cfg: dict[str, Any], key: str, default: float) -> float:
    v = cfg.get(key, default)
    return float(v) if v is not None else default


def percentile(values: Any, q: float) -> float:
    """The q-quantile by linear interpolation between closest ranks (Hyndman & Fan type 7, the
    numpy default), written out so the value never depends on a library version (docs/04).
    Sorted values x, h = (n − 1)·q; result = x[⌊h⌋] + (h − ⌊h⌋)·(x[⌊h⌋+1] − x[⌊h⌋])."""
    xs = sorted(float(v) for v in values)
    if not xs:
        raise ValueError("percentile of no values")
    h = (len(xs) - 1) * q
    lo = int(h)
    if lo + 1 >= len(xs):
        return xs[-1]
    return xs[lo] + (h - lo) * (xs[lo + 1] - xs[lo])
