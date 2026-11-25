# SPDX-License-Identifier: Apache-2.0
"""A headline number in its unit, one formatter for the page, the email and the alt text."""

from __future__ import annotations

import re
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


# 0.28 (M28.1): a typed target read back in its unit, the way `text` writes it. Money takes the
# Indian and Western short words; a percent is stored as a fraction.
_SCALE = {
    "cr": Decimal("1e7"),
    "crore": Decimal("1e7"),
    "crores": Decimal("1e7"),
    "l": Decimal("1e5"),
    "lakh": Decimal("1e5"),
    "lakhs": Decimal("1e5"),
    "lac": Decimal("1e5"),
    "k": Decimal("1e3"),
    "m": Decimal("1e6"),
    "mn": Decimal("1e6"),
    "million": Decimal("1e6"),
    "b": Decimal("1e9"),
    "bn": Decimal("1e9"),
    "billion": Decimal("1e9"),
}
_NUMBER = re.compile(r"^-?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?$")


def parse(unit: str | None, text: str) -> Decimal:
    """Raises ValueError when `text` isn't a number in this unit."""
    t = text.strip().lower().replace("₹", "").replace("$", "").replace("rs.", "").strip()
    for tail in (" d", "d", " days", " m", " metres", "%"):
        if unit in ("days", "metres", "percent", "ratio") and t.endswith(tail):
            t = t[: -len(tail)].strip()
            break
    scale = Decimal(1)
    if unit not in ("percent", "ratio", "days", "metres"):
        m = re.match(r"^(.*?)\s*([a-z]+)$", t)
        if m and m.group(2) in _SCALE:
            t, scale = m.group(1).strip(), _SCALE[m.group(2)]
    if not _NUMBER.match(t):
        raise ValueError(text)
    v = Decimal(t.replace(",", "")) * scale
    return v / 100 if unit in ("percent", "ratio") else v


def _money(small: bool) -> tuple[Unit, float]:
    from othor.engine.finding_charts import money_unit, small_money_unit

    return small_money_unit() if small else money_unit()


def small_money(values: Any) -> bool:
    """A money chart whose values all stay under ₹1 Cr ($1M) is drawn in ₹ lakh ($ thousands)
    (0.28.4: Average order value at ₹1.8 L was drawn as ₹0.02 Cr)."""
    from othor.engine.finding_charts import money_unit

    big = Decimal(str(money_unit()[1]))
    vals = [abs(Decimal(str(v))) for v in values]
    return bool(vals) and max(vals) < big


def chart_unit(unit: str | None, small: bool = False) -> Unit:
    if unit == "inr":  # ₹ crore, or $ millions (M15.6); lakh / thousands when all are small
        return _money(small)[0]
    if unit == "days":
        return Unit("days", decimals=0)
    if unit in ("percent", "ratio"):
        return Unit("percent", decimals=0)
    if unit == "metres":
        return Unit("metres", decimals=0)
    return Unit("count", decimals=1)


def chart_value(unit: str | None, v: Any, small: bool = False) -> float:
    """The value as the chart's axis shows it (₹ in crore, $ in millions; lakh / thousands when
    the chart is small money)."""
    d = Decimal(str(v))
    if unit != "inr":
        return float(d)
    return float(d / Decimal(str(_money(small)[1])))


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


def places(v: Any, most: int = 2) -> int:
    """How many decimals a typed figure carries: '3.5' → 1, '45' → 0, '45.00' → 0 (#147)."""
    d = Decimal(str(v)).normalize()
    exp = d.as_tuple().exponent
    return min(most, max(0, -exp)) if isinstance(exp, int) else 0


def rounded(unit: str | None, v: Any, like: Any = None) -> str:
    """A goal's figure with no false precision: ₹ crore to one decimal (₹57.2 Cr, ₹60 Cr), a
    count whole (#148), days and other numbers to the decimals of the goal's own target (`like`,
    at most two): a 3.5 d target tracks at 3.6 d, never '4 d' (#147). Without `like`, as before
    #147: days whole, other numbers to one decimal (a goal's figure always passes its target)."""
    d = Decimal(str(v))
    if unit == "inr":
        return fmt.inr_cr_rounded(d)
    if unit == "inr_per_metre":
        return fmt.inr_plain(d)
    if unit == "count":
        return fmt.num(d)
    if unit in ("percent", "ratio"):
        return fmt.num(d, decimals=1)
    n = places(like) if like is not None else (0 if unit == "days" else 1)
    if unit == "days":
        return f"{fmt.num(d, decimals=n)} d"
    return fmt.num(d, decimals=n)


def goal_target(unit: str | None, v: Any) -> str:
    """A goal's own target, to its own decimals: 'at most 3.5 d' (#147), '2,27,200' (#148)."""
    return rounded(unit, v, like=v)


def goal_value(unit: str | None, v: Any, like: Any = None) -> str:
    """A goal's figure where a page writes figures the way `text` does: days to the target's
    decimals (#147) and counts whole (#148), every other unit as `text`."""
    return rounded(unit, v, like=like) if unit in ("days", "count") else text(unit, v)


def text_band(unit: str | None, lo: Any, hi: Any) -> str:
    """A range written the way `text` writes each end, the shared unit once (0.28.4, 100-night
    test: "₹0.55 Cr–₹0.78 Cr" beside "₹51.5–63.1 Cr"): '₹0.55–0.78 Cr', '₹5.8–7.1 L',
    '74,778–1,25,000 m'; ends in different units stay whole: '₹8.7 L–₹1.2 Cr'."""
    a, b = text(unit, lo), text(unit, hi)
    tail_a = a.rsplit(" ", 1)[1] if " " in a else ""
    tail_b = b.rsplit(" ", 1)[1] if " " in b else ""
    if tail_a != tail_b:
        return f"{a}–{b}"
    head = a.removesuffix(" " + tail_a) if tail_a else a
    sym = fmt.currency_symbol()
    tail = b.removeprefix(sym) if head.startswith(sym) and b.startswith(sym) else b
    return f"{head}–{tail}"


def band(unit: str | None, lo: Any, hi: Any, like: Any = None) -> str:
    """'₹55.9–58.5 Cr', '38–44 d', '3.4–3.9 d' for a 3.5 d target, a count whole."""
    a, b = Decimal(str(lo)), Decimal(str(hi))
    if unit == "inr":
        return fmt.inr_cr_band(a, b)
    if unit == "inr_per_metre":
        return fmt.inr_plain_band(a, b)
    if unit == "count":
        return f"{fmt.num(a)}–{fmt.num(b)}"
    if unit in ("percent", "ratio"):
        return f"{fmt.num(a, decimals=1)}–{fmt.num(b, decimals=1)}"
    n = places(like) if like is not None else (0 if unit == "days" else 1)
    if unit == "days":
        return f"{fmt.num(a, decimals=n)}–{fmt.num(b, decimals=n)} d"
    return f"{fmt.num(a, decimals=n)}–{fmt.num(b, decimals=n)}"
