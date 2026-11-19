# SPDX-License-Identifier: Apache-2.0
"""The targets offered for a goal (0.28, M28.1): computed by code from the company's own history
and tonight's forecasts, never written by a model (non-negotiable 1). Each is a chip the owner
can pick or type over: the target quoted in their own file, last year's actual for the same
span, where the number is heading (the forecast the goal projection already uses), and a
stretch (the forecast band's edge, upward for "at least", downward for "at most")."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from othor.engine.detectors.base import SeriesIndex
from othor.engine.forecast.store import _series
from othor.engine.forecast.store import goals as project

LABELS = {
    "file": "From your file",
    "last_year": "Last year's actual",
    "heading": "Where it's heading",
    "stretch": "A stretch",
}


@dataclass(frozen=True)
class Chip:
    kind: str  # file · last_year · heading · stretch
    value: Decimal

    @property
    def label(self) -> str:
        return LABELS[self.kind]


def fiscal_year_end(today: dt.date, start_month: int) -> dt.date:
    """The last day of the fiscal year `today` falls in (it starts on the 1st of `start_month`)."""
    start = dt.date(today.year if today.month >= start_month else today.year - 1, start_month, 1)
    nxt = dt.date(start.year + 1, start.month, 1)
    return nxt - dt.timedelta(days=1)


def nice(v: Decimal) -> Decimal:
    """Three significant figures: a target reads like one a person would set."""
    if v == 0:
        return Decimal(0)
    q = Decimal(1).scaleb(v.adjusted() - 2)
    return v.quantize(q).normalize() if q < 1 else (v / q).quantize(Decimal(1)) * q


def _year_ago(d: dt.date) -> dt.date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year - 1, day=28)


def candidates(
    index: SeriesIndex,
    rows: list[dict[str, Any]],
    metric: str,
    *,
    aggregate: str,
    direction: str,
    by: dt.date,
    quoted: Decimal | None = None,
) -> list[Chip]:
    out: list[Chip] = []
    if quoted is not None:
        out.append(Chip("file", quoted))
    ps, vs = _series(index, metric, "month")
    ago = _year_ago(by)
    if aggregate == "fiscal_year_total":
        start = _year_ago(ago) + dt.timedelta(days=1)
        span = [Decimal(repr(v)) for p, v in zip(ps, vs, strict=True) if start <= p <= ago]
        if span:
            out.append(Chip("last_year", nice(sum(span, Decimal(0)))))
    else:
        month = ago.replace(day=1)
        last = [Decimal(repr(v)) for p, v in zip(ps, vs, strict=True) if p == month]
        if last:
            out.append(Chip("last_year", nice(last[0])))
    draft = {
        "forecast": {
            "goals": [
                {
                    "key": "_draft",
                    "metric": metric,
                    "target": "0",
                    "direction": direction,
                    "by": by.isoformat(),
                    "aggregate": aggregate,
                }
            ]
        }
    }
    got = project(draft, index, rows) if rows else []
    if got:
        g = got[0]
        out.append(Chip("heading", nice(Decimal(g["projection"]))))
        edge = g.get("upper") if direction == "at_least" else g.get("lower")
        if edge is not None:
            out.append(Chip("stretch", nice(Decimal(edge))))
    return out


def goal_text(body: dict[str, Any], unit: str | None, names: dict[str, str]) -> str:
    """'Net revenue at least ₹60 Cr for the year to 31 Mar 2027', from the record, by code."""
    from othor.briefs import values
    from othor.core import fmt

    name = names.get(str(body.get("metric")), str(body.get("name") or body.get("metric")))
    word = "at most" if body.get("direction") == "at_most" else "at least"
    by = dt.date.fromisoformat(str(body["by"]))
    target = values.text(unit, body["target"])
    if body.get("aggregate") == "fiscal_year_total":
        return f"{name} {word} {target} for the year to {fmt.day_month_year(by)}"
    return f"{name} {word} {target} by {fmt.day_month_year(by)}"
