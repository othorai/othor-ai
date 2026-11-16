# SPDX-License-Identifier: Apache-2.0
"""The `forecast` step (M7, docs/04 §5): forecasts stored before their actuals. Pure: the run
and replay compute the same document from the run's outputs, spec and date.

- **Tonight's forecasts:** for each headline metric (spec `forecast.headline`: headline metrics
  and goals' metrics), at its own grain (day-grain snapshots at the week) and, for a flow, also
  weekly. Each forecasts the periods after its last complete one (one week; months through the
  latest goal date of the metric, at least one). They are written to `forecast` rows at seal,
  before any of those periods has an actual.
- **Fixture runs only (tests, decided 5 Nov 2026):** the forecasts earlier nightly runs would
  have stored, each fitted on the data before its period, tagged `reconstructed`. A real
  company never gets them: a forecast made after the outcome isn't a forecast.
- **Backtest (a company's first run):** how close one-step forecasts would have been on the
  last 12 periods, labelled a backtest ("Forecast quality"); never a breach.
- **Goals:** a fiscal-year total = actual to date + tonight's forecasts for the remaining
  months; a value goal = tonight's forecast for the goal's month. With a band from the
  intervals (approximate) and the gap to the target.
- **Weeks ahead (v0.12, the outlook):** each weekly series is fitted once for `AHEAD_WEEKS`
  weeks. Its first week is tonight's row as before (the forecast of record, unchanged); all
  four weeks go to their own document (`forecasts_ahead.json`), written at seal as `forecast`
  rows tagged with their horizon. The band widens with the horizon (z·σ·√h). Forecast breach
  never reads a row with a horizon over one.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import numpy as np

from othor.engine.detect import windows
from othor.engine.detectors.base import SeriesIndex
from othor.engine.forecast import abstain as abst
from othor.engine.forecast import band as fband
from othor.engine.forecast.ahead import known_ahead
from othor.engine.forecast.changes import changes
from othor.engine.forecast.confidence import track as track_of
from othor.engine.forecast.ets import BAND_WEEKS, Fit, Forecast, _s, fit, masked, next_periods
from othor.engine.forecast.goals_v2 import goals as goals_v2
from othor.engine.forecast.parts import drivers
from othor.engine.forecast.record import carry
from othor.engine.forecast.select import Selection, q, select
from othor.engine.store import Point

# v0.19 (M19.1): the method chosen by its backtest and calibrated P10/P50/P90, stored next to
# the forecast of record. Sweep's Forecast breach and the goal lines keep reading the forecast of
# record (`point`, `lower`, `upper`, unchanged: owner, 15 Nov 2026); What's ahead reads `v2`.
ENGINE = 2

AHEAD_WEEKS = 4  # the outlook (v0.12): weekly forecasts this many weeks ahead
AHEAD_FORMAT = 1
RECONSTRUCT = {"week": 16, "month": 4}  # fixture runs: earlier periods with a forecast of record
BACKTEST = 12


def grains(m: dict[str, Any]) -> list[str]:
    own = m.get("grain") or "month"
    out = {"week" if own == "day" else own}
    if m.get("kind", "flow") == "flow":
        out.add("week")
    return sorted(out)


def _mask(spec: dict[str, Any], metric: str, periods: list[dt.date], grain: str) -> list[bool]:
    """Periods touched by a known pattern on this metric (or on every metric), masked."""
    if not periods:
        return []
    years = range(periods[0].year - 1, periods[-1].year + 2)
    spans: list[tuple[dt.date, dt.date]] = []
    for p in spec.get("suppressions", []):
        if p.get("confirmed", True) is False:
            continue
        ms = p.get("metrics") or []
        if ms and metric not in ms:
            continue
        settle = dt.timedelta(days=int(p.get("settle_days") or 0))
        spans += [(a, b + settle) for a, b in windows(p, years)]
    # A window masks only periods no longer than itself: a one-week Diwali or shutdown window
    # masks its weeks, never a whole month it merely overlaps (the month keeps its seasonal
    # shape; a long period is covered only when the window explains it, docs/04 §4).
    out = []
    for d in periods:
        end = next_periods(d, grain, 1)[0] - dt.timedelta(days=1)
        out.append(any(a <= end and d <= b and (end - d) <= (b - a) for a, b in spans))
    return out


def _series(
    index: SeriesIndex, metric: str, grain: str, before: dt.date | None = None
) -> tuple[list[dt.date], list[float]]:
    s = index.get(metric, grain)
    if s is None:
        return [], []
    pairs = [
        (p, float(v))
        for p, v in zip(s.periods, s.values, strict=True)
        if v == v and (before is None or p < before)
    ]
    return [p for p, _ in pairs], [v for _, v in pairs]


def _weeks(index: SeriesIndex, metric: str) -> int:
    return len(_series(index, metric, "week")[0])


def _horizon(spec: dict[str, Any], metric: str, grain: str, last: dt.date) -> int:
    if grain != "month":
        return 1
    bys = [
        dt.date.fromisoformat(str(g["by"]))
        for g in spec.get("forecast", {}).get("goals", [])
        if g.get("metric") == metric
    ]
    n = 1
    for by in bys:
        months = (by.year - last.year) * 12 + (by.month - last.month)
        n = max(n, months)
    return n


def forecast_series(
    periods: list[dt.date],
    values: list[float],
    grain: str,
    horizon: int,
    *,
    mask: list[bool],
    band: bool,
    per_day: bool = False,
) -> tuple[Fit | None, Selection | None]:
    """v0.19 (M19.1): the method its backtest chose, P50 from it fitted on the whole series, and
    the calibrated 80% band from its out-of-sample errors (never narrower than the fitted model's
    own error). None when the series is too short to test a method.
    `per_day` (M19.2, monthly flows): the series is forecast as a daily rate (value ÷ days in the
    month) and scaled back, so a short February isn't over-forecast; errors are in the original
    units."""
    import numpy as np

    from othor.engine.forecast.ets import forecast_named

    scale = [float(_days(p, grain)) if per_day else 1.0 for p in periods]
    vals = [v / s for v, s in zip(values, scale, strict=True)]
    sel = select(vals, grain, mask)
    if sel is None:
        return None, None
    y = masked(np.asarray(vals, dtype=float), np.asarray(mask, dtype=bool))
    pred = forecast_named(sel.method, y, grain, horizon)
    if pred is None:
        return None, sel
    errs = [e * scale[i] for e, i in zip(sel.errors, sel.origins, strict=True)]
    # σ: the backtest's real misses, but never under the fitted model's own error (a series with
    # few periods to test, like a young weekly number, would otherwise get an overconfident band)
    s_back = fband.sigma(errs)
    old = fit(periods, values, grain, 1, mask=mask, band=True)
    s_fit = float(old.params["sigma"]) if old is not None else 0.0
    s = max(s_back, s_fit)
    k, inside, n = fband.calibrate(errs)
    out = []
    future = next_periods(periods[-1], grain, horizon)
    for h, (p, v) in enumerate(zip(future, pred, strict=True)):
        v = v * (_days(p, grain) if per_day else 1.0)
        w = fband.half_width(s, k, h + 1)
        out.append(Forecast(p, _s(v), _s(v - w) if band else None, _s(v + w) if band else None))
    params = {
        "per_day": per_day,
        "mae": q(float(np.mean(np.abs(errs)))),
        "engine": ENGINE,
        "n": len(values),
        "baseline": sel.baseline,
        "method_scores": sel.scores,
        "sigma": _s(s),
        "sigma_source": "backtest" if s_back >= s_fit else "fit",
        "sigma_backtest": _s(s_back),
        "sigma_fit": _s(s_fit),
        "calibration": {"k": q(k, "0.1"), "inside": inside, "of": n},
    }
    return Fit(sel.method, params, "", tuple(out)), sel


def _days(d: dt.date, grain: str) -> int:
    return (next_periods(d, grain, 1)[0] - d).days


def best_series(
    periods: list[dt.date],
    values: list[float],
    grain: str,
    horizon: int,
    *,
    mask: list[bool],
    band: bool,
    flow: bool,
) -> tuple[Fit | None, Selection | None]:
    """The plain v2 forecast, or for a monthly flow the per-day one when it beats the plain one's
    backtest error by the same 5% margin (M19.2, days in the month)."""
    from othor.engine.forecast.select import BEAT

    plain = forecast_series(periods, values, grain, horizon, mask=mask, band=band)
    if grain != "month" or not flow or plain[0] is None:
        return plain
    daily = forecast_series(periods, values, grain, horizon, mask=mask, band=band, per_day=True)
    if daily[0] is None:
        return plain
    if float(daily[0].params["mae"]) <= float(plain[0].params["mae"]) * (1 - BEAT):
        return daily
    return plain


def _scaled(f: Forecast, e: float) -> Forecast:
    return Forecast(
        f.period,
        _s(float(f.point) * e),
        None if f.lower is None else _s(float(f.lower) * e),
        None if f.upper is None else _s(float(f.upper) * e),
    )


def _v2(f: Forecast | None) -> dict[str, Any] | None:
    if f is None:
        return None
    return {"p10": f.lower, "p50": f.point, "p90": f.upper}


def _row(
    metric: str,
    grain: str,
    f: Any,
    method: str,
    aicc: str,
    params: dict[str, Any],
    as_of: dt.date,
    reconstructed: bool,
) -> dict[str, Any]:
    return {
        "series": f"{metric}|{grain}",
        "metric": metric,
        "grain": grain,
        "period": f.period.isoformat(),
        "point": f.point,
        "lower": f.lower,
        "upper": f.upper,
        "method": method,
        "aicc": aicc,
        "params": params,
        "as_of": as_of.isoformat(),
        "reconstructed": reconstructed,
    }


def run(
    spec: dict[str, Any],
    run_date: dt.date,
    outputs: list[Point],
    *,
    kind: str,
    first: bool,
) -> dict[str, Any]:
    return tonight(spec, run_date, outputs, kind=kind, first=first)[0]


def tonight(
    spec: dict[str, Any],
    run_date: dt.date,
    outputs: list[Point],
    *,
    kind: str,
    first: bool,
    prev: dict[str, Any] | None = None,
    also: tuple[str, ...] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Tonight's forecasts document and the weeks-ahead document (v0.12): each weekly series is
    fitted once, for `AHEAD_WEEKS` weeks; its first week is the row it always was.

    The numbers forecast: the spec's (the Ontology's headline numbers and every goal's), then
    `also`: the active briefs' headline numbers from the run's context (v0.19 fix, 15 Nov 2026),
    so every number a brief's What's ahead shows has a forecast, even when the Ontology marks
    no headline number. The context is a sealed input, so replay forecasts the same numbers."""
    index = SeriesIndex(outputs)
    as_of = run_date - dt.timedelta(days=1)
    rows: list[dict[str, Any]] = []
    ahead: list[dict[str, Any]] = []
    quality: dict[str, Any] = {}
    waiting: list[str] = []
    selection: dict[str, Any] = {}
    abstain: dict[str, str] = {}
    known: dict[str, Any] = {}
    parts: dict[str, Any] = {}
    last_of: dict[str, str] = {}
    month_v2: dict[str, list[dict[str, Any]]] = {}
    month_actuals: dict[str, list[tuple[dt.date, float]]] = {}
    listed = list(spec.get("forecast", {}).get("headline", []))
    listed += sorted(set(also) - set(listed))
    definitions = _definitions(spec, run_date, prev, listed)
    v2_points: dict[str, list[dict[str, Any]]] = {}
    complete: dict[str, dict[str, float]] = {}
    for metric in listed:
        m = spec.get("metrics", {}).get(metric) or {}
        if m.get("status") != "active":
            continue
        band = _weeks(index, metric) >= BAND_WEEKS
        if not band:
            waiting.append(metric)
        for grain in grains(m):
            periods, values = _series(index, metric, grain)
            if not periods:
                continue
            horizon = _horizon(spec, metric, grain, periods[-1])
            got = fit(
                periods,
                values,
                grain,
                max(horizon, AHEAD_WEEKS) if grain == "week" else horizon,
                mask=_mask(spec, metric, periods, grain),
                band=band,
            )
            n_ahead = max(horizon, AHEAD_WEEKS) if grain == "week" else horizon
            mask = _mask(spec, metric, periods, grain)
            got2, sel = best_series(
                periods,
                values,
                grain,
                n_ahead,
                mask=mask,
                band=band,
                flow=m.get("kind", "flow") == "flow",
            )
            series_key = f"{metric}|{grain}"
            last_of[series_key] = periods[-1].isoformat()
            if got2 is not None:
                # what's known ahead (M19.2): a confirmed pattern's measured effect on the periods
                # it touches; named without an effect when it hasn't been seen twice yet
                ka = known_ahead(
                    spec, metric, grain, periods, values, [f.period for f in got2.points]
                )
                if ka:
                    known[series_key] = ka
                    pts = []
                    for f in got2.points:
                        e = 1.0
                        for pat in ka.get(f.period.isoformat(), []):
                            if pat["effect"] is not None:
                                e *= float(pat["effect"])
                        pts.append(_scaled(f, e) if e != 1.0 else f)
                    got2 = Fit(got2.method, got2.params, got2.aicc, tuple(pts))
            v2 = {f.period: f for f in got2.points} if got2 is not None else {}
            v2_points[series_key] = [
                {"period": f.period.isoformat(), **(_v2(f) or {})} for f in v2.values()
            ]
            complete[series_key] = {p.isoformat(): v for p, v in zip(periods, values, strict=True)}
            if grain == "month":
                month_actuals[metric] = list(zip(periods, values, strict=True))
                month_v2[metric] = [
                    {"period": f.period.isoformat(), **(_v2(f) or {})} for f in v2.values()
                ]
            if got2 is not None and m.get("terms"):
                # the total's typical level last period: its masked baseline when a known pattern
                # (Diwali week) touched it, so the pattern ending isn't read as a driver
                typical = masked(np.asarray(values, dtype=float), np.asarray(mask, dtype=bool))
                d = _parts(spec, index, metric, grain, m["terms"], got2, float(typical[-1]))
                if d:
                    parts[series_key] = d
            if got2 is not None and sel is not None:
                selection[f"{metric}|{grain}"] = _selection(got2, sel)
                f1 = got2.points[0]
                why = abst.reason(
                    weeks=_weeks(index, metric),
                    band_weeks=BAND_WEEKS,
                    stale_after_last=_stale_after(outputs, metric, grain, periods[-1]),
                    grain=grain,
                    p50=float(f1.point),
                    lower=None if f1.lower is None else float(f1.lower),
                    upper=None if f1.upper is None else float(f1.upper),
                )
                why = why or _definition_abstain(definitions.get(metric), periods)
                if why:
                    abstain[f"{metric}|{grain}"] = why
            elif not band:
                abstain[f"{metric}|{grain}"] = "short_history"
            if got is not None:
                rows += [
                    {
                        **_row(metric, grain, f, got.method, got.aicc, got.params, as_of, False),
                        "v2": _v2(v2.get(f.period)),
                    }
                    for f in got.points[:horizon]
                ]
                if grain == "week":
                    ahead += [
                        {
                            **_row(
                                metric, grain, f, got.method, got.aicc, got.params, as_of, False
                            ),
                            "horizon": h,
                            "v2": _v2(v2.get(f.period)),
                        }
                        for h, f in enumerate(got.points[:AHEAD_WEEKS], start=1)
                    ]
            if kind == "fixture":
                rows += _reconstructed(spec, index, metric, grain, periods)
            if first and grain == (m.get("grain") or "month"):
                q = _backtest(spec, index, metric, grain, periods, values)
                if q:
                    quality[f"{metric}|{grain}"] = q
    rows.sort(key=lambda r: (r["series"], r["period"], r["as_of"]))
    ahead.sort(key=lambda r: (r["series"], r["horizon"]))
    doc = {
        "rows": rows,
        "goals": goals(spec, index, rows),
        "quality": quality,
        "waiting": sorted(waiting),
        # v0.19 (M19.1): the backtest that chose each v2 method, and when v2 says nothing
        "engine": ENGINE,
        "selection": dict(sorted(selection.items())),
        "abstain": dict(sorted(abstain.items())),
        # v0.19 (M19.2): goals' chances, what drives a total, what's known ahead, what changed
        "goals_v2": goals_v2(spec, month_actuals, month_v2, abstain),
        "parts": dict(sorted(parts.items())),
        "known_ahead": dict(sorted(known.items())),
        "definitions": definitions,
        "inputs": _inputs(spec, last_of),
    }
    doc["changes"] = changes(doc, prev)
    # v0.19 (M19.3): the real track record, carried forward, and how sure each forecast is
    record, pending = carry(prev, v2_points, complete, run_date)
    doc["record"], doc["pending"] = record, pending
    doc["track"] = {
        s: t
        for s in sorted(set(selection) | set(record))
        if s not in abstain and (t := track_of(record.get(s, []), selection.get(s))) is not None
    }
    return doc, {"format": AHEAD_FORMAT, "weeks": AHEAD_WEEKS, "rows": ahead}


def _reconstructed(
    spec: dict[str, Any], index: SeriesIndex, metric: str, grain: str, periods: list[dt.date]
) -> list[dict[str, Any]]:
    """Fixture runs only: as of each recent past period, the forecast an earlier run would
    have stored for it, fitted on the data before it."""
    out = []
    # the recent complete periods and the one running now, each as of its own start
    targets = [*periods[-RECONSTRUCT.get(grain, 4) :], *next_periods(periods[-1], grain, 1)]
    for target in targets:
        ps, vs = _series(index, metric, grain, before=target)
        band = len([p for p in _series(index, metric, "week", before=target)[0]]) >= BAND_WEEKS
        got = fit(ps, vs, grain, 1, mask=_mask(spec, metric, ps, grain), band=band) if ps else None
        if got is None or got.points[0].period != target:
            continue
        out.append(
            _row(
                metric,
                grain,
                got.points[0],
                got.method,
                got.aicc,
                got.params,
                target - dt.timedelta(days=1),
                True,
            )
        )
    return out


def _backtest(
    spec: dict[str, Any],
    index: SeriesIndex,
    metric: str,
    grain: str,
    periods: list[dt.date],
    values: list[float],
) -> dict[str, Any] | None:
    """One-step forecasts on the last 12 periods, each fitted on the data before it: the mean
    absolute % error and how often the actual fell inside the band. A backtest, labelled."""
    errs, inside, n = [], 0, 0
    for i in range(max(len(periods) - BACKTEST, 0), len(periods)):
        got = (
            fit(periods[:i], values[:i], grain, 1, mask=_mask(spec, metric, periods[:i], grain))
            if i >= 6
            else None
        )
        if got is None:
            continue
        f = got.points[0]
        actual = values[i]
        n += 1
        if actual:
            errs.append(abs(float(f.point) - actual) / abs(actual))
        if f.lower is not None and float(f.lower) <= actual <= float(f.upper or 0):
            inside += 1
    if not n:
        return None
    mape = sum(errs) / len(errs) if errs else 0.0
    return {
        "periods": n,
        "mape": str(Decimal(repr(mape)).quantize(Decimal("0.0001"))),
        "inside_band": inside,
        "label": "backtest",
    }


def goals(
    spec: dict[str, Any], index: SeriesIndex, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out = []
    for g in spec.get("forecast", {}).get("goals", []):
        metric = g.get("metric")
        by = dt.date.fromisoformat(str(g["by"]))
        target = Decimal(str(g["target"]))
        tonight = {
            r["period"]: r
            for r in rows
            if r["metric"] == metric and r["grain"] == "month" and not r["reconstructed"]
        }
        if g.get("aggregate") == "fiscal_year_total":
            start = by.replace(year=by.year - 1) + dt.timedelta(days=1)
            ps, vs = _series(index, metric, "month")
            actual = sum(
                (Decimal(repr(v)) for p, v in zip(ps, vs, strict=True) if start <= p <= by),
                Decimal(0),
            )
            last = max((p for p in ps if p <= by), default=None)
            months = (
                next_periods(last, "month", (by.year - last.year) * 12 + by.month - last.month)
                if last
                else []
            )
            found = [tonight.get(p.isoformat()) for p in months if p <= by]
            ahead = [r for r in found if r is not None]
            if not ahead or len(ahead) != len(found):
                continue
            projection = actual + sum((Decimal(r["point"]) for r in ahead), Decimal(0))
            has_band = all(r["lower"] is not None for r in ahead)
            lower = (
                actual + sum((Decimal(r["lower"]) for r in ahead), Decimal(0)) if has_band else None
            )
            upper = (
                actual + sum((Decimal(r["upper"]) for r in ahead), Decimal(0)) if has_band else None
            )
            to_date = actual
        else:
            r = tonight.get(by.replace(day=1).isoformat())
            if r is None:
                continue
            projection = Decimal(r["point"])
            lower = Decimal(r["lower"]) if r["lower"] is not None else None
            upper = Decimal(r["upper"]) if r["upper"] is not None else None
            to_date = None
        short = (
            target - projection
            if g.get("direction", "at_least") == "at_least"
            else projection - target
        )
        out.append(
            {
                "key": g["key"],
                "name": g.get("name", ""),
                "metric": metric,
                "aggregate": g.get("aggregate", "value"),
                "direction": g.get("direction", "at_least"),
                "target": str(target),
                "by": by.isoformat(),
                "to_date": None if to_date is None else str(to_date.quantize(Decimal("0.01"))),
                "projection": str(projection.quantize(Decimal("0.01"))),
                "lower": None if lower is None else str(lower.quantize(Decimal("0.01"))),
                "upper": None if upper is None else str(upper.quantize(Decimal("0.01"))),
                "short_by": str(short.quantize(Decimal("0.01"))),
                "on_track": short <= 0,
            }
        )
    return out


def _selection(got: Fit, sel: Selection) -> dict[str, Any]:
    """The v2 choice for one series, every night: what M19.3's track record and v0.22's
    forecast quality tab read. Labelled a backtest, never a breach."""
    return {
        "label": "backtest",
        "method": sel.method,
        "baseline": sel.baseline,
        "periods": sel.scores[sel.method]["n"],
        # in the number's own units (a per-day choice's scores are per day: `per_day`)
        "mae": got.params["mae"],
        "mape": sel.scores[sel.method]["mape"],
        "baseline_mae": sel.scores[sel.baseline]["mae"],
        "per_day": got.params["per_day"],
        **{k: got.params[k] for k in ("sigma", "sigma_source", "calibration", "method_scores")},
    }


def _stale_after(outputs: list[Point], metric: str, grain: str, last: dt.date) -> bool:
    """The source behind this number wasn't updated: a stale point newer than the last good one."""
    return any(
        p.stale
        and p.metric == metric
        and p.grain == grain
        and p.breakdown == ""
        and p.period > last
        for p in outputs
    )


DEFINITION_PERIODS = 4  # a definition changed within this many periods: say nothing yet


def _definitions(
    spec: dict[str, Any],
    run_date: dt.date,
    prev: dict[str, Any] | None,
    listed: list[str] | None = None,
) -> dict[str, Any]:
    """Each headline metric's definition hash and the date it was first seen, carried forward
    from the previous run (M19.2); unknown (None) when there's no earlier run to compare with."""
    before = (prev or {}).get("definitions", {})
    out = {}
    names = spec.get("forecast", {}).get("headline", []) if listed is None else listed
    for metric in names:
        h = (spec.get("metrics", {}).get(metric) or {}).get("definition_hash")
        old = before.get(metric) or {}
        if prev is None or not old:
            since = old.get("since") if old.get("hash") == h else None
        elif old.get("hash") == h:
            since = old.get("since")
        else:
            since = run_date.isoformat()
        out[metric] = {"hash": h, "since": since}
    return out


def _definition_abstain(d: dict[str, Any] | None, periods: list[dt.date]) -> str | None:
    if not d or not d.get("since"):
        return None
    since = dt.date.fromisoformat(d["since"])
    after = [p for p in periods if p >= since]
    return "definition_changed" if len(after) < DEFINITION_PERIODS else None


def _inputs(spec: dict[str, Any], last_of: dict[str, str]) -> dict[str, Any]:
    """What a forecast depends on, hashed, so tomorrow's run can say what changed."""
    import hashlib
    import json

    def h(x: Any) -> str:
        return hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()[:16]

    goals_by: dict[str, list[Any]] = {}
    for g in spec.get("forecast", {}).get("goals", []):
        goals_by.setdefault(g.get("metric"), []).append(g)
    return {
        "last": dict(sorted(last_of.items())),
        "patterns": h(spec.get("suppressions", [])),
        "goals": {k: h(v) for k, v in sorted(goals_by.items())},
    }


def _parts(
    spec: dict[str, Any],
    index: SeriesIndex,
    metric: str,
    grain: str,
    terms: list[dict[str, Any]],
    total: Fit,
    total_last: float,
) -> dict[str, Any] | None:
    """Each signed part's v2 forecast for the next period, reconciled to the total (M19.2)."""
    rows = []
    for t in terms:
        ps, vs = _series(index, t["metric"], grain)
        if not ps:
            return None
        got, _sel = forecast_series(
            ps, vs, grain, 1, mask=_mask(spec, t["metric"], ps, grain), band=False
        )
        if got is None:
            return None
        typical = masked(
            np.asarray(vs, dtype=float),
            np.asarray(_mask(spec, t["metric"], ps, grain), dtype=bool),
        )
        rows.append((t["metric"], int(t["sign"]), float(got.points[0].point), float(typical[-1])))
    names = {r[0]: str((spec.get("metrics", {}).get(r[0]) or {}).get("name") or r[0]) for r in rows}
    first = total.points[0]
    return {
        "period": first.period.isoformat(),
        **drivers(float(first.point), total_last, rows, names),
    }
