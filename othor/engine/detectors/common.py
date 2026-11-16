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
