# SPDX-License-Identifier: Apache-2.0
"""ETS forecasts for headline numbers (docs/04 §5, M7).

- statsmodels `ETSModel` (pinned), additive error, chosen by AICc among a fixed candidate set:
  trend none, additive, additive damped; additive seasonality only with two full cycles
  (12 for monthly series); weekly series carry no seasonality.
- Periods inside a known pattern's window are masked: their values are replaced by a straight
  line between the neighbouring periods before the fit, so a Diwali week or a plant shutdown
  never teaches the model (the same masking as baselines, docs/04 §4).
- An 80% interval. No band until the series has 8 weeks of history at the weekly grain (the
  8-week rule, back-filled weeks count); before that the point alone is stored.
- Deterministic: fixed starting values, a fixed iteration cap, results rounded to six decimals
  and stored as strings. The same series always gives the same bytes.

Pure and model-free: no `othor.ai`, no wall clock.
"""

from __future__ import annotations

import datetime as dt
import warnings
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import numpy as np

MIN_POINTS = 6  # fewer complete periods: no forecast at all
BAND_WEEKS = 8  # docs/04 §5: no band before 8 weeks of history
LEVEL = 0.8
MAXITER = 200
TRENDS: tuple[tuple[str | None, bool], ...] = ((None, False), ("add", False), ("add", True))
SEASON = {"month": 12, "quarter": 4}


@dataclass(frozen=True)
class Forecast:
    period: dt.date
    point: str
    lower: str | None
    upper: str | None


@dataclass(frozen=True)
class Fit:
    method: str
    params: dict[str, Any]
    aicc: str
    points: tuple[Forecast, ...]


def _s(v: float) -> str:
    return str(Decimal(repr(float(v))).quantize(Decimal("0.000001")))


def masked(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Masked periods replaced by a straight line between their unmasked neighbours."""
    out = values.astype(float).copy()
    idx = np.arange(len(out))
    keep = ~mask & np.isfinite(out)
    if keep.sum() < 2:
        return out
    out[~keep] = np.interp(idx[~keep], idx[keep], out[keep])
    return out


def next_periods(last: dt.date, grain: str, n: int) -> list[dt.date]:
    out: list[dt.date] = []
    d = last
    for _ in range(n):
        if grain == "week":
            d = d + dt.timedelta(weeks=1)
        elif grain == "month":
            d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        elif grain == "quarter":
            m = d.month + 3
            d = d.replace(year=d.year + (m - 1) // 12, month=(m - 1) % 12 + 1, day=1)
        else:
            d = d + dt.timedelta(days=1)
        out.append(d)
    return out


def fit(
    periods: list[dt.date],
    values: list[float],
    grain: str,
    horizon: int,
    *,
    mask: list[bool] | None = None,
    band: bool = True,
) -> Fit | None:
    """The best candidate by AICc, forecasting `horizon` periods after the last one."""
    from scipy import stats
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel

    y = np.asarray(values, dtype=float)
    if len(y) < MIN_POINTS or np.isfinite(y).sum() < MIN_POINTS:
        return None
    y = masked(y, np.asarray(mask if mask is not None else [False] * len(y), dtype=bool))
    if not np.isfinite(y).all():
        return None
    season = SEASON.get(grain)
    seasonal = season is not None and len(y) >= 2 * season
    best: tuple[float, Any, str] | None = None
    for trend, damped in TRENDS:
        if trend is None and damped:
            continue
        try:
            model = ETSModel(
                y,
                error="add",
                trend=trend,
                damped_trend=damped,
                seasonal="add" if seasonal else None,
                seasonal_periods=season if seasonal else None,
                initialization_method="heuristic" if seasonal else "estimated",
            )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                res = model.fit(disp=False, maxiter=MAXITER)
        except (ValueError, np.linalg.LinAlgError):
            continue
        aicc = float(res.aicc)
        if not np.isfinite(aicc):
            continue
        name = (
            "ETS(A,"
            + ("N" if trend is None else "Ad" if damped else "A")
            + (",A)" if seasonal else ",N)")
        )
        if best is None or aicc < best[0] - 1e-9:
            best = (aicc, res, name)
    if best is None:
        return None
    aicc, res, name = best
    pred = res.forecast(horizon)
    sigma = float(np.sqrt(res.mse)) if np.isfinite(res.mse) else 0.0
    z = float(stats.norm.ppf(0.5 + LEVEL / 2))
    out = []
    for h, (p, v) in enumerate(zip(next_periods(periods[-1], grain, horizon), pred, strict=True)):
        # the interval widens with the horizon (random-walk growth of the one-step error)
        width = z * sigma * float(np.sqrt(h + 1))
        out.append(
            Forecast(p, _s(v), _s(v - width) if band else None, _s(v + width) if band else None)
        )
    params = {"sigma": _s(sigma), "n": len(y), "seasonal": seasonal}
    return Fit(name, params, _s(aicc), tuple(out))


# --- v0.19 (M19.1): one named candidate at a time, and the naive baselines ---------------------

NAMED: dict[str, tuple[str | None, bool, bool]] = {  # name -> (trend, damped, seasonal)
    "ETS(A,N,N)": (None, False, False),
    "ETS(A,A,N)": ("add", False, False),
    "ETS(A,Ad,N)": ("add", True, False),
    "ETS(A,N,A)": (None, False, True),
    "ETS(A,Ad,A)": ("add", True, True),
}


def candidates(grain: str, n: int) -> list[str]:
    """The ETS candidates for a series of `n` periods, in a fixed order; the seasonal ones only
    with two full cycles."""
    season = SEASON.get(grain)
    out = ["ETS(A,N,N)", "ETS(A,A,N)", "ETS(A,Ad,N)"]
    if season is not None and n >= 2 * season:
        out += ["ETS(A,N,A)", "ETS(A,Ad,A)"]
    return out


def baseline_name(grain: str, n: int) -> str:
    season = SEASON.get(grain)
    return "seasonal-naive" if season is not None and n >= season + 1 else "naive"


def forecast_named(name: str, y: np.ndarray, grain: str, horizon: int) -> np.ndarray | None:
    """`horizon` forecasts of one named method fitted on `y` (already masked), or None when it
    can't be fitted. Deterministic: the fixed starting values and iteration cap of `fit`."""
    if not np.isfinite(y).all() or len(y) < 2:
        return None
    if name == "naive":
        return np.repeat(y[-1], horizon).astype(float)
    if name == "seasonal-naive":
        cycle = SEASON[grain]
        if len(y) < cycle:
            return None
        return np.array([y[len(y) - cycle + (h % cycle)] for h in range(horizon)], dtype=float)
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel

    trend, damped, seasonal = NAMED[name]
    season = SEASON.get(grain)
    if seasonal and (season is None or len(y) < 2 * season):
        return None
    if len(y) < MIN_POINTS:
        return None
    try:
        model = ETSModel(
            y,
            error="add",
            trend=trend,
            damped_trend=damped,
            seasonal="add" if seasonal else None,
            seasonal_periods=season if seasonal else None,
            initialization_method="heuristic" if seasonal else "estimated",
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = model.fit(disp=False, maxiter=MAXITER)
        pred = np.asarray(res.forecast(horizon), dtype=float)
    except (ValueError, np.linalg.LinAlgError):
        return None
    return pred if np.isfinite(pred).all() else None
