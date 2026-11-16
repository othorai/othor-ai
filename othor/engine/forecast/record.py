# SPDX-License-Identifier: Apache-2.0
"""The real track record (v0.19, M19.3): how the forecasts Othor AI actually made, before each
period began, compared with what then happened. Carried forward night to night in
`forecasts.json` (the previous run's sealed document is an input, so replay holds).

- `pending`: per series, the v2 forecast of each period that hasn't started yet, overwritten each
  night until the period starts, so the one scored is the last made before it began.
- When a pending period's actual is complete, it's scored once (inside the band or not, its %
  error) and moves to `record`, which keeps the last `KEEP` per series.
Pure: no model, no clock.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

KEEP = 12
STALE_DAYS = 120  # a pending forecast whose period never completed is dropped after this


def _q(v: float, places: str = "0.0001") -> str:
    return str(Decimal(repr(float(v))).quantize(Decimal(places)))


def carry(
    prev: dict[str, Any] | None,
    tonight: dict[str, list[dict[str, Any]]],
    actuals: dict[str, dict[str, float]],
    run_date: dt.date,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """(record, pending) for tonight. `tonight`: series → v2 points {period, p10, p50, p90};
    `actuals`: series → {period iso: value} for complete periods."""
    record = {s: list(v) for s, v in ((prev or {}).get("record") or {}).items()}
    pending = {s: dict(v) for s, v in ((prev or {}).get("pending") or {}).items()}
    # tonight's forecasts of periods that haven't started: the latest before each begins
    for s, pts in tonight.items():
        for p in pts:
            if p.get("p10") is None or dt.date.fromisoformat(p["period"]) < run_date:
                continue
            pending.setdefault(s, {})[p["period"]] = {
                "p10": p["p10"],
                "p50": p["p50"],
                "p90": p["p90"],
                "as_of": (run_date - dt.timedelta(days=1)).isoformat(),
            }
    # score each pending period whose actual is now complete, once
    for s in sorted(pending):
        done = actuals.get(s, {})
        for period in sorted(pending[s]):
            f = pending[s][period]
            if period in done:
                a = done[period]
                lo, hi, mid = float(f["p10"]), float(f["p90"]), float(f["p50"])
                record.setdefault(s, []).append(
                    {
                        "period": period,
                        "as_of": f["as_of"],
                        "p10": f["p10"],
                        "p50": f["p50"],
                        "p90": f["p90"],
                        "actual": _q(a, "0.000001"),
                        "inside": lo <= a <= hi,
                        "error_pct": _q(abs(mid - a) / abs(a) * 100, "0.01") if a else None,
                    }
                )
                del pending[s][period]
            elif (run_date - dt.date.fromisoformat(period)).days > STALE_DAYS:
                del pending[s][period]
        if not pending[s]:
            del pending[s]
    record = {
        s: sorted(v, key=lambda r: r["period"])[-KEEP:] for s, v in sorted(record.items()) if v
    }
    pending = {s: dict(sorted(v.items())) for s, v in sorted(pending.items())}
    return record, pending
