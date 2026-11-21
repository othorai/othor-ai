# SPDX-License-Identifier: Apache-2.0
"""How far a forecast can see ahead (Forecasts 1.0, owner 21 Nov 2026): from the run's stored
`horizon` rows (each number 12 periods ahead, F1) and the company's one boundary setting, the
outlook's `fog_band_share` (±35% by default; a week past it is the outlook's Limited view).

A period is a call while its likely range's half-width, (P90 − P10) / 2, is within the share of
the size of its expected value; the number can see ahead to the last period before the first one
past the boundary. Past it, the forecast is an estimate (good for direction); past the stored
periods, it's an assumption (the last 12 months repeat). Pure: decimal arithmetic, no clock, no
model, so the page shows the same thing for the same run every time.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from othor.core import fmt


@dataclass(frozen=True)
class Sight:
    periods: int  # how many periods ahead are a call (0: not even the next one)
    last: str | None  # the last such period (ISO date), None when there's none
    all_of: int  # how many periods were stored

    @property
    def full(self) -> bool:
        """Every stored period is a call: it can see at least that far."""
        return self.all_of > 0 and self.periods == self.all_of


def share(spec: dict[str, Any]) -> Decimal:
    """The company's boundary: the weather settings' `fog_band_share` (the spec may override)."""
    from othor.briefs.weather import settings

    return Decimal(str(settings(spec)["fog_band_share"]))


def _d(v: Any) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def can_see(rows: list[dict[str, Any]], within: Decimal) -> Sight:
    """The leading periods whose half-range is within `within` × |expected|."""
    n, last = 0, None
    for r in sorted(rows, key=lambda r: int(r["h"])):
        lo, mid, hi = _d(r.get("p10")), _d(r.get("p50")), _d(r.get("p90"))
        if lo is None or hi is None or mid is None or mid == 0:
            break
        if (hi - lo) / 2 > within * abs(mid):
            break
        n, last = n + 1, str(r["period"])
    return Sight(n, last, len(rows))


def zone(s: Sight, h: int) -> str:
    """Which zone the h-th period ahead is in: likely, estimate or assumption."""
    if h <= s.periods:
        return "likely"
    if h <= s.all_of:
        return "estimate"
    return "assumption"


WORD = {"day": ("day", "days"), "week": ("week", "weeks"), "month": ("month", "months")}


def span(n: int, grain: str, *, full: bool = False) -> str:
    """'2 weeks', '1 month', '12 months or more'; 'Not yet' when it can't call the next period."""
    if n <= 0:
        return "Not yet"
    one, many = WORD.get(grain, ("period", "periods"))
    return f"{fmt.num(n)} {one if n == 1 else many}" + (" or more" if full else "")
