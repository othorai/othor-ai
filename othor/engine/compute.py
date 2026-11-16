# SPDX-License-Identifier: Apache-2.0
"""Inputs → outputs, with no I/O beyond DuckDB in memory: the part of a run that replay
re-executes (docs/02 "Determinism and replay").

1. the breakdown cap (top N by size, ties by name, the rest summed as "Other" when additive);
2. snapshot roll-ups: the last daily value in each week and month, so back-filled Sundays and
   daily snapshots form one continuous weekly series (5 Nov 2026);
3. derived metrics, in the spec's order, from the `series` table the compiler's SQL reads;
4. completeness and staleness of every point;
5. baselines per total series: history, the last 12 complete periods' mean and spread;
6. Home question candidates from rule checks (unmapped item-code prefixes).

Everything is Decimal and sorted; the outputs document is canonical JSON.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from typing import Any

from othor.engine import periods
from othor.engine.store import OUTPUT_FIELDS, Point, Q, dec, hash_points, sort_points
from othor.ontology.questions import unmapped_candidates

OUTPUTS_FORMAT = 1
ADDITIVE = ("sum", "count", "last")
BASELINE_PERIODS = 12
OTHER = "Other"


@dataclass
class Inputs:
    run_date: dt.date
    kind: str
    scope: dict[str, Any] | None
    spec: dict[str, Any]
    spec_hash: str
    sources: dict[str, dict[str, Any]]  # name → status, last_good, unmapped, hashes
    points: list[Point]


@dataclass
class Outputs:
    points: list[Point]
    baselines: dict[str, Any]
    questions: list[dict[str, Any]]
    doc: dict[str, Any] = field(default_factory=dict)


def compute(inp: Inputs) -> Outputs:
    spec = inp.spec
    base = [p for p in inp.points if p.period < inp.run_date]
    base = _exact_allocations(spec, base)
    base = _cap(spec, base)
    base += _rollups(spec, base, inp.run_date)
    derived = _derive(spec, base, inp.run_date)
    stale_sources = {n for n, m in inp.sources.items() if m.get("status") == "stale"}
    points = [_finish(spec, p, inp.run_date, stale_sources) for p in [*base, *derived]]
    points = sort_points(points)
    baselines = _baselines(spec, points, inp.run_date)
    questions = _questions(spec, inp, points)
    doc = {
        "format": OUTPUTS_FORMAT,
        "run_date": inp.run_date.isoformat(),
        "kind": inp.kind,
        "scope": inp.scope,
        "spec_hash": inp.spec_hash,
        "series": {
            "points": len(points),
            "series": len({(p.metric, p.grain, p.breakdown) for p in points}),
            "metrics": len({p.metric for p in points}),
            "hash": hash_points(points, OUTPUT_FIELDS),
        },
        "baselines": baselines,
        "questions": questions,
        "coverage": {
            "sources": {
                n: {"status": m.get("status"), "last_good": m.get("last_good")}
                for n, m in sorted(inp.sources.items())
            },
            "stale_metrics": sorted({p.metric for p in points if p.stale}),
        },
        "headline": _headline(spec, baselines),
    }
    return Outputs(points, baselines, questions, doc)


# --- 0. allocations add up exactly (decided 4 Nov) --------------------------------------------

PAISE = Decimal("0.01")


def _exact_allocations(spec: dict[str, Any], points: list[Point]) -> list[Point]:
    """An allocated breakdown (credit notes by plant) adds up to the metric's total exactly: each
    value rounded to the paise, the remainder on the largest one."""
    allocated = {(a["metric"], a["breakdown"]) for a in spec.get("allocations", [])}
    if not allocated:
        return points
    totals = {(p.metric, p.grain, p.period): p.value for p in points if not p.breakdown}
    groups: dict[tuple[str, str, str, dt.date], list[int]] = defaultdict(list)
    for i, p in enumerate(points):
        if (p.metric, p.breakdown) in allocated:
            groups[(p.metric, p.grain, p.breakdown, p.period)].append(i)
    out = list(points)
    for (metric, grain, _b, period), idx in groups.items():
        total = totals.get((metric, grain, period))
        if total is None:
            continue
        vals = [(out[i].value or Decimal(0)).quantize(PAISE) for i in idx]
        rest = total - sum(vals, Decimal(0))
        big = max(range(len(idx)), key=lambda j: (abs(vals[j]), out[idx[j]].breakdown_value or ""))
        vals[big] += rest
        for j, i in enumerate(idx):
            out[i] = out[i]._replace(value=vals[j].quantize(Q))
    return out


# --- 1. breakdown cap -------------------------------------------------------------------------


def _cap(spec: dict[str, Any], points: list[Point]) -> list[Point]:
    groups: dict[tuple[str, str], list[Point]] = defaultdict(list)
    out: list[Point] = []
    for p in points:
        if p.breakdown:
            groups[(p.metric, p.breakdown)].append(p)
        else:
            out.append(p)
    for (metric, bkey), pts in sorted(groups.items()):
        m = spec["metrics"].get(metric, {})
        cap = int(spec.get("breakdowns", {}).get(bkey, {}).get("cap", 50))
        values = {p.breakdown_value for p in pts}
        if len(values) <= cap:
            out += pts
            continue
        top = set(_top(m, pts, cap))
        out += [p for p in pts if p.breakdown_value in top]
        if m.get("agg") not in ADDITIVE:
            continue  # an average or a distinct count can't be summed into "Other"
        rest: dict[tuple[str, dt.date], list[Point]] = defaultdict(list)
        for p in pts:
            if p.breakdown_value not in top:
                rest[(p.grain, p.period)].append(p)
        for (grain, period), ps in sorted(rest.items()):
            vals = [p.value for p in ps if p.value is not None]
            out.append(
                Point(
                    metric,
                    grain,
                    bkey,
                    OTHER,
                    period,
                    sum(vals, Decimal(0)).quantize(Q) if vals else None,
                    any(p.reconstructed for p in ps),
                )
            )
    return out


def _top(m: dict[str, Any], pts: list[Point], cap: int) -> list[str | None]:
    """By size: snapshots at their latest day, flows over all months (or their finest grain)."""
    if m.get("kind") == "snapshot":
        last = max(p.period for p in pts)
        chosen = [p for p in pts if p.period == last]
    else:
        grains = {p.grain for p in pts}
        g = next(x for x in ("month", "week", "day") if x in grains)
        chosen = [p for p in pts if p.grain == g]
    size: dict[str | None, Decimal] = defaultdict(Decimal)
    for p in chosen:
        size[p.breakdown_value] += abs(p.value or Decimal(0))
    for p in pts:
        size.setdefault(p.breakdown_value, Decimal(0))
    ranked = sorted(size.items(), key=lambda kv: (-kv[1], kv[0] or ""))
    return [k for k, _ in ranked[:cap]]


# --- 2. snapshot roll-ups ---------------------------------------------------------------------


def _rollups(spec: dict[str, Any], points: list[Point], run_date: dt.date) -> list[Point]:
    snap = {
        k
        for k, m in spec["metrics"].items()
        if m.get("type") == "base" and m.get("kind") == "snapshot"
    }
    last: dict[tuple[str, str, str, str | None, dt.date], Point] = {}
    for p in points:
        if p.metric not in snap or p.grain != "day":
            continue
        for grain in ("week", "month"):
            k = (
                p.metric,
                grain,
                p.breakdown,
                p.breakdown_value,
                periods.period_start(p.period, grain),
            )
            if k not in last or last[k].period < p.period:
                last[k] = p
    return [
        Point(k[0], k[1], k[2], k[3], k[4], p.value, p.reconstructed)
        for k, p in sorted(
            last.items(),
            key=lambda kv: (*kv[0][:3], "" if kv[0][3] is None else "\x01" + kv[0][3], kv[0][4]),
        )
    ]


# --- 3. derived metrics -----------------------------------------------------------------------


def _derive(spec: dict[str, Any], points: list[Point], run_date: dt.date) -> list[Point]:
    import duckdb
    import pandas as pd

    derived_keys = [
        k
        for k in spec.get("order", [])
        if spec["metrics"].get(k, {}).get("type") == "derived"
        and spec["metrics"][k].get("status") == "active"
    ]
    if not derived_keys:
        return []
    recon = {p.key(): p.reconstructed for p in points if p.reconstructed}
    # a derived point needs every component's history: never before the latest first period
    first: dict[tuple[str, str], dt.date] = {}
    for p in points:
        k = (
            p.metric,
            "day" if spec["metrics"].get(p.metric, {}).get("kind") == "snapshot" else p.grain,
        )
        if k not in first or p.period < first[k]:
            first[k] = p.period
    con = duckdb.connect()
    out: list[Point] = []
    try:
        con.execute(
            "CREATE TABLE series (metric VARCHAR, grain VARCHAR, breakdown VARCHAR, "
            "breakdown_value VARCHAR, period DATE, value DECIMAL(24,6))"
        )

        def insert(pts: Iterable[Point]) -> None:
            rows = [
                (
                    p.metric,
                    p.grain,
                    p.breakdown,
                    p.breakdown_value,
                    p.period,
                    None if p.value is None else str(p.value),
                )
                for p in pts
            ]
            if not rows:
                return
            df = pd.DataFrame(
                rows, columns=["metric", "grain", "breakdown", "breakdown_value", "period", "value"]
            )
            df["value"] = df["value"].astype("string")
            con.register("incoming", df)
            con.execute(
                "INSERT INTO series SELECT metric, grain, breakdown, breakdown_value, "
                "CAST(period AS DATE), CAST(value AS DECIMAL(24,6)) FROM incoming"
            )
            con.unregister("incoming")

        insert(points)
        for key in derived_keys:
            m = spec["metrics"][key]
            comps = m.get("components", [])
            mine: list[Point] = []
            for qkey, sql in sorted(m.get("queries", {}).items()):
                grain, _, bkey = qkey.partition(":")
                starts = [
                    first.get(
                        (
                            c,
                            "day"
                            if spec["metrics"].get(c, {}).get("kind") == "snapshot"
                            else grain,
                        )
                    )
                    for c in comps
                ]
                start = max((d for d in starts if d is not None), default=None)
                if start is not None:
                    start = periods.period_start(start, grain)
                for r in con.execute(sql).fetchall():
                    period = _date(r[0])
                    if period >= run_date or (start is not None and period < start):
                        continue
                    bv = None if r[1] is None else str(r[1])
                    if bkey and bv is None:
                        continue
                    value = None if (len(r) > 3 and r[3] == "not_determinable") else dec(r[2])
                    rec = any(recon.get((c, grain, bkey, bv, period)) for c in comps)
                    mine.append(Point(key, grain, bkey, bv, period, value, rec))
            insert(mine)
            for p in mine:
                if p.reconstructed:
                    recon[p.key()] = True
                k = (p.metric, p.grain)
                if k not in first or p.period < first[k]:
                    first[k] = p.period
            out += mine
    finally:
        con.close()
    return out


# --- 4. completeness, staleness ---------------------------------------------------------------


def _sources_of(spec: dict[str, Any], metric: str) -> list[str]:
    m = spec["metrics"].get(metric, {})
    if m.get("type") == "base":
        return [m["source"]]
    return list(m.get("sources", []))


def _finish(spec: dict[str, Any], p: Point, run_date: dt.date, stale: set[str]) -> Point:
    m = spec["metrics"].get(p.metric, {})
    snapshot_day = m.get("kind") == "snapshot" and p.grain == "day"
    done = True if snapshot_day else periods.complete(p.period, p.grain, run_date)
    is_stale = any(s in stale for s in _sources_of(spec, p.metric))
    return p._replace(complete=done, stale=is_stale)


# --- 5. baselines -----------------------------------------------------------------------------


def _baselines(spec: dict[str, Any], points: list[Point], run_date: dt.date) -> dict[str, Any]:
    as_of = periods.as_of(run_date)
    series: dict[tuple[str, str], list[Point]] = defaultdict(list)
    for p in points:
        if not p.breakdown and p.complete and p.value is not None:
            series[(p.metric, p.grain)].append(p)
    out: dict[str, Any] = {}
    for (metric, grain), pts in sorted(series.items()):
        pts.sort(key=lambda p: p.period)
        recon = [p for p in pts if p.reconstructed]
        last = pts[-BASELINE_PERIODS:]
        vals = [p.value for p in last if p.value is not None]
        mean, sd = _mean_sd(vals)
        start = pts[0].period
        out[f"{metric}|{grain}"] = {
            "metric": metric,
            "grain": grain,
            "history_start": start.isoformat(),
            "history_days": (as_of - start).days + 1,
            "periods": len(pts),
            "reconstructed_to": recon[0].period.isoformat() if recon else None,
            "reconstructed_from": _recon_from(spec, metric) if recon else None,
            "mean": None if mean is None else str(mean),
            "sd": None if sd is None else str(sd),
            "last": {"period": pts[-1].period.isoformat(), "value": str(pts[-1].value)},
            "prev": (
                {"period": pts[-2].period.isoformat(), "value": str(pts[-2].value)}
                if len(pts) > 1
                else None
            ),
            "stale": pts[-1].stale,
        }
    return out


def _mean_sd(vals: list[Decimal]) -> tuple[Decimal | None, Decimal | None]:
    if not vals:
        return None, None
    with localcontext() as c:
        c.prec = 40
        n = Decimal(len(vals))
        mean = sum(vals, Decimal(0)) / n
        if len(vals) < 2:
            return mean.quantize(Q), None
        var = sum(((v - mean) ** 2 for v in vals), Decimal(0)) / (n - 1)
        return mean.quantize(Q), var.sqrt().quantize(Q)


def _recon_from(spec: dict[str, Any], metric: str, seen: set[str] | None = None) -> str | None:
    seen = seen or set()
    if metric in seen:
        return None
    seen.add(metric)
    m = spec["metrics"].get(metric, {})
    cols: list[str] = []
    if m.get("type") == "base":
        cols = list((m.get("backfill") or {}).get("as_of_columns", []))
    else:
        for c in m.get("components", []):
            r = _recon_from(spec, c, seen)
            if r:
                cols += r.split(", ")
    return ", ".join(sorted(set(cols))) or None


def _headline(spec: dict[str, Any], baselines: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for key in spec.get("forecast", {}).get("headline", []):
        m = spec["metrics"].get(key, {})
        grain = m.get("grain") or "month"
        b = baselines.get(f"{key}|{grain}")
        out.append(
            {
                "metric": key,
                "name": m.get("name") or key,
                "unit": m.get("unit"),
                "grain": grain,
                "baseline": f"{key}|{grain}" if b else None,
            }
        )
    return out


# --- 6. questions -----------------------------------------------------------------------------


def _questions(spec: dict[str, Any], inp: Inputs, points: list[Point]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for check in spec.get("unmapped_checks", []):
        meta = inp.sources.get(check["source"], {})
        if meta.get("status") != "read":
            continue
        unmapped = [
            (dt.date.fromisoformat(month), prefix, Decimal(value))
            for metric, month, prefix, value in meta.get("unmapped", [])
            if metric == check["metric"] and value != "None"
        ]
        totals = {
            p.period: p.value
            for p in points
            if p.metric == check["metric"]
            and p.grain == "month"
            and not p.breakdown
            and p.value is not None
        }
        out += unmapped_candidates(check, unmapped, totals)
    return sorted(out, key=lambda q: q["fingerprint"])


def _date(v: Any) -> dt.date:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])
