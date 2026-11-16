# SPDX-License-Identifier: Apache-2.0
"""How sure a forecast is (v0.19, M19.3): the track record summed up, and a plain level.

- The real record once a series has `MIN_REAL` scored forecasts; until then the backtest,
  labelled as such.
- high: at least 75% inside the band and a typical error of 10% or less; low: under 60% inside,
  or a typical error over 25%; medium otherwise. (M19.4 turns the level into a word.)
Pure: no model, no clock.
"""

from __future__ import annotations

import statistics
from typing import Any

MIN_REAL = 6
HIGH_INSIDE, HIGH_ERROR = 0.75, 10.0
LOW_INSIDE, LOW_ERROR = 0.60, 25.0


def level(inside_share: float, typical_error_pct: float | None) -> str:
    err = typical_error_pct if typical_error_pct is not None else 0.0
    if inside_share < LOW_INSIDE or err > LOW_ERROR:
        return "low"
    if inside_share >= HIGH_INSIDE and err <= HIGH_ERROR:
        return "high"
    return "medium"


def track(record: list[dict[str, Any]], selection: dict[str, Any] | None) -> dict[str, Any] | None:
    """The summary for one series: the real record when long enough, else the backtest."""
    out: dict[str, Any]
    if len(record) >= MIN_REAL:
        errs = [float(r["error_pct"]) for r in record if r.get("error_pct") is not None]
        n, inside = len(record), sum(1 for r in record if r["inside"])
        typical = round(statistics.median(errs), 2) if errs else None
        last = record[-1]
        out = {
            "source": "real",
            "n": n,
            "inside": inside,
            "typical_error_pct": None if typical is None else f"{typical:.2f}",
            "last": {k: last[k] for k in ("period", "p10", "p50", "p90", "actual", "inside")},
        }
    elif selection:
        cal = selection.get("calibration") or {}
        n, inside = int(cal.get("of") or 0), int(cal.get("inside") or 0)
        if not n:
            return None
        typical = round(float(selection.get("mape") or 0) * 100, 2)
        out = {
            "source": "backtest",
            "n": n,
            "inside": inside,
            "typical_error_pct": f"{typical:.2f}",
            "last": None,
        }
    else:
        return None
    err = out["typical_error_pct"]
    out["level"] = level(inside / n, None if err is None else float(err))
    return out
