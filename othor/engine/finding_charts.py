# SPDX-License-Identifier: Apache-2.0
"""One ChartSpec per finding (M6b, docs/12), built from the run's series and the finding's slots.
Pure and deterministic; stored in the finding so the page, the archive and replay show the same
chart.

- missing records (one account): its weekly invoices as bars that stop, then a dashed "no invoices"
  box to the run date (gap_block), from the run's entity probe. The group of silent accounts has
  no chart: its card shows their rows.

- rising variance: average line with a p10–p90 spread band, a line where it starts widening;
- broken relationship: the two series indexed to 100, a region from the break;
- changepoint: the weekly series as a step, a line at the change;
- net hiding gross: contribution bars by breakdown value, with the total;
- concentration: the top-N share now and at the start;
- cross-source (keys): the unmatched records against those with a match.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import numpy as np

from othor.charts.spec import (
    Annotation,
    Axis,
    Band,
    Category,
    ChartSpec,
    Period,
    Series,
    Unit,
    YAxis,
)
from othor.core import fmt
from othor.engine.detectors.base import SeriesIndex
from othor.engine.detectors.common import percentile

WEEKS = 13  # the charts show about the last quarter
CR = Unit("currency", "INR", "cr", 2)
USD_M = Unit("currency", "USD", "m", 2)


def money_unit() -> tuple[Unit, float]:
    """The big money unit of the company's currency (M15.6): ₹ crore, or $ millions; with the
    divisor that turns a stored amount into it. Read at the run (the spec sets the currency)."""
    return (USD_M, 1e6) if fmt.is_usd() else (CR, 1e7)


def small_money_unit() -> tuple[Unit, float]:
    """₹ lakh, or $ thousands."""
    return (
        (Unit("currency", "USD", "k", 2), 1e3)
        if fmt.is_usd()
        else (Unit("currency", "INR", "l", 2), 1e5)
    )


PCT = Unit("percent", decimals=0)


def build(
    f: dict[str, Any],
    series: SeriesIndex,
    spec: dict[str, Any],
    probes: dict[str, Any] | None = None,
) -> str | None:
    """The finding's ChartSpec as its JSON text (the run's canonical outputs hold no floats);
    `ChartSpec.from_json` reads it back for `render_svg`. `probes` are the run's probe results
    (missing records draws from the entity probe)."""
    try:
        if f["detector"] == "missing_records":
            chart = _missing(f, spec, probes or {})
        else:
            fn = _BY.get(f["detector"])
            if fn is None:
                return None
            chart = fn(f, series, spec)
    except (KeyError, ValueError, ZeroDivisionError):
        return None
    return None if chart is None else dump(chart)


PRECISION = ".6f"  # every number in a stored chart: one fixed-precision string, written once


def _fix(o: Any) -> Any:
    if isinstance(o, bool) or o is None:
        return o
    if isinstance(o, float):
        return format(o, PRECISION)
    if isinstance(o, dict):
        return {k: _fix(v) for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [_fix(v) for v in o]
    return o


def dump(chart: ChartSpec) -> str:
    """Canonical text for a stored chart: sorted keys, no spaces, numbers as fixed-precision
    strings, so replay compares the bytes (decided 5 Nov 2026)."""
    import json

    return json.dumps(
        _fix(chart.to_dict()), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _num(v: Any) -> Any:
    return float(v) if isinstance(v, str) else v


def load(text: str) -> ChartSpec:
    """The stored text back to a ChartSpec for `render_svg` (numbers back to floats)."""
    import json

    d = json.loads(text)
    for s in d.get("series") or []:
        s["points"] = [[p[0], _num(p[1])] for p in s["points"]]
    if d.get("band"):
        for side in ("lower", "upper"):
            d["band"][side] = [[p[0], _num(p[1])] for p in d["band"][side]]
        d["band"]["level"] = _num(d["band"]["level"])
    for c in d.get("categories") or []:
        c["value"] = _num(c["value"])
    for a in d.get("annotations") or []:
        if a.get("value") is not None:
            a["value"] = _num(a["value"])
    return ChartSpec.from_dict(d)


def _unit(spec: dict[str, Any], metric: str) -> Unit:
    u = spec.get("metrics", {}).get(metric, {}).get("unit")
    if u == "inr":
        return money_unit()[0]
    if u == "days":
        return Unit("days", decimals=1)
    if u in ("percent", "ratio"):
        return Unit("percent", decimals=1)
    return Unit("count")


def _scaled(spec: dict[str, Any], metric: str, values: list[float]) -> tuple[Unit, float]:
    """The metric's own unit; money in crore, lakh or plain rupees by the size of its values, so
    an order value of ₹56,000 doesn't read 0.0056 Cr (indigo2, 12 Nov 2026)."""
    u = _unit(spec, metric)
    if u.type != "currency":
        return u, 1.0
    top = max((abs(v) for v in values), default=0.0)
    big, small = money_unit(), small_money_unit()
    if top >= big[1]:
        return big
    if top >= small[1]:
        return small
    return Unit("currency", fmt.currency(), "none", 0), 1.0


def _label(u: Unit, value: float) -> str:
    """ "average ₹56,114", "average 4.3 d": a value in its metric's unit."""
    if u.type == "currency":
        return fmt.inr(value)
    if u.type == "days":
        return f"{fmt.num(value, decimals=1)} d"
    if u.type == "percent":
        return fmt.pct(value, decimals=1)
    return fmt.num(value, decimals=1)


def _name(spec: dict[str, Any], metric: str) -> str:
    return str(spec.get("metrics", {}).get(metric, {}).get("name") or metric)


def _iso(d: dt.date) -> str:
    return d.isoformat()


def _variance(f: dict[str, Any], series: SeriesIndex, spec: dict[str, Any]) -> ChartSpec | None:
    sl = f["slots"]
    day = series.get(f["metric"], "day", f["breakdown"], f["breakdown_value"])
    if day is None:
        return None
    end = dt.date.fromisoformat(f["window"][1])
    start = end - dt.timedelta(weeks=WEEKS)
    pairs = [(p, v) for p, v in zip(day.periods, day.values, strict=True) if np.isfinite(v)]
    mid, lo, hi = [], [], []
    w = end - dt.timedelta(days=(end.weekday() + 1) % 7)  # the Sunday on or before the end
    weeks = []
    while w > start:
        weeks.append(w)
        w -= dt.timedelta(weeks=1)
    for w in sorted(weeks):
        vals = [v for p, v in pairs if w - dt.timedelta(days=13) <= p <= w]
        if len(vals) < 3:
            continue
        mid.append((_iso(w), round(float(np.mean(vals)), 2)))
        lo.append((_iso(w), round(percentile(vals, 0.1), 2)))
        hi.append((_iso(w), round(percentile(vals, 0.9), 2)))
    if len(mid) < 4:
        return None
    where = f["breakdown_value"] or ""
    started = dt.date.fromisoformat(sl["started"])
    # the metric's own unit (it was days for every metric: built first for dispatch time)
    unit, div = _scaled(spec, f["metric"], [v for _, v in hi])
    if div != 1.0:
        mid, lo, hi = ([(d, round(v / div, 4)) for d, v in xs] for xs in (mid, lo, hi))
    mean = _label(unit if div == 1.0 else _unit(spec, f["metric"]), float(sl["mean_recent"]))
    # screen readers get the word ("4.3 days"), the chart's label the short form ("4.3 d")
    said = mean.replace(" d", " days") if mean.endswith(" d") else mean
    return ChartSpec(
        id=f"finding:{f['fingerprint']}",
        kind="line",
        title=f"{_name(spec, f['metric'])}, {where} · 2-week window",
        unit=unit,
        period=Period("week", mid[0][0], mid[-1][0]),
        series=(Series("avg", f"average {mean}", "neutral", tuple(mid)),),
        band=Band("avg", 0.8, tuple(lo), tuple(hi), "p10-p90", role="negative", label="p10–p90"),
        annotations=(
            Annotation(
                "vline",
                at=sl["started"],
                label=f"{fmt.day_month(started)} · spread starts widening",
                role="negative",
                style="dashed",
            ),
        ),
        provenance={"finding": f["fingerprint"], "metric_id": f["metric"]},
        alt_text=(
            f"{_name(spec, f['metric'])} at {where}: the p10–p90 spread widened from "
            f"{fmt.day_month(started)} while the average held at {said}."
        ),
    )


def _weekly(series: SeriesIndex, metric: str) -> dict[dt.date, float]:
    for grain in ("week", "month"):
        s = series.get(metric, grain)
        if s is not None:
            return {p: float(v) for p, v in zip(s.periods, s.values, strict=True) if np.isfinite(v)}
    return {}


def _pair(f: dict[str, Any], series: SeriesIndex, spec: dict[str, Any]) -> ChartSpec | None:
    sl = f["slots"]
    a, b = f["entities"][:2]
    va, vb = _weekly(series, a), _weekly(series, b)
    days = sorted(set(va) & set(vb))
    brk = dt.date.fromisoformat(sl["break"])
    days = [d for d in days if d >= brk - dt.timedelta(weeks=WEEKS - 6)][-WEEKS:]
    if len(days) < 4 or not vb[days[0]] or not va[days[0]]:
        return None
    ratio = {d: va[d] / vb[d] for d in days if vb[d]}
    base_r, base_b = ratio[days[0]], vb[days[0]]
    na, nb = _name(spec, a), _name(spec, b)
    up = fmt.pct(sl["ratio_change"], sign=True)
    return ChartSpec(
        id=f"finding:{f['fingerprint']}",
        kind="line",
        title=f"{na} per unit and {nb} · index",
        unit=Unit("ratio", decimals=0),
        period=Period("week", _iso(days[0]), _iso(days[-1])),
        series=(
            Series(
                "b",
                nb.lower(),
                "neutral",
                tuple((_iso(d), round(vb[d] / base_b * 100, 1)) for d in days),
            ),
            Series(
                "ratio",
                f"{na.lower()} per unit {up}",
                "negative",
                tuple((_iso(d), round(ratio[d] / base_r * 100, 1)) for d in days if d in ratio),
            ),
        ),
        annotations=(
            Annotation(
                "region",
                from_=sl["break"],
                to=_iso(days[-1]),
                label=(
                    f"from {fmt.day_month(brk)} · r {fmt.num(sl['r_before'], decimals=2)} → "
                    f"{fmt.num(sl['r_after'], decimals=2)}"
                ),
            ),
        ),
        provenance={"finding": f["fingerprint"], "metric_id": a},
        alt_text=(
            f"{na} per unit of {nb.lower()} rose {up} from {fmt.day_month(brk)}; correlation fell "
            f"from {fmt.num(sl['r_before'], decimals=2)} to {fmt.num(sl['r_after'], decimals=2)}."
        ),
    )


def _step(f: dict[str, Any], series: SeriesIndex, spec: dict[str, Any]) -> ChartSpec | None:
    sl = f["slots"]
    wk = series.get(f["metric"], "week", f["breakdown"], f["breakdown_value"])
    if wk is None:
        return None
    change = dt.date.fromisoformat(sl["change_week"])
    pts = [
        (p, float(v))
        for p, v in zip(wk.periods, wk.values, strict=True)
        if np.isfinite(v) and p >= change - dt.timedelta(weeks=10)
    ][-26:]
    if len(pts) < 4:
        return None
    unit, scale = _scaled(spec, f["metric"], [v for _, v in pts])
    name = _name(spec, f["metric"])
    return ChartSpec(
        id=f"finding:{f['fingerprint']}",
        kind="step",
        title=f"{name} · weekly",
        unit=unit,
        period=Period("week", _iso(pts[0][0]), _iso(pts[-1][0])),
        series=(
            Series("v", name, "negative", tuple((_iso(p), round(v / scale, 4)) for p, v in pts)),
        ),
        annotations=(
            Annotation(
                "vline",
                at=sl["change_week"],
                label=f"week of {fmt.day_month(change)}",
                role="negative",
                style="dashed",
            ),
        ),
        provenance={"finding": f["fingerprint"], "metric_id": f["metric"]},
        alt_text=(
            f"{name}, weekly: a step in the week of {fmt.day_month(change)} that has held since."
        ),
    )


def fold_below(spec: dict[str, Any]) -> Decimal:
    """Parts that moved less than a fifth of the company's floor fold into "Other" (₹1 L of the
    default ₹5 L; M15.6: the floor is the owner's, in their currency)."""
    return Decimal(str(spec.get("detectors", {}).get("materiality_inr", "500000"))) / 5


def _nhg(f: dict[str, Any], series: SeriesIndex, spec: dict[str, Any]) -> ChartSpec | None:
    sl = f["slots"]
    contrib = {k: Decimal(v) for k, v in sl["contributions"].items()}
    fold = fold_below(spec)
    big = sorted((k for k, v in contrib.items() if abs(v) >= fold), key=lambda k: contrib[k])
    unit, div = money_unit()
    bars = [
        Category(k, round(float(contrib[k]) / div, 2), "negative" if contrib[k] < 0 else "positive")
        for k in big
    ]
    total = Category("Total", round(float(Decimal(sl["net"])) / div, 2), "neutral", total=True)
    cats = (*bars, total)
    name = _name(spec, f["metric"])
    prev, now = (dt.date.fromisoformat(sl[k]).strftime("%b") for k in ("previous", "period"))
    return ChartSpec(
        id=f"finding:{f['fingerprint']}",
        kind="bars_contribution",
        title=f"{name} change by {f.get('family_breakdown') or 'breakdown'} · {now} vs {prev}",
        unit=unit,
        x=Axis("category"),
        categories=cats,
        provenance={"finding": f["fingerprint"], "metric_id": f["metric"]},
        alt_text=(
            f"{name} change, {now} vs {prev}: "
            + ", ".join(
                f"{c.key} {fmt.inr(Decimal(str(c.value)) * Decimal(str(div)), sign=True)}"
                for c in cats
            )
            + "."
        ),
    )


def _concentration(f: dict[str, Any], series: SeriesIndex, spec: dict[str, Any]) -> ChartSpec:
    sl = f["slots"]
    n = int(sl.get("top_n") or 4)
    now, start = float(sl["share_now"]), float(sl["share_start"])
    month = dt.date.fromisoformat(sl["start"]).strftime("%B")
    name = _name(spec, f["metric"])
    return ChartSpec(
        id=f"finding:{f['fingerprint']}",
        kind="bars_share",
        title=f"Top {n} customers' share of the {name.lower()}",
        unit=PCT,
        x=Axis("category"),
        categories=(
            Category("Today", now, "neutral", label=f"Top {n} · {fmt.pct(now)}"),
            Category(month, start, "muted", label=fmt.pct(start)),
        ),
        provenance={"finding": f["fingerprint"], "metric_id": f["metric"]},
        alt_text=(
            f"Top {n} customers' share of the {name.lower()}: {fmt.pct(now)} today, "
            f"{fmt.pct(start)} in {month}."
        ),
    )


def _cross(f: dict[str, Any], series: SeriesIndex, spec: dict[str, Any]) -> ChartSpec | None:
    sl = f["slots"]
    if "count" not in sl:
        return None
    from othor.engine.sweep_text import money

    n, noun = int(sl["count"]), sl.get("noun") or "records"
    matched = max(int(sl.get("checked", n)) - n, 0)
    total = n + matched
    rsrc = sl.get("right_source") or str(sl.get("right", "")).partition(" in ")[2]
    return ChartSpec(
        id=f"finding:{f['fingerprint']}",
        kind="bars_share",
        title=f"{noun.capitalize()} {sl.get('lag_days')}+ days old, with and without a match",
        unit=PCT,
        x=Axis("category"),
        y=YAxis(zero_based=True),
        categories=(
            Category(
                "No match",
                n / total if total else 1.0,
                "neutral",
                label=f"{fmt.num(n)} {noun} · {money(f['money'])}",
            ),
            Category(
                "Matched",
                matched / total if total else 0.0,
                "negative",
                label=fmt.num(matched),
            ),
        ),
        provenance={"finding": f["fingerprint"]},
        alt_text=(
            f"{fmt.num(n)} {noun} ({money(f['money'])}) have no match in {rsrc}; "
            f"{fmt.num(matched)} do."
        ),
    )


GAP_WEEKS = 10  # bars before the gap


def _missing(f: dict[str, Any], spec: dict[str, Any], probes: dict[str, Any]) -> ChartSpec | None:
    """One account gone quiet: its weekly invoice value up to its last invoice, then the gap."""
    name = f.get("breakdown_value")
    sl = f.get("slots") or {}
    if not name or f.get("state") == "measured" or "gap_days" not in sl:
        return None  # the group of silent accounts, or a paused account: no chart
    probe = (probes.get("entities") or {}).get(f["breakdown"]) or {}
    days: dict[dt.date, float] = {}
    for who, day, value in probe.get("events", []):
        if str(who) == name:
            d = dt.date.fromisoformat(day)
            days[d] = days.get(d, 0.0) + float(value)
    if not days:
        return None
    last = dt.date.fromisoformat(sl["last"])
    end = dt.date.fromisoformat(f["window"][1])
    last_week = last - dt.timedelta(days=last.weekday())  # the Monday of its last invoice
    weeks = [last_week - dt.timedelta(weeks=k) for k in range(GAP_WEEKS - 1, -1, -1)]
    pts = []
    for w in weeks:
        total = sum(v for d, v in days.items() if w <= d < w + dt.timedelta(days=7))
        pts.append((_iso(w), round(total, 2)))
    if sum(1 for _, v in pts if v > 0) < 4:
        return None
    gap_from = last_week + dt.timedelta(weeks=1)
    return ChartSpec(
        id=f"finding:{f['fingerprint']}",
        kind="gap_block",
        title=f"{name} · invoices, weekly",
        unit=_unit(spec, f["metric"]),
        period=Period("week", pts[0][0], _iso(end)),
        series=(Series("inv", "Invoices", "neutral", tuple(pts)),),
        annotations=(
            Annotation(
                "region", from_=_iso(gap_from), to=_iso(end), label="no invoices", style="dashed"
            ),
        ),
        provenance={"finding": f["fingerprint"], "metric_id": f["metric"]},
        alt_text=(
            f"{name}, weekly invoices from {fmt.day_month(weeks[0])}: none since "
            f"{fmt.day_month(last)}, {fmt.num(sl['gap_days'])} days."
        ),
    )


def _one(noun: str) -> str:
    return noun[:-1] if noun.endswith("s") else noun


_BY = {
    "rising_variance": _variance,
    "broken_relationship": _pair,
    "changepoint": _step,
    "net_hiding_gross": _nhg,
    "concentration": _concentration,
    "cross_source": _cross,
}
