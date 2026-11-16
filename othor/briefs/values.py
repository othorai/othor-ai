# SPDX-License-Identifier: Apache-2.0
"""A headline number in its unit, one formatter for the page, the email and the alt text."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from othor.charts.spec import Unit
from othor.core import fmt
from othor.engine.sweep_text import money


def text(unit: str | None, v: Any) -> str:
    d = Decimal(str(v))
    if unit == "inr":
        return money(d)
    if unit == "inr_per_metre":
        return fmt.inr_plain(d)
    if unit == "days":
        return f"{fmt.num(d)} d"
    if unit in ("percent", "ratio"):
        return fmt.pct(d)
    if unit == "metres":
        return f"{fmt.num(d)} m"
    return fmt.num(d, decimals=1) if d != d.to_integral_value() else fmt.num(d)


def chart_unit(unit: str | None) -> Unit:
    if unit == "inr":  # ₹ crore, or $ millions (M15.6)
        from othor.engine.finding_charts import money_unit

        return money_unit()[0]
    if unit == "days":
        return Unit("days", decimals=0)
    if unit in ("percent", "ratio"):
        return Unit("percent", decimals=0)
    if unit == "metres":
        return Unit("metres", decimals=0)
    return Unit("count", decimals=1)


def chart_value(unit: str | None, v: Any) -> float:
    """The value as the chart's axis shows it (₹ in crore, $ in millions)."""
    d = Decimal(str(v))
    if unit != "inr":
        return float(d)
    from othor.engine.finding_charts import money_unit

    return float(d / Decimal(str(money_unit()[1])))


def indicator_text(v: Any, kind: str | None) -> str:
    """A watched indicator's value (Sweep's Watching tab and the brief's Watching line)."""
    d = Decimal(str(v))
    if kind == "vs_average":
        return fmt.pct(d, sign=True)
    if kind == "finding_slot":
        return fmt.num(d)
    if abs(d) < 1 and kind == "value":
        return fmt.pct(d)
    return fmt.num(d, decimals=1)


def rounded(unit: str | None, v: Any) -> str:
    """A goal's figure with no false precision: ₹ crore to one decimal (₹57.2 Cr, ₹60 Cr),
    days and other units whole or to one decimal."""
    d = Decimal(str(v))
    if unit == "inr":
        return fmt.inr_cr_rounded(d)
    if unit == "days":
        return f"{fmt.num(d)} d"
    if unit == "inr_per_metre":
        return fmt.inr_plain(d)
    return fmt.num(d, decimals=1)


def band(unit: str | None, lo: Any, hi: Any) -> str:
    """'₹55.9–58.5 Cr', '38–44 d'."""
    a, b = Decimal(str(lo)), Decimal(str(hi))
    if unit == "inr":
        return fmt.inr_cr_band(a, b)
    if unit == "days":
        return f"{fmt.num(a)}–{fmt.num(b)} d"
    if unit == "inr_per_metre":
        return fmt.inr_plain_band(a, b)
    return f"{fmt.num(a, decimals=1)}–{fmt.num(b, decimals=1)}"
