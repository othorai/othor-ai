# SPDX-License-Identifier: Apache-2.0
"""Forecast breach (docs/04 §3, detector 3; M7): a complete period against the forecast stored
before it (never a refit), and a goal's projection against its target.

- **Level:** each recent complete period with a forecast of record (the latest made before the
  period started, from the run's context). The test is two-sided on the forecast's predictive
  distribution (normal, sd from the 80% band); money is the distance outside the band. A
  forecast stored without a band (under 8 weeks of history) isn't tested. Weekly and monthly
  breaches go through the same FDR as every other test, then the known-pattern windows (a
  Diwali week, a plant shutdown).
- **Goal:** a fiscal-year projection (or a value goal's forecast for its month) that misses the
  target by more than the materiality floor; money is the shortfall when the goal is in ₹.

Coverage: a company starts with no stored forecasts; Forecast breach starts as periods complete
(first forecasts on the company's first run), so it runs partly until then.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from scipy import stats

from othor.engine.detectors import common
from othor.engine.detectors.base import Context, Coverage, Result, Spec, dec, pval, register
from othor.engine.forecast.ets import LEVEL, next_periods

NAME = "forecast_breach"
VERSION = "1"
Z80 = float(stats.norm.ppf(0.5 + LEVEL / 2))


def _actual(ctx: Context, metric: str, grain: str, period: dt.date) -> float | None:
    s = ctx.series.get(metric, grain)
    if s is None:
        return None
    for p, v in zip(s.periods, s.values, strict=True):
        if p == period and v == v:
            return float(v)
    return None


def _per_unit(ctx: Context, metric: str, grain: str, period: dt.date) -> tuple[float, str] | None:
    """₹ per unit of a non-₹ metric (metres dispatched → line revenue per metre), from its
    declared `at_stake` metric over the 13 periods before the one tested."""
    proxy = (ctx.metric(metric).get("at_stake") or {}).get("metric")
    a, b = ctx.series.get(metric, grain), ctx.series.get(proxy, grain) if proxy else None
    if a is None or b is None:
        return None
    va = {p: float(v) for p, v in zip(a.periods, a.values, strict=True) if v == v and p < period}
    vb = {p: float(v) for p, v in zip(b.periods, b.values, strict=True) if v == v and p < period}
    days = sorted(set(va) & set(vb))[-13:]
    units = sum(va[d] for d in days)
    if not days or units <= 0:
        return None
    return sum(vb[d] for d in days) / units, str(proxy)


def _result(ctx: Context, metric: str, grain: str, window: tuple[dt.date, dt.date]) -> Result:
    return Result(
        detector=NAME,
        version=VERSION,
        series=[f"{metric}|{grain}||"],
        metric=metric,
        breakdown="",
        breakdown_value=None,
        window=window,
        statistic=None,
        p_value=None,
        effect=None,
        strong=False,
        direction="none",
        money=None,
        money_basis="",
        family=common.family(ctx, metric),
    )


def _level(ctx: Context, r: dict[str, Any]) -> Result | None:
    metric, grain = r["metric"], r["grain"]
    period = dt.date.fromisoformat(r["period"])
    end = next_periods(period, grain, 1)[0] - dt.timedelta(days=1)
    actual = _actual(ctx, metric, grain, period)
    if actual is None or r.get("lower") is None or r.get("upper") is None:
        return None
    point, lo, hi = float(r["point"]), float(r["lower"]), float(r["upper"])
    sd = (hi - lo) / (2 * Z80) if hi > lo else 0.0
    res = _result(ctx, metric, grain, (period, end))
    res.reconstructed_only = bool(r.get("reconstructed"))
    if sd <= 0:
        res.eligible, res.reason = False, "no band"
        return res
    z = (actual - point) / sd
    outside = lo - actual if actual < lo else actual - hi if actual > hi else 0.0
    res.statistic, res.effect = dec(z), dec((actual - point) / point if point else None)
    res.p_value = pval(float(2 * stats.norm.sf(abs(z))))
    res.direction = "up" if actual > point else "down"
    res.strong = outside > (hi - lo) / 2
    unit = ctx.metric(metric).get("unit")
    if unit == "inr" and outside > 0:
        res.money = dec(outside, places=Decimal("0.01"))
        res.money_basis = (
            f"{metric} {grain} of {period.isoformat()} outside its 80% band by the amount shown"
        )
    elif outside > 0:
        per = _per_unit(ctx, metric, grain, period)
        if per is not None:
            res.money = dec(outside * per[0], places=Decimal("0.01"))
            res.money_basis = (
                f"{metric} outside its 80% band × {per[1]} per unit over the 13 periods before"
            )
    res.slots = {
        "grain": grain,
        "period": period.isoformat(),
        "actual": dec(actual),
        "point": r["point"],
        "lower": r["lower"],
        "upper": r["upper"],
        "share": dec((actual - point) / point if point else 0),
        "forecast_as_of": r["as_of"],
        "method": r.get("method", ""),
        "unit": unit or "",
    }
    if not outside:
        res.reason = "inside its band"
    return res


def _goal(ctx: Context, g: dict[str, Any]) -> Result | None:
    by = dt.date.fromisoformat(g["by"])
    start = by.replace(year=by.year - 1) + dt.timedelta(days=1)
    res = _result(ctx, g["metric"], "month", (start, by))
    res.series = [f"goal|{g['key']}"]
    res.family = f"goal:{g['key']}"
    short = Decimal(g["short_by"])
    projection = Decimal(g["projection"])
    res.slots = {
        "goal": g["key"],
        "name": g.get("name", ""),
        "target": g["target"],
        "projection": g["projection"],
        "to_date": g.get("to_date"),
        "lower": g.get("lower"),
        "upper": g.get("upper"),
        "short_by": g["short_by"],
        "by": g["by"],
        "direction": g.get("direction", "at_least"),
        "kind": "goal",
    }
    res.direction = "down" if g.get("direction", "at_least") == "at_least" else "up"
    if g.get("lower") is None or g.get("upper") is None:
        res.eligible, res.reason = False, "no band"
        return res
    sd = (Decimal(g["upper"]) - Decimal(g["lower"])) / Decimal(str(2 * Z80))
    z = float(short / sd) if sd > 0 else 0.0
    res.statistic = dec(z)
    res.p_value = pval(float(stats.norm.sf(z)) if short > 0 else 1.0)
    res.strong = short > (Decimal(g["upper"]) - Decimal(g["lower"])) / 2
    unit = ctx.metric(g["metric"]).get("unit")
    if unit == "inr" and short > 0:
        res.money = dec(float(short), places=Decimal("0.01"))
        res.money_basis = (
            f"the target minus the projected fiscal-year total ({dec(float(projection))})"
        )
    if short <= 0:
        res.reason = "on track"
    return res


def run(ctx: Context) -> tuple[list[Result], Coverage]:
    fc = ctx.forecasts or {}
    record = fc.get("of_record") or []
    headline = list(ctx.spec.get("forecast", {}).get("headline", []))
    out: list[Result] = []
    for r in record:
        got = _level(ctx, r)
        if got is not None:
            out.append(got)
    for g in fc.get("goals") or []:
        got = _goal(ctx, g)
        if got is not None:
            out.append(got)
    tested = sorted({r.metric for r in out if r.eligible and r.series[0] != "goal"})
    notes: tuple[str, ...] = ()
    if not record:
        first = fc.get("first")
        notes = (
            "starts as periods complete"
            + (f" (first forecasts {dt.date.fromisoformat(first):%-d %b})" if first else ""),
        )
    status = (
        "could_not_run"
        if not out
        else ("ran" if headline and set(headline) <= set(tested) else "partly")
    )
    return out, Coverage(
        NAME,
        status,
        len(tested),
        len(headline),
        "forecasts",
        needs="forecast" if not out else None,
        notes=notes,
    )


register(Spec(NAME, VERSION, "Forecast breach", run, needs=("forecast",), enabled=True))
