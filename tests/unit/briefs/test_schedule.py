# SPDX-License-Identifier: Apache-2.0
"""Daily, Weekdays, Weekly or Monthly briefs (M15.7c part B, owner 14 Nov 2026): when a brief is
made and sent, the period a weekly or monthly issue covers, and the words for each."""

from __future__ import annotations

import datetime as dt

from othor.briefs import schedule as sch

MON = dt.date(2026, 11, 16)


def test_daily_and_weekdays_are_made_every_night_and_sent_as_before() -> None:
    for d in (MON + dt.timedelta(days=i) for i in range(7)):
        assert sch.made_on({"schedule": "daily"}, d) and sch.sends_on({"schedule": "daily"}, d)
        assert sch.made_on({"schedule": "weekdays"}, d)
        assert sch.sends_on({"schedule": "weekdays"}, d) == (d.weekday() < 5)
    assert sch.made_on({}, MON) and sch.sends_on({}, MON)  # a brief from before: daily


def test_weekly_is_made_and_sent_on_its_day_only() -> None:
    body = {"schedule": "weekly", "on": "wed"}
    days = [MON + dt.timedelta(days=i) for i in range(14)]
    assert [d for d in days if sch.made_on(body, d)] == [
        dt.date(2026, 11, 18),
        dt.date(2026, 11, 25),
    ]
    assert [d for d in days if sch.sends_on(body, d)] == [
        dt.date(2026, 11, 18),
        dt.date(2026, 11, 25),
    ]


def test_monthly_is_made_on_its_day_of_the_month() -> None:
    body = {"schedule": "monthly", "on": "1"}
    assert sch.made_on(body, dt.date(2026, 12, 1)) and not sch.made_on(body, dt.date(2026, 12, 2))
    assert sch.made_on({"schedule": "monthly", "on": "28"}, dt.date(2027, 2, 28))


def test_on_is_cleaned_to_a_real_day() -> None:
    assert sch.clean_on("weekly", "fri") == "fri"
    assert sch.clean_on("weekly", "funday") == "mon"
    assert sch.clean_on("monthly", "15") == "15"
    for bad in ("0", "29", "31", "x", None):
        assert sch.clean_on("monthly", bad) == "1"
    assert sch.clean_on("daily", "fri") is None


def test_the_period_a_weekly_or_monthly_issue_covers() -> None:
    w = {"schedule": "weekly", "on": "mon"}
    assert sch.period(w, MON, dt.date(2026, 11, 9)) == (dt.date(2026, 11, 9), "last week")
    assert sch.period(w, MON, None) == (dt.date(2026, 11, 9), "last week")  # the first issue
    m = {"schedule": "monthly", "on": "1"}
    assert sch.period(m, dt.date(2026, 12, 1), None) == (dt.date(2026, 11, 1), "last month")
    assert sch.period(m, dt.date(2027, 1, 1), dt.date(2026, 12, 1))[1] == "last month"
    assert sch.period({"schedule": "daily"}, MON, None) is None


def test_the_schedule_in_words() -> None:
    assert sch.label({"schedule": "daily"}) == "Every day"
    assert sch.label({"schedule": "weekdays"}) == "Weekdays"
    assert sch.label({"schedule": "weekly", "on": "mon"}) == "Weekly · Mondays"
    assert sch.label({"schedule": "monthly", "on": "1"}) == "Monthly · the 1st"
    assert sch.label({"schedule": "monthly", "on": "22"}) == "Monthly · the 22nd"
    assert sch.label({"schedule": "monthly", "on": "13"}) == "Monthly · the 13th"


def test_the_next_issue_of_a_weekly_or_monthly_brief() -> None:
    assert sch.next_on({"schedule": "weekly", "on": "mon"}, dt.date(2026, 11, 17)) == dt.date(
        2026, 11, 23
    )
    assert sch.next_on({"schedule": "weekly", "on": "mon"}, MON) == MON
    assert sch.next_on({"schedule": "monthly", "on": "1"}, dt.date(2026, 12, 15)) == dt.date(
        2027, 1, 1
    )
    assert sch.next_on({"schedule": "daily"}, MON) == MON
