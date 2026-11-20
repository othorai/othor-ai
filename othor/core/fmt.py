# SPDX-License-Identifier: Apache-2.0
"""Number, money and date formatting: the one formatter every renderer and template uses.

Money is in the company's currency (M15.6): INR in lakh/crore with Indian grouping ("₹4.62 Cr",
"₹18 L", "₹41.6", 12,34,567), or USD in thousands/millions with thousands grouping ("$1.2M",
"$45K", 1,234,567). The style is set per render like the display zone (`set_money_style`): a page,
an email, a night's run (from its spec), so a run and its replay write the same text. Negative
numbers use a true minus sign (U+2212). Rounding is half-even on Decimal, so the same value always
prints the same way (non-negotiable 2).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

MINUS = "−"
LAKH = Decimal(100_000)
CRORE = Decimal(10_000_000)
THOUSAND = Decimal(1_000)
MILLION = Decimal(1_000_000)
BILLION = Decimal(1_000_000_000)
Number = int | float | Decimal

# --- the company's currency (M15.6) ------------------------------------------------------------
CURRENCIES = ("INR", "USD")
SYMBOL = {"INR": "₹", "USD": "$"}
_MONEY: ContextVar[str | None] = ContextVar("othor_money", default=None)
# a web request's company currency: one holder per request (set by the middleware), filled when the
# company is known (the sign-in check runs in a worker thread with its own copy of the context, so
# a plain value set there would never reach the route; the holder is shared)
_REQUEST: ContextVar[list[str | None] | None] = ContextVar("othor_request_money", default=None)


def _ok(currency: str | None) -> str:
    return currency if currency in SYMBOL else "INR"


def set_money_style(currency: str | None) -> None:
    """Set the company's currency for the rest of this render (a page, an alert)."""
    _MONEY.set(_ok(currency))


@contextmanager
def money_style(currency: str | None) -> Iterator[None]:
    """The currency inside a block: a night's run and its replay (from the spec), an email."""
    token = _MONEY.set(_ok(currency))
    try:
        yield
    finally:
        _MONEY.reset(token)


def request_holder() -> Any:
    """For the web middleware: a fresh holder for this request, and no explicit currency left over
    from whatever ran before in this context (reset together with the token)."""
    return (_REQUEST.set([None]), _MONEY.set(None), _REQUEST_CHART.set([None]), _CHART.set(None))


def end_request(token: Any) -> None:
    box, money, cbox, chart = token
    _CHART.reset(chart)
    _REQUEST_CHART.reset(cbox)
    _MONEY.reset(money)
    _REQUEST.reset(box)


def set_request_currency(currency: str | None) -> None:
    """The company of this request is known (the sign-in check): its currency for the route."""
    box = _REQUEST.get()
    if box is not None:
        box[0] = _ok(currency)


# --- how charts are drawn (v0.25 M25.1, owner 18 Nov 2026) --------------------------------------
# Settings › Charts: smooth lines (on by default) and values on lines (Key points by default). A
# render-time choice, like the currency: never in a chart's spec, so stored specs and results
# keep their bytes (non-negotiable 2). `hover` is off for pictures (PNG): a tooltip needs a page.


@dataclass(frozen=True)
class ChartStyle:
    smooth: bool = True
    values: str = "key"  # "key" (last, highest, lowest, a finding's own) or "off"
    hover: bool = True


_CHART: ContextVar[ChartStyle | None] = ContextVar("othor_chart", default=None)
_REQUEST_CHART: ContextVar[list[ChartStyle | None] | None] = ContextVar(
    "othor_request_chart", default=None
)


def style_of(company: Any) -> ChartStyle:
    """A company's chart choices (defaults when it has none, or none is known)."""
    smooth = getattr(company, "chart_smooth", True)
    values = getattr(company, "chart_values", "key")
    return ChartStyle(
        smooth=True if smooth is None else bool(smooth),
        values=values if values in ("key", "off") else "key",
    )


def set_chart_style(style: ChartStyle) -> None:
    _CHART.set(style)


@contextmanager
def chart_style(style: ChartStyle) -> Iterator[None]:
    """The chart choices inside a block: an email, a PDF, a picture."""
    token = _CHART.set(style)
    try:
        yield
    finally:
        _CHART.reset(token)


def set_request_chart(company: Any) -> None:
    box = _REQUEST_CHART.get()
    if box is not None:
        box[0] = style_of(company)


def charts() -> ChartStyle:
    """How the current render draws charts."""
    explicit = _CHART.get()
    if explicit is not None:
        return explicit
    box = _REQUEST_CHART.get()
    return box[0] if box and box[0] else ChartStyle()


def currency() -> str:
    """The currency of the current render: "INR" or "USD"."""
    explicit = _MONEY.get()
    if explicit is not None:
        return explicit
    box = _REQUEST.get()
    return box[0] if box and box[0] else "INR"


def is_usd() -> bool:
    return currency() == "USD"


def _dec(v: Number) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _round(v: Decimal, decimals: int) -> Decimal:
    return v.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_EVEN)


def _group_indian(digits: str) -> str:
    """'128400' -> '1,28,400' (last three, then pairs)."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    pairs: list[str] = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head, *pairs, tail]) if head else ",".join([*pairs, tail])


def _group_thousands(digits: str) -> str:
    """'1234567' -> '1,234,567'."""
    return f"{int(digits):,}" if digits else digits


def _group(digits: str) -> str:
    """The company's grouping: Indian for INR, thousands for USD (it follows the currency)."""
    return _group_thousands(digits) if is_usd() else _group_indian(digits)


def _plain(v: Decimal, decimals: int | None) -> str:
    """Grouped magnitude; decimals=None keeps up to 1 and drops a trailing '.0'."""
    if decimals is None:
        r = _round(v, 1)
        s = f"{r:f}"
        if s.endswith(".0"):
            s = s[:-2]
    else:
        s = f"{_round(v, decimals):f}"
    whole, _, frac = s.partition(".")
    return _group(whole) + (f".{frac}" if frac else "")


def _signed(text: str, negative: bool, zero: bool, sign: bool) -> str:
    if negative:
        return MINUS + text
    if sign and not zero:
        return "+" + text
    return text


def inr(
    value: Number,
    *,
    unit: Literal["auto", "cr", "l", "plain", "b", "m", "k"] = "auto",
    decimals: int | None = None,
    sign: bool = False,
) -> str:
    """Money in the company's currency (the name is v0.1's; it is the one money formatter).
    INR: "cr" / "l" / "plain"; USD: "b" / "m" / "k" / "plain" ("cr" and "l" read as "m" and "k",
    the big and the small unit)."""
    v = _dec(value)
    mag = abs(v)
    sym = SYMBOL[currency()]
    if is_usd():
        unit = {"cr": "m", "l": "k"}.get(unit, unit)  # type: ignore[assignment]
        if unit == "auto":
            unit = (
                "b"
                if mag >= BILLION
                else "m"
                if mag >= MILLION
                else "k"
                if mag >= THOUSAND
                else "plain"
            )
        div, suffix = {"b": (BILLION, "B"), "m": (MILLION, "M"), "k": (THOUSAND, "K")}.get(
            unit, (Decimal(1), "")
        )
        scaled = mag / div
    else:
        if unit == "auto":
            unit = "cr" if mag >= CRORE else "l" if mag >= LAKH else "plain"
        if unit == "cr":
            if decimals is None:  # ₹12,345 Cr, not ₹12,345.00 Cr (M11a)
                decimals = 0 if mag >= 1000 * CRORE else 2
            scaled, suffix = mag / CRORE, " Cr"
        elif unit == "l":
            scaled, suffix = mag / LAKH, " L"
        else:
            scaled, suffix = mag, ""
    body = _plain(scaled, decimals) + suffix
    shown = _round(scaled, 1 if decimals is None else decimals)
    if shown == 0 and mag != 0:
        # a value that isn't zero never prints as ₹0 (M11a): "<₹0.1", "<₹0.01 Cr"
        step = Decimal(1).scaleb(-(1 if decimals is None else decimals))
        return _signed("<" + sym + f"{step:f}" + suffix, v < 0, False, False)
    zero = mag == 0 or shown == 0
    return _signed(sym + body, v < 0 and not zero, zero, sign)


def currency_symbol() -> str:
    """The company's currency sign: "₹" or "$" (M15.6), never written in a template."""
    return SYMBOL[currency()]


def _big() -> tuple[Decimal, str]:
    """The big unit of the company's currency: crore, or million."""
    return (MILLION, "M") if is_usd() else (CRORE, " Cr")


def inr_cr_rounded(value: Number) -> str:
    """A goal's figure with no false precision: '₹57.2 Cr', '₹60 Cr'; '$57.2M'."""
    div, suffix = _big()
    cr = (_dec(value) / div).quantize(Decimal("0.1"))
    return (
        f"{currency_symbol()}{num(cr, decimals=0 if cr == cr.to_integral_value() else 1)}{suffix}"
    )


def inr_cr_band(lo: Number, hi: Number) -> str:
    """'₹55.9–58.5 Cr'; '$55.9–58.5M'."""
    div, suffix = _big()
    x, y = (num((_dec(v) / div).quantize(Decimal("0.1")), decimals=1) for v in (lo, hi))
    return f"{currency_symbol()}{x}–{y}{suffix}"


def inr_plain(value: Number, *, decimals: int = 1) -> str:
    """A small rupee amount as is, e.g. a rate per metre: '₹52.9'; a negative one '−₹69.5'."""
    v = _dec(value)
    text = f"{currency_symbol()}{num(abs(v), decimals=decimals)}"
    return f"{MINUS}{text}" if v < 0 and _round(abs(v), decimals) != 0 else text


def inr_plain_band(lo: Number, hi: Number, *, decimals: int = 1) -> str:
    return f"{currency_symbol()}{num(lo, decimals=decimals)}–{num(hi, decimals=decimals)}"


def num(value: Number, *, decimals: int = 0, sign: bool = False) -> str:
    v = _dec(value)
    r = _round(abs(v), decimals)
    return _signed(_plain(r, decimals), v < 0 and r != 0, r == 0, sign)


def count_short(value: Number) -> str:
    """A count in short: 950, 12.4k, 100k, 1.3M (the AI usage card's tokens; never money)."""
    v = abs(_dec(value))
    for size, mark in ((Decimal(1_000_000), "M"), (Decimal(1_000), "k")):
        if v >= size:
            n = v / size
            return (
                _plain(_round(n, 0 if n >= 100 else 1), 0 if n >= 100 else 1).removesuffix(".0")
                + mark
            )
    return num(v)


def pct(fraction: Number, *, decimals: int = 0, sign: bool = False) -> str:
    return num(_dec(fraction) * 100, decimals=decimals, sign=sign) + "%"


def days(value: Number, *, decimals: int = 1) -> str:
    return num(value, decimals=decimals) + " d"


def ms(value: Number, *, decimals: int = 0) -> str:
    """A duration in milliseconds: '102 ms' (a no-break space: the unit never wraps alone)."""
    return num(value, decimals=decimals) + "\u00a0ms"


def seconds(value: Number, *, decimals: int = 1) -> str:
    """A duration in seconds: '13.1 s' (a no-break space)."""
    return num(value, decimals=decimals) + "\u00a0s"


def date_short(d: dt.date) -> str:
    return f"{d:%a} {d.day} {d:%b}"


def day_month(d: dt.date) -> str:
    return f"{d.day} {d:%b}"


def day_month_year(d: dt.date) -> str:
    """10 Nov 2026: a date that may be from another year (What's new on the website)."""
    return f"{d.day} {d:%b %Y}"


def day_range(a: dt.date, b: dt.date) -> str:
    """23–27 Oct; 30 Sep – 2 Oct; a single day when they're the same."""
    if a == b:
        return day_month(a)
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{day_month(b)}"
    return f"{day_month(a)} – {day_month(b)}"


def time_hm(t: dt.time | str, *, seconds: bool = False) -> str:
    """A time of day in the 12-hour form (owner, 16 Nov 2026): '6:00 AM', '2:05 PM',
    '12:00 PM' at noon; with seconds, '6:00:05 AM'. A stored 'HH:MM' reads the same."""
    if isinstance(t, str):
        t = dt.time.fromisoformat(t)
    h = t.hour % 12 or 12
    sec = f":{t.second:02d}" if seconds else ""
    return f"{h}:{t.minute:02d}{sec} {'AM' if t.hour < 12 else 'PM'}"


# --- clock times with their zone (M12b) -----------------------------------------------------------
# Every time of day a person reads goes through clock(): "6:00 AM IST" (12-hour from 16 Nov
# 2026). The zone it's shown in is the display zone of the current render: the company's today
# (set per page and per email); the viewer's or recipient's from v0.15, by changing only where it
# is set, not the templates.
_DISPLAY_TZ: ContextVar[str] = ContextVar("othor_display_tz", default="Asia/Kolkata")


def set_display_tz(tz: str | None) -> None:
    """Set the zone for the rest of this render (a request's thread, an email's build)."""
    _DISPLAY_TZ.set(tz or "Asia/Kolkata")


@contextmanager
def display_tz(tz: str | None) -> Iterator[None]:
    token = _DISPLAY_TZ.set(tz or "Asia/Kolkata")
    try:
        yield
    finally:
        _DISPLAY_TZ.reset(token)


def tz_abbr(tz: str | None = None, on: dt.date | None = None) -> str:
    """'IST' for Asia/Kolkata, 'EST' / 'EDT' for New York by the date; 'UTC+04' where the zone has
    no letters."""
    zone = ZoneInfo(tz or _DISPLAY_TZ.get())
    day = on or dt.date(2026, 1, 15)
    name = dt.datetime(day.year, day.month, day.day, 12, tzinfo=zone).tzname() or ""
    return f"UTC{name}" if name[:1] in "+-" else name


def local(t: dt.datetime) -> dt.datetime:
    """A stored time (UTC; naive means UTC) in the display zone, for its date and time of day."""
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.UTC)
    return t.astimezone(ZoneInfo(_DISPLAY_TZ.get()))


def clock(t: dt.time | dt.datetime | str | None, *, seconds: bool = False) -> str:
    """A time of day with its zone, 12-hour (owner, 16 Nov 2026): '6:00 AM IST'. A naive time
    or 'HH:MM' string is already in the display zone (a company's run and brief times); an aware
    datetime is converted to it."""
    if t is None or t == "":
        return ""
    zone = _DISPLAY_TZ.get()
    # a time of day ("06:00") is labelled with today's abbreviation in its zone: EDT in July, EST
    # in January (M15.6); display only, the night never writes clock times
    on = dt.datetime.now(ZoneInfo(zone)).date()
    if isinstance(t, dt.datetime):
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.UTC)
        local = t.astimezone(ZoneInfo(zone))
        on, t = local.date(), local.time()
    if isinstance(t, str):
        t = dt.time.fromisoformat(t)
    return f"{time_hm(t, seconds=seconds)} {tz_abbr(zone, on)}"


def said_at(t: dt.datetime, now: dt.datetime | None = None) -> str:
    """When a chat reply was written (owner, 19 Nov 2026): 'Today, 8:30 PM IST',
    'Yesterday, 6:01 PM IST', '17 Nov, 6:01 PM IST', with the year when it isn't this year."""
    when = local(t)
    today = local(now or dt.datetime.now(dt.UTC)).date()
    d = when.date()
    if d == today:
        day = "Today"
    elif d == today - dt.timedelta(days=1):
        day = "Yesterday"
    elif d.year == today.year:
        day = day_month(d)
    else:
        day = day_month_year(d)
    return f"{day}, {clock(t)}"


def code(value: int | str) -> str:
    s = f"{int(value):06d}" if isinstance(value, int) else value
    return f"{s[:3]} {s[3:]}"
