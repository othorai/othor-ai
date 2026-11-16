# SPDX-License-Identifier: Apache-2.0
"""The method chosen by its own track record (v0.19, M19.1).

For each of the last `BACKTEST` complete periods, every candidate is fitted on the data before it
and forecasts that one period (a rolling origin). The candidate with the lowest mean absolute
error wins, and it must beat the plain baseline (seasonal-naive when a full cycle exists, else the
last value) by at least `BEAT`, or the baseline is used. Periods inside a known pattern are left
out of the scoring (and masked in every fit, as before). Ties go to the earlier candidate in the
fixed order. Pure and deterministic: no model, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np

from othor.engine.forecast.ets import MIN_POINTS, baseline_name, candidates, forecast_named, masked

BACKTEST = 12  # origins: the last 12 complete periods
BEAT = 0.05  # a candidate must beat the baseline's error by at least 5%
MIN_ORIGINS = 4  # fewer scored origins: too little history to test a method


def q(v: float, places: str = "0.000001") -> str:
    return str(Decimal(repr(float(v))).quantize(Decimal(places)))


@dataclass(frozen=True)
class Selection:
    method: str
    baseline: str
    scores: dict[str, dict[str, Any]]  # name -> {"mae", "mape", "n"}
    errors: tuple[float, ...]  # the chosen method's one-step errors (forecast - actual)
    actuals: tuple[float, ...] = field(default=())
    origins: tuple[int, ...] = field(default=())  # the scored periods' indexes


def backtest(
    values: list[float], grain: str, mask: list[bool] | None = None
) -> dict[str, list[tuple[int, float]]]:
    """Each method's one-step forecasts at each scored origin: name -> [(origin, forecast)]."""
    y = np.asarray(values, dtype=float)
    m = np.asarray(mask if mask is not None else [False] * len(y), dtype=bool)
    n = len(y)
    names = [*candidates(grain, n), baseline_name(grain, n)]
    out: dict[str, list[tuple[int, float]]] = {name: [] for name in names}
    for i in range(max(n - BACKTEST, MIN_POINTS), n):
        if m[i] or not np.isfinite(y[i]):
            continue  # a known-pattern period isn't scored
        train = masked(y[:i], m[:i])
        for name in names:
            got = forecast_named(name, train, grain, 1)
            if got is not None:
                out[name].append((i, float(got[0])))
    return out


def select(values: list[float], grain: str, mask: list[bool] | None = None) -> Selection | None:
    """The chosen method and every candidate's score; None with too little history to test."""
    y = np.asarray(values, dtype=float)
    base = baseline_name(grain, len(y))
    runs = backtest(values, grain, mask)
    # score every method on the same origins: the ones where the baseline and it both forecast
    origins = {i for i, _ in runs[base]}
    if len(origins) < MIN_ORIGINS:
        return None
    scores: dict[str, dict[str, Any]] = {}
    errs: dict[str, list[float]] = {}
    for name, got in runs.items():
        if {i for i, _ in got} != origins:
            continue  # a method that failed on any origin is out
        e = [f - y[i] for i, f in sorted(got)]
        a = [y[i] for i, _ in sorted(got)]
        mae = float(np.mean(np.abs(e)))
        pct = [abs(x) / abs(v) for x, v in zip(e, a, strict=True) if v]
        scores[name] = {
            "mae": q(mae),
            "mape": q(float(np.mean(pct)) if pct else 0.0, "0.0001"),
            "n": len(e),
        }
        errs[name] = e
    if base not in scores:
        return None
    best = base
    for name in candidates(grain, len(y)):
        if name in scores and Decimal(scores[name]["mae"]) < Decimal(scores[best]["mae"]):
            best = name
    if best != base and Decimal(scores[best]["mae"]) > Decimal(scores[base]["mae"]) * Decimal(
        str(1 - BEAT)
    ):
        best = base  # not clearly better than the plain baseline
    actuals = tuple(float(y[i]) for i in sorted(origins))
    return Selection(best, base, scores, tuple(errs[best]), actuals, tuple(sorted(origins)))
