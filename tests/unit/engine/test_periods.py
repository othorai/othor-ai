# SPDX-License-Identifier: Apache-2.0
"""Run dates (docs/04 §1–2): D sees data to D − 1; Monday weeks; April fiscal years."""

import datetime as dt
import itertools

from othor.engine import periods as p

D = dt.date


def test_as_of_and_complete_periods():
    run = D(2026, 11, 2)  # Monday
    assert p.as_of(run) == D(2026, 11, 1)
    assert p.complete(D(2026, 10, 1), "month", run)
    assert not p.complete(D(2026, 11, 1), "month", run)
    assert p.complete(D(2026, 10, 26), "week", run)  # Mon 26 Oct – Sun 1 Nov
    assert p.complete(D(2026, 11, 1), "day", run)
    assert not p.complete(D(2026, 11, 2), "day", run)


def test_period_starts():
    assert p.period_start(D(2026, 11, 1), "week") == D(2026, 10, 26)
    assert p.period_start(D(2026, 11, 18), "month") == D(2026, 11, 1)
    assert p.period_end(D(2026, 12, 1), "month") == D(2027, 1, 1)
    assert p.period_end(D(2026, 2, 1), "month") == D(2026, 3, 1)


def test_history_window_starts_five_fiscal_years_back():
    assert p.history_start(D(2026, 11, 2), 4) == D(2021, 4, 1)  # sales_dw starts Apr 2021
    assert p.history_start(D(2027, 2, 10), 4) == D(2021, 4, 1)
    assert p.history_start(D(2027, 4, 2), 4) == D(2022, 4, 1)


def test_refresh_from_is_the_look_back_at_a_period_start():
    assert p.refresh_from(D(2026, 11, 1), D(2026, 11, 2), "month") == D(2026, 9, 1)
    assert p.refresh_from(D(2026, 11, 1), D(2026, 11, 2), "week") == D(2026, 9, 21)
    assert p.refresh_from(D(2026, 11, 1), D(2026, 11, 2), "day") == D(2026, 9, 26)
    # a gap since the last run: from that run, not tonight
    assert p.refresh_from(D(2026, 10, 1), D(2026, 11, 2), "month") == D(2026, 8, 1)


def test_backfill_points_are_sundays_capped_at_104_weeks():
    first, last = p.backfill_points(D(2026, 11, 2), D(2024, 4, 1))
    assert last == D(2026, 10, 25) and last.weekday() == 6  # before the first daily (1 Nov)
    assert first.weekday() == 6
    assert (last - first).days // 7 + 1 == 104
    # usable history shorter than 104 weeks: from its first Sunday
    first, last = p.backfill_points(D(2026, 11, 2), D(2026, 6, 3))
    assert first == D(2026, 6, 7)
    # D − 1 not a Sunday: the last Sunday before it
    assert p.backfill_points(D(2026, 11, 5), None)[1] == D(2026, 11, 1)
    # nothing usable yet
    first, last = p.backfill_points(D(2026, 11, 2), D(2026, 10, 30))
    assert first > last


def test_backfill_chunks_cover_every_sunday_once_in_order():
    """M15.1: the back-fill reads its span in chunks of 13 Sundays (one statement over 104 weeks
    of a 7 M-row table passed MySQL's 120 s limit on AWS); the chunks touch and cover it all."""
    first, last = p.backfill_points(dt.date(2026, 11, 2), None)
    chunks = p.backfill_chunks(first, last)
    assert chunks[0][0] == first and chunks[-1][1] == last
    assert all(b - a == dt.timedelta(weeks=p.BACKFILL_CHUNK_WEEKS - 1) for a, b in chunks[:-1])
    for (_a, b), (c, _d) in itertools.pairwise(chunks):
        assert c - b == dt.timedelta(weeks=1)
    sundays = sum((b - a).days // 7 + 1 for a, b in chunks)
    assert sundays == (last - first).days // 7 + 1 == p.BACKFILL_MAX_WEEKS
    assert all(a.weekday() == 6 and b.weekday() == 6 for a, b in chunks)
    # a short span is one chunk; nothing to reconstruct is no chunk
    assert p.backfill_chunks(last, last) == [(last, last)]
    assert p.backfill_chunks(last, first) == []
