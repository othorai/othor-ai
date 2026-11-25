# SPDX-License-Identifier: Apache-2.0
"""The ordinary picture (Morning Brief 2.4 and Weekly Digest 1.1, owner 20–21 Nov 2026;
docs/plans/ordinary-picture.md). On a day or week with nothing significant, a few plain updates
from the run's stored points, so the owner still learns something about their own numbers:

1. **the numbers that moved most**, ranked by the size of the move against the number's own usual
   variation (its period-to-period changes before; not by significance): "Collections ₹1.06 Cr
   in the week of 2 Nov, up 8% on the week before";
2. **where each sits in its usual range**: from the forecast of record for that period when there
   is one ("within its usual range, near the top"), else against its own last periods ("the
   highest in 13 weeks");
3. **the best and weakest part**: the breakdown value with the largest gain and the largest fall
   (groups such as region or plant; never a named account);
4. **each goal's progress**, even on track.

Pure and deterministic: points, the forecast of record and the forecast's goals in, plain lines
out, each with its metric and window (its provenance). No model, no randomness, no clock; the
same inputs give the same bytes, so replay agrees. Never a finding: no tags, no money at stake,
never counted in Sweep; the words are "updates", never "found".
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from itertools import pairwise
from typing import Any

from othor.briefs import values
from othor.briefs.tiles import grain
from othor.charts.spec import Annotation, Axis, Category, ChartSpec, Period, Series, Unit
from othor.core import fmt
from othor.engine.finding_charts import dump
from othor.engine.store import Point

LINES = 4  # up to 4 updates in a brief (the plan)
HISTORY = 12  # the periods a move and a range are measured against
LAKH = Decimal(100000)
MIN_HISTORY = 6  # fewer than this before the latest: no range line from history
NO_CHANGE = Decimal("0.005")
INTRO = "Here are some updates from your numbers"
QUIET_HEAD = "Nothing significant came up"


# --- small words -------------------------------------------------------------------------------


def _when(g: str, period: dt.date) -> str:
    if g == "month":
        return f"in {period:%b}"
    if g == "week":
        return f"in the week of {fmt.day_month(period)}"
    return f"on {fmt.day_month(period)}"


def _before(g: str, previous: dt.date) -> str:
    if g == "month":
        return f"{previous:%b}"
    return "the week before" if g == "week" else "the day before"


def _plural(g: str, n: int) -> str:
    return f"{fmt.num(n)} {g}{'' if n == 1 else 's'}"


def _amount(unit: str | None, d: Decimal) -> str:
    """A change's size in the number's own unit, or "" when it rounds to nothing."""
    d = abs(d)
    if unit in ("percent", "ratio"):
        txt = fmt.num(d * 100, decimals=1).removesuffix(".0")
        return "" if txt in ("0", "0.0") else f"{txt} point{'' if txt == '1' else 's'}"
    txt = values.text(unit, d)
    return "" if txt.strip(" ₹$dmLCrKMB.").strip("0") == "" else txt


def _change(unit: str | None, value: Decimal, before: Decimal, g: str, prev: dt.date) -> str:
    """ "up 8% on the week before", "down 2 d on Sep", "about the same as Sep"; a rise of more
    than 100% is written from one value to the other, never "+378%"."""
    diff = value - before
    if before > 0 and diff > before and unit not in ("days", "percent", "ratio"):
        was = "in " + _before(g, prev) if g == "month" else _before(g, prev)
        return f"up from {values.text(unit, before)} {was}"
    if unit in ("days", "percent", "ratio"):
        size = _amount(unit, diff)
    elif before and abs(diff / abs(before)) >= NO_CHANGE:
        size = fmt.pct(abs(diff / abs(before)))
    else:
        size = ""
    if not size:
        return f"about the same as {_before(g, prev)}"
    return f"{'up' if diff > 0 else 'down'} {size} on {_before(g, prev)}"


def _name(spec: dict[str, Any], metric: str) -> str:
    return str((spec.get("metrics", {}).get(metric) or {}).get("name") or metric)


def _low(s: str) -> str:
    return fmt.lower_first(s)


# --- the series --------------------------------------------------------------------------------


def _series(
    points: list[Point], metric: str, g: str, breakdown: str = "", value: str | None = None
) -> list[tuple[dt.date, Decimal]]:
    return sorted(
        (p.period, p.value)
        for p in points
        if p.metric == metric
        and p.grain == g
        and p.breakdown == breakdown
        and (not breakdown or p.breakdown_value == value)
        and p.complete
        and p.value is not None
    )


def _usual_step(s: list[tuple[dt.date, Decimal]]) -> Decimal:
    """The number's usual period-to-period change before its latest one (root mean square of the
    last HISTORY changes): what a move is measured against."""
    vs = [v for _, v in s[:-1]][-(HISTORY + 1) :]
    steps = [b - a for a, b in pairwise(vs)]
    if not steps:
        return Decimal(0)
    return (sum((x * x for x in steps), Decimal(0)) / len(steps)).sqrt()


def _range(
    s: list[tuple[dt.date, Decimal]],
    g: str,
    rec: dict[str, Any] | None,
) -> str:
    value = s[-1][1]
    if rec is not None and rec.get("lower") is not None and rec.get("upper") is not None:
        lo, hi = Decimal(str(rec["lower"])), Decimal(str(rec["upper"]))
        if value < lo:
            return "below its usual range"
        if value > hi:
            return "above its usual range"
        pos = (value - lo) / (hi - lo) if hi > lo else Decimal("0.5")
        where = "near the bottom" if pos < Decimal(1) / 3 else "near the top"
        if Decimal(1) / 3 <= pos <= Decimal(2) / 3:
            where = "near the middle"
        return f"within its usual range, {where}"
    before = [v for _, v in s[:-1]][-HISTORY:]
    if len(before) < MIN_HISTORY:
        return ""
    n = len(before) + 1
    lo, hi = min(before), max(before)
    if value > hi:
        return f"the highest in {_plural(g, n)}"
    if value < lo:
        return f"the lowest in {_plural(g, n)}"
    if hi == lo:
        return ""
    pos = (value - lo) / (hi - lo)
    part = "lower" if pos < Decimal(1) / 3 else ("upper" if pos > Decimal(2) / 3 else "middle")
    return f"in the {part} part of its last {_plural(g, n)}"


def _shape(change: str, where: str) -> str:
    """The kind of move, for a story's heading: a record, up, down or flat."""
    if where.startswith("the highest"):
        return "record_high"
    if where.startswith("the lowest"):
        return "record_low"
    if change.startswith("up"):
        return "up"
    if change.startswith("down"):
        return "down"
    return "flat"


def _line(
    key: str,
    name: str,
    unit: str | None,
    g: str,
    s: list[tuple[dt.date, Decimal]],
    where: str,
) -> str:
    """The number's last periods as a line with its axes, the latest value marked."""
    pts = s[-(HISTORY + 1) :]
    small = values.small_money([v for _, v in pts]) if unit == "inr" else False
    last_p, last_v = pts[-1]
    chart_unit = values.chart_unit(unit, small)
    div = Decimal(1)
    if unit == "metres" and max(abs(v) for _, v in pts) >= LAKH:  # axis in lakh metres
        chart_unit, div = Unit("metres", scale="l", decimals=1), LAKH

    def y(v: Decimal) -> float:
        return values.chart_value(unit, v, small) if div == 1 else float(v / div)

    label = where[:1].upper() + where[1:] if where.startswith("the ") else values.text(unit, last_v)
    every = {"month": "monthly", "day": "daily"}.get(g, "weekly")
    return dump(
        ChartSpec(
            id=f"ordinary-line:{key}:{g}:{last_p.isoformat()}",
            kind="line",
            title=f"{name}, {every}",
            unit=chart_unit,
            period=Period(g, pts[0][0].isoformat(), last_p.isoformat()),  # type: ignore[arg-type]
            series=(
                Series(
                    key,
                    name,
                    "neutral",
                    tuple((p.isoformat(), y(v)) for p, v in pts),
                ),
            ),
            annotations=(
                Annotation(
                    "point",
                    at=last_p.isoformat(),
                    value=y(last_v),
                    label=label,
                    role="neutral",
                ),
            ),
            provenance={"metric_id": key},
            alt_text=(
                f"{name}, {every}, {_plural(g, len(pts))} to {_when(g, last_p)}: "
                f"{values.text(unit, last_v)}."
            ),
        )
    )


# --- the four parts ----------------------------------------------------------------------------


def movers(
    spec: dict[str, Any],
    metrics: list[str],
    points: list[Point],
    record: list[dict[str, Any]],
    stale: set[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in metrics:
        m = spec.get("metrics", {}).get(key) or {}
        if not m or key in stale:
            continue
        g = grain(m)
        s = _series(points, key, g)
        if len(s) < 2:
            continue
        (prev, before), (period, value) = s[-2], s[-1]
        unit = m.get("unit")
        usual = _usual_step(s)
        move = abs(value - before)
        score = (move / usual) if usual else (Decimal(0) if not move else Decimal(999))
        rec = next(
            (
                r
                for r in record
                if r.get("metric") == key
                and r.get("grain") == g
                and r.get("period") == period.isoformat()
            ),
            None,
        )
        name = _name(spec, key)
        change = _change(unit, value, before, g, prev)
        where = _range(s, g, rec)
        if change.startswith("about the same") and where.startswith(("the highest", "the lowest")):
            where = ""  # a move too small to show is never "the highest in 12 months"
        shape = _shape(change, where)
        # a rebound from an unusually slow (or busy) period is called that, never "+378%"
        earlier = [v for _, v in s[:-2]][-HISTORY:]
        usual_now = len(earlier) >= MIN_HISTORY and min(earlier) <= value <= max(earlier)
        if usual_now and (before < min(earlier) or before > max(earlier)):
            span = {"month": "month", "day": "day"}.get(g, "week")
            slow = "slow" if before < min(earlier) else "busy"
            change, where, shape = f"back to its usual level after a {slow} {span}", "", "rebound"
        text = f"{name} {values.text(unit, value)} {_when(g, period)}, {change}"
        out.append(
            {
                "kind": "mover",
                "metric": key,
                "name": name,
                "grain": g,
                "window": [prev.isoformat(), period.isoformat()],
                "value": values.text(unit, value),
                "change": change,
                "range": where,
                "shape": shape,
                "periods": len(earlier) + 2,
                "score": str(score.quantize(Decimal("0.001"))),
                "text": text + (f", {where}." if where else "."),
                "chart": _line(key, name, unit, g, s, where),
            }
        )
    out.sort(key=lambda x: (-Decimal(str(x["score"])), str(x["metric"])))
    return out


def _parts_of(
    spec: dict[str, Any], key: str, points: list[Point], g: str, window: list[str]
) -> dict[str, Any] | None:
    m = spec.get("metrics", {}).get(key) or {}
    unit = m.get("unit")
    prev, period = (dt.date.fromisoformat(x) for x in window)
    groups = spec.get("breakdowns") or {}
    best: tuple[Decimal, str, list[tuple[str, Decimal]]] | None = None
    have: dict[str, dict[str, dict[dt.date, Decimal]]] = {}
    for p in points:
        if (
            p.metric != key
            or p.grain != g
            or not p.breakdown
            or p.breakdown_value is None
            or p.value is None
            or not p.complete
            or p.period not in (prev, period)
            or (groups.get(p.breakdown) or {}).get("entity", True)
        ):
            continue
        have.setdefault(p.breakdown, {}).setdefault(p.breakdown_value, {})[p.period] = p.value
    for bk in sorted(have):
        deltas = sorted(
            ((v, d[period] - d[prev]) for v, d in have[bk].items() if prev in d and period in d),
            key=lambda x: (-x[1], x[0]),
        )
        if len(deltas) < 2:
            continue
        up, down = deltas[0], deltas[-1]
        spread = max(up[1], Decimal(0)) - min(down[1], Decimal(0))
        if spread and (best is None or spread > best[0]):
            best = (spread, bk, deltas)
    if best is None:
        return None
    _, bk, deltas = best
    (top, gain), (bottom, fall) = deltas[0], deltas[-1]
    sides = [
        {"part": v, "word": word, "size": size}
        for v, d, word in ((top, gain, "up"), (bottom, -fall, "down"))
        if d > 0 and (size := _amount(unit, d))
    ]
    if not sides:
        return None
    bits = [f"{x['part']} {x['word']} {x['size']}" for x in sides]
    group = str((groups.get(bk) or {}).get("name") or bk)
    name = _name(spec, key)
    text = f"{name} by {_low(group)}: {' and '.join(bits)} on {_before(g, prev)}."
    return {
        "kind": "parts",
        "metric": key,
        "grain": g,
        "window": window,
        "breakdown": bk,
        "group": group,
        "sides": sides,
        "text": text,
        "chart": _bars(key, name, group, unit, g, prev, period, deltas),
    }


def _bars(
    key: str,
    name: str,
    group: str,
    unit: str | None,
    g: str,
    prev: dt.date,
    period: dt.date,
    deltas: list[tuple[str, Decimal]],
) -> str:
    """Each part's change as contribution bars from zero (the Weekly Digest's chart)."""
    small = values.small_money([d for _, d in deltas]) if unit == "inr" else False
    cats = tuple(
        Category(
            v,
            values.chart_value(unit, d, small),
            "negative" if d < 0 else "positive",
        )
        for v, d in sorted(deltas, key=lambda x: (x[1], x[0]))
    )
    when = _when(g, period).removeprefix("in ").removeprefix("on ")
    return dump(
        ChartSpec(
            id=f"ordinary:{key}:{g}:{period.isoformat()}",
            kind="bars_contribution",
            title=f"{name} change by {_low(group)} · {when} vs {_before(g, prev)}",
            unit=values.chart_unit(unit, small),
            x=Axis("category"),
            categories=cats,
            provenance={"metric_id": key},
            alt_text=(
                f"{name} change by {_low(group)}, {when}: "
                + ", ".join(
                    f"{v} {'up' if d > 0 else 'down'} {values.text(unit, abs(d))}"
                    for v, d in deltas
                )
                + "."
            ),
        )
    )


def goal_updates(
    spec: dict[str, Any], metrics: list[str], goals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out = []
    for g in sorted(goals, key=lambda x: (str(x.get("by")), str(x.get("key")))):
        key = g.get("metric")
        if key not in metrics or g.get("projection") is None or g.get("target") is None:
            continue
        unit = (spec.get("metrics", {}).get(key) or {}).get("unit")
        by = dt.date.fromisoformat(str(g["by"]))
        name = _name(spec, key)
        fy = g.get("aggregate") == "fiscal_year_total"
        label = f"{name} FY{by.year % 100:02d}" if fy else name
        when = "" if fy else f" by {fmt.day_month(by)}"
        text = (
            f"{label}: heading for {values.rounded(unit, g['projection'], like=g['target'])} "
            f"against the {values.goal_target(unit, g['target'])} goal{when}"
        )
        if g.get("probability") is not None:
            text += f", {int(g['probability'])}% likely to reach it"
        out.append(
            {
                "kind": "goal",
                "metric": key,
                "key": g["key"],
                "probability": g.get("probability"),
                "window": [str(g["by"])],
                "text": text + ".",
            }
        )
    return out


def picture(
    spec: dict[str, Any],
    metrics: list[str],
    points: list[Point],
    *,
    record: list[dict[str, Any]] | None = None,
    goals: list[dict[str, Any]] | None = None,
    stale: set[str] | frozenset[str] = frozenset(),
    limit: int = LINES,
) -> dict[str, Any]:
    """The ordinary picture of `metrics`: the movers (ranked), the parts (in the movers' order),
    the goals, and up to `limit` lines in reading order (the top mover, its parts, the next
    mover, a goal, then the rest)."""
    ms = movers(spec, metrics, points, record or [], set(stale))
    parts = [p for m in ms if (p := _parts_of(spec, m["metric"], points, m["grain"], m["window"]))]
    gs = goal_updates(spec, metrics, goals or [])
    order = ms[:1] + parts[:1] + ms[1:2] + gs[:1] + ms[2:] + parts[1:] + gs[1:]
    lines = [{k: x[k] for k in ("kind", "metric", "text", "window")} for x in order[:limit]]
    return {"movers": ms, "parts": parts, "goals": gs, "lines": lines}
