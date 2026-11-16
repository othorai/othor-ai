# SPDX-License-Identifier: Apache-2.0
"""Headline tiles (docs/04 §8, decided 5 Nov 2026). Pure: the same points give the same tile.

- The value of the last complete period: the metric's own grain; a daily flow at the week.
- One comparison, named so a reader never confuses them:
  1. **band:** the forecast of record for that period ("in band", "−1% vs band"), measured
     from the nearest band edge;
  2. **goal:** a goal on the metric ("on track for 45 · vs target", "target 42 · vs target");
  3. **period:** against the period before ("+4% vs Sep"; under 0.5% is "no change vs Sep");
  - before 8 weeks of history and with no band: "3 days of history · trend and band from
    24 Dec" (10j).
- A stale source: "as of Fri" (10l).
- A sparkline ChartSpec of the last 12 periods, with the band where forecasts of record exist.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from othor.briefs import values
from othor.charts.spec import Band, ChartSpec, Period, Series
from othor.core import fmt
from othor.engine.finding_charts import dump
from othor.engine.forecast.ets import BAND_WEEKS
from othor.engine.store import Point

SPARK = 12
NO_CHANGE = Decimal("0.005")


def grain(m: dict[str, Any]) -> str:
    own = str(m.get("grain") or "month")
    return "week" if own == "day" and m.get("kind", "flow") == "flow" else own


def _label(name: str, g: str, period: dt.date) -> str:
    if g == "month":
        return f"{name} · {period:%b}"
    if g == "week":
        return f"{name} · week of {fmt.day_month(period)}"
    return name


def _when(g: str, period: dt.date) -> str:
    if g == "month":
        return f"{period:%b}"
    if g == "week":
        return f"week of {fmt.day_month(period)}"
    return fmt.day_month(period)


def _end(g: str, period: dt.date) -> dt.date:
    if g == "month":
        return (period.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(1)
    if g == "week":
        return period + dt.timedelta(days=6)
    return period


def _pct(d: Decimal) -> str:
    return fmt.pct(abs(d))


def _series(points: list[Point], metric: str, g: str) -> list[tuple[dt.date, Decimal]]:
    out = [
        (p.period, p.value)
        for p in points
        if p.metric == metric
        and p.grain == g
        and p.breakdown == ""
        and p.complete
        and p.value is not None
    ]
    return sorted(out)


def tile(
    spec: dict[str, Any],
    metric: str,
    points: list[Point],
    record: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    run_date: dt.date,
    *,
    stale_as_of: dict[str, dt.date] | None = None,
    known: list[tuple[dt.date, dt.date, str, list[str]]] | None = None,
) -> dict[str, Any] | None:
    m = spec.get("metrics", {}).get(metric) or {}
    g = grain(m)
    unit = m.get("unit")
    name = str(m.get("name") or metric)
    s = _series(points, metric, g)
    if not s:
        return None
    period, value = s[-1]
    rec = next(
        (
            r
            for r in record
            if r["metric"] == metric and r["grain"] == g and r["period"] == period.isoformat()
        ),
        None,
    )
    goal = next((x for x in goals if x.get("metric") == metric), None)
    role = "neutral"
    if rec is not None and rec.get("lower") is not None and rec.get("upper") is not None:
        lo, hi = Decimal(rec["lower"]), Decimal(rec["upper"])
        if value < lo:
            comp = {"kind": "band", "text": f"−{_pct((lo - value) / lo)} vs band"}
            role = "negative"
        elif value > hi:
            comp = {"kind": "band", "text": f"+{_pct((value - hi) / hi)} vs band"}
            role = "negative"
        else:
            comp = {"kind": "band", "text": "in band"}
    elif goal is not None and goal.get("aggregate", "value") == "value":
        target = values.text(unit, goal["target"]).replace(" d", "")
        target = target.replace(fmt.currency_symbol(), "")
        if goal.get("on_track"):
            comp = {"kind": "goal", "text": f"on track for {target} · vs target"}
            role = "positive"
        else:
            comp = {"kind": "goal", "text": f"target {target} · vs target"}
    else:
        span = (s[-1][0] - s[0][0]).days + 1 if g == "day" else len(s) * 7
        if g == "day" and span < BAND_WEEKS * 7:
            starts = s[0][0] + dt.timedelta(weeks=BAND_WEEKS)
            comp = {
                "kind": "history",
                "text": f"{len(s)} days of history · trend and band from {fmt.day_month(starts)}",
            }
        elif len(s) >= 2 and s[-2][1]:
            before_p, before = s[-2]
            change = (value - before) / abs(before)
            if abs(change) < NO_CHANGE:
                comp = {"kind": "period", "text": f"no change vs {_when(g, before_p)}"}
            else:
                sign = "+" if change > 0 else "−"
                comp = {"kind": "period", "text": f"{sign}{_pct(change)} vs {_when(g, before_p)}"}
        else:
            comp = {"kind": "history", "text": f"{len(s)} {g}s of history"}
    if comp["kind"] == "band" and comp["text"] != "in band":
        end = _end(g, period)
        hit = next(
            (
                lab
                for a, b, lab, ms in known or []
                if a <= end
                and period <= b
                and (not ms or metric in ms)
                and (end - period) <= (b - a)
            ),
            None,
        )
        if hit:  # outside its band in a known pattern's week: say which (never unexplained)
            comp = {**comp, "text": f"{comp['text']} · {hit}"}
            role = "muted"
    stale = None
    if stale_as_of and metric in stale_as_of:
        stale = f"as of {stale_as_of[metric]:%a}"
    return {
        "metric": metric,
        "label": _label(name, g, period),
        "period": period.isoformat(),
        "grain": g,
        "value": values.text(unit, value),
        "raw": str(value),
        "comparison": comp,
        "role": role,
        "stale": stale,
        "chart": _chart(metric, name, unit, g, s[-SPARK:], record, role, comp["kind"]),
    }


def _chart(
    metric: str,
    name: str,
    unit: str | None,
    g: str,
    s: list[tuple[dt.date, Decimal]],
    record: list[dict[str, Any]],
    role: str,
    kind: str,
) -> str:
    pts = tuple((p.isoformat(), values.chart_value(unit, v)) for p, v in s)
    by = {
        r["period"]: r
        for r in record
        if r["metric"] == metric and r["grain"] == g and r.get("lower") is not None
    }
    band_pts = [(p.isoformat(), by[p.isoformat()]) for p, _ in s if p.isoformat() in by]
    band = None
    if band_pts:
        band = Band(
            metric,
            0.8,
            tuple((p, values.chart_value(unit, r["lower"])) for p, r in band_pts),
            tuple((p, values.chart_value(unit, r["upper"])) for p, r in band_pts),
        )
    last = values.text(unit, s[-1][1])
    chart = ChartSpec(
        id=f"tile:{metric}:{g}:{s[-1][0].isoformat()}",
        kind="sparkline",
        title=f"{name} · {g}ly" if g != "day" else f"{name} · daily",
        unit=values.chart_unit(unit),
        period=Period(g, s[0][0].isoformat(), s[-1][0].isoformat()),  # type: ignore[arg-type]
        series=(Series(metric, name, role, pts),),  # type: ignore[arg-type]
        band=band,
        alt_text=(
            f"{name}, last {len(s)} {g}s to {_when(g, s[-1][0])}: {last}"
            + (" against its forecast band." if band and kind == "band" else ".")
        ),
        provenance={"metric_id": metric},
    )
    return dump(chart)


def emphasis(t: dict[str, Any]) -> str:
    """How loud a stored tile is (owner, 11 Nov 2026; M15.3), worked out when it is shown so the
    brief's stored body never changes: "clear" (red) when it is outside its band by more than the
    band's half-width, or a goal falling behind; "edge" (dark, with a ring) when just outside;
    "in" (grey) inside its band; "" otherwise (a period or history comparison, or a known
    pattern that explains it)."""
    comp = (t.get("comparison") or {}).get("kind")
    if t.get("role") == "muted":
        return ""
    if comp == "goal":
        return "" if t.get("role") == "positive" else "clear"
    if comp != "band" or not t.get("chart"):
        return ""
    from othor.engine.finding_charts import load

    spec = load(t["chart"])
    if spec.band is None or not spec.series or not spec.series[0].points:
        return ""
    when, v = spec.series[0].points[-1]
    lo = next((y for d, y in spec.band.lower if d == when), None)
    hi = next((y for d, y in spec.band.upper if d == when), None)
    if lo is None or hi is None:
        return ""
    low, high, val = Decimal(str(lo)), Decimal(str(hi)), Decimal(str(v))
    if low <= val <= high:
        return "in"
    half = (high - low) / 2
    return "clear" if max(low - val, val - high) > half else "edge"
