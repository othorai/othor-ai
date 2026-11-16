# SPDX-License-Identifier: Apache-2.0
"""How often a brief is made and sent (M15.7c part B, owner 14 Nov 2026): Daily, Weekdays,
Weekly (on a weekday) or Monthly (on the 1st–28th, so every month has the day).

Daily and Weekdays briefs are made every night, as before; Weekdays skips the weekend email. A
Weekly or Monthly brief is made and sent on its day only, and covers its whole period: since its
previous issue, or one week or month back for the first. Existing briefs have no `on` and are
daily. Pure: the run and the delivery tick call the same functions."""

from __future__ import annotations

import datetime as dt
from typing import Any

SCHEDULES = ("daily", "weekdays", "weekly", "monthly")
PERIODIC = ("weekly", "monthly")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTH_DAYS = tuple(str(d) for d in range(1, 29))
WORD = {"weekly": "last week", "monthly": "last month"}


def kind(body: dict[str, Any]) -> str:
    s = body.get("schedule")
    return s if s in SCHEDULES else "daily"


def clean_on(schedule: str, on: Any) -> str | None:
    """The day a Weekly or Monthly brief comes out; None for the others."""
    if schedule == "weekly":
        return on if on in WEEKDAYS else "mon"
    if schedule == "monthly":
        return str(on) if str(on) in MONTH_DAYS else "1"
    return None


def _on(body: dict[str, Any]) -> str:
    return str(clean_on(kind(body), body.get("on")))


def _is_day(body: dict[str, Any], day: dt.date) -> bool:
    k = kind(body)
    if k == "weekly":
        return WEEKDAYS[day.weekday()] == _on(body)
    if k == "monthly":
        return day.day == int(_on(body))
    return True


def made_on(body: dict[str, Any], day: dt.date) -> bool:
    """Whether the night of `day` makes an issue of this brief."""
    return kind(body) not in PERIODIC or _is_day(body, day)


def sends_on(body: dict[str, Any], day: dt.date) -> bool:
    """Whether the brief's email goes out on `day`."""
    if kind(body) == "weekdays":
        return day.weekday() < 5
    return made_on(body, day)


def _month_back(day: dt.date) -> dt.date:
    y, m = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)
    return day.replace(year=y, month=m, day=min(day.day, 28))


def period(
    body: dict[str, Any], day: dt.date, previous: dt.date | None
) -> tuple[dt.date, str] | None:
    """(the period's first day, "last week" or "last month") for a Weekly or Monthly issue on
    `day`: from its previous issue, else one week or month back. None for Daily and Weekdays."""
    k = kind(body)
    if k not in PERIODIC:
        return None
    back = day - dt.timedelta(weeks=1) if k == "weekly" else _month_back(day)
    return (previous if previous is not None and previous < day else back, WORD[k])


def _nth(n: int) -> str:
    end = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{end}"


def label(body: dict[str, Any]) -> str:
    """'Every day', 'Weekdays', 'Weekly · Mondays', 'Monthly · the 1st'."""
    k = kind(body)
    if k == "weekly":
        return f"Weekly · {DAY_NAMES[WEEKDAYS.index(_on(body))]}s"
    if k == "monthly":
        return f"Monthly · the {_nth(int(_on(body)))}"
    return "Weekdays" if k == "weekdays" else "Every day"


def on_options(schedule: str) -> list[tuple[str, str]]:
    """The choices for "On" in the Delivery tab."""
    if schedule == "weekly":
        return list(zip(WEEKDAYS, DAY_NAMES, strict=True))
    if schedule == "monthly":
        return [(d, f"The {_nth(int(d))}") for d in MONTH_DAYS]
    return []


def since_word(issue: dict[str, Any] | None) -> str:
    """'yesterday', or 'last week' / 'last month' for a Weekly or Monthly issue (its stored
    period), for "Since yesterday" and the like."""
    return str(((issue or {}).get("period") or {}).get("word") or "yesterday")


def next_on(body: dict[str, Any], day: dt.date) -> dt.date:
    """The next day (from `day`, itself included) this brief is made."""
    d = day
    while not made_on(body, d):
        d += dt.timedelta(days=1)
    return d
