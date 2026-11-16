# SPDX-License-Identifier: Apache-2.0
"""Dates of a run (docs/04 §1–2). A run for date D starts at 02:00 on D and sees data up to and
including D − 1; snapshots are taken as of D − 1; a period is complete when it ends on or
before D. Weeks run Monday–Sunday, months are calendar months, fiscal years start in April."""

from __future__ import annotations

import datetime as dt

LOOKBACK_DAYS = 35  # docs/04 §2 *default*: late credit notes
HISTORY_YEARS = 5  # first read: from the start of the fiscal year five years back
BACKFILL_MAX_WEEKS = 104
BACKFILL_CHUNK_WEEKS = 13  # M15.1: one statement per quarter of Sundays, not 104 weeks at once


def as_of(run_date: dt.date) -> dt.date:
    return run_date - dt.timedelta(days=1)


def period_start(d: dt.date, grain: str) -> dt.date:
    if grain == "day":
        return d
    if grain == "week":
        return d - dt.timedelta(days=d.weekday())
    if grain == "month":
        return d.replace(day=1)
    raise ValueError(f"unknown grain {grain}")


def period_end(start: dt.date, grain: str) -> dt.date:
    """Exclusive end."""
    if grain == "day":
        return start + dt.timedelta(days=1)
    if grain == "week":
        return start + dt.timedelta(days=7)
    if grain == "month":
        return (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    raise ValueError(f"unknown grain {grain}")


def complete(start: dt.date, grain: str, run_date: dt.date) -> bool:
    return period_end(start, grain) <= run_date


def fiscal_year_start(d: dt.date, start_month: int) -> dt.date:
    y = d.year if d.month >= start_month else d.year - 1
    return dt.date(y, start_month, 1)


def history_start(run_date: dt.date, fy_start_month: int) -> dt.date:
    """Where the first read starts: Apr 2021 for a run in Nov 2026."""
    fy = fiscal_year_start(as_of(run_date), fy_start_month)
    return fy.replace(year=fy.year - HISTORY_YEARS)


def refresh_from(last_run_date: dt.date, run_date: dt.date, grain: str) -> dt.date:
    """Later runs re-read from the earlier run's as-of minus the look-back, at a period start;
    periods before it are carried from that run's inputs."""
    d = min(last_run_date, run_date)
    return period_start(as_of(d) - dt.timedelta(days=LOOKBACK_DAYS), grain)


def sunday_on_or_after(d: dt.date) -> dt.date:
    return d + dt.timedelta(days=(6 - d.weekday()) % 7)


def sunday_before(d: dt.date) -> dt.date:
    """The last Sunday strictly before `d`."""
    delta = (d.weekday() + 1) % 7 or 7
    return d - dt.timedelta(days=delta)


def backfill_points(run_date: dt.date, usable_from: dt.date | None) -> tuple[dt.date, dt.date]:
    """[first, last] Sundays to reconstruct: weekly back from the Sunday before the first daily
    snapshot, 104 weeks or to the start of usable history, whichever is shorter (5 Nov 2026).
    first > last means there is nothing to reconstruct."""
    last = sunday_before(as_of(run_date))
    first = last - dt.timedelta(weeks=BACKFILL_MAX_WEEKS - 1)
    if usable_from is not None:
        first = max(first, sunday_on_or_after(usable_from))
    return first, last


def backfill_chunks(
    first: dt.date, last: dt.date, weeks: int = BACKFILL_CHUNK_WEEKS
) -> list[tuple[dt.date, dt.date]]:
    """[first, last] Sundays split into touching spans of `weeks` Sundays each (the last may be
    shorter), in order. The same query runs per span; the points are the same as one statement's
    (M15.1: a 7 M-row table over 104 weeks passed MySQL's 120 s limit in one statement)."""
    out: list[tuple[dt.date, dt.date]] = []
    start = first
    while start <= last:
        end = min(start + dt.timedelta(weeks=weeks - 1), last)
        out.append((start, end))
        start = end + dt.timedelta(weeks=1)
    return out
