# SPDX-License-Identifier: Apache-2.0
"""One formatter for every number on screen, in email and in charts (docs/07, docs/12)."""

import datetime as dt
from decimal import Decimal

import pytest

from othor.core import fmt


@pytest.mark.parametrize(
    ("value", "kwargs", "out"),
    [
        (46_200_000, {}, "₹4.62 Cr"),
        (5_200_000, {"unit": "cr"}, "₹0.52 Cr"),
        (14_000_000, {"unit": "cr", "decimals": 1}, "₹1.4 Cr"),
        (600_000_000, {"unit": "cr", "decimals": 0}, "₹60 Cr"),
        (1_800_000, {}, "₹18 L"),
        (2_160_000, {}, "₹21.6 L"),
        (41.6, {}, "₹41.6"),
        (4_999, {}, "₹4,999"),
        (128_400, {"unit": "plain"}, "₹1,28,400"),
        (-14_000_000, {}, "−₹1.40 Cr"),
        (12_100_000, {"sign": True}, "+₹1.21 Cr"),
        (0, {"sign": True}, "₹0"),
        # the edges (M11a): never ₹0 for a value that isn't zero; no decimals from ₹1,000 Cr
        (0.04, {}, "<₹0.1"),
        (-0.04, {}, "−<₹0.1"),
        (0.4, {}, "₹0.4"),
        (40_000, {"unit": "cr"}, "<₹0.01 Cr"),
        (123_450_000_000, {}, "₹12,345 Cr"),
        (480_000_000_000, {}, "₹48,000 Cr"),
        (9_999_000_000, {}, "₹999.90 Cr"),
    ],
)
def test_inr(value: float, kwargs: dict, out: str) -> None:  # type: ignore[type-arg]
    assert fmt.inr(value, **kwargs) == out


def test_inr_accepts_decimal_and_rounds_half_even() -> None:
    assert fmt.inr(Decimal("46250000"), unit="cr") == "₹4.62 Cr"
    assert fmt.inr(Decimal("46350000"), unit="cr") == "₹4.64 Cr"


@pytest.mark.parametrize(
    ("value", "kwargs", "out"),
    [
        (1_284_000, {}, "12,84,000"),
        (999, {}, "999"),
        (12_34_56_789, {}, "12,34,56,789"),
        (1.21, {"decimals": 2, "sign": True}, "+1.21"),
        (-0.19, {"decimals": 2, "sign": True}, "−0.19"),
        (-1.4, {"decimals": 2}, "−1.40"),
    ],
)
def test_num(value: float, kwargs: dict, out: str) -> None:  # type: ignore[type-arg]
    assert fmt.num(value, **kwargs) == out


def test_pct_days_dates() -> None:
    assert fmt.pct(0.62) == "62%"
    assert fmt.pct(-0.081, decimals=1, sign=True) == "−8.1%"
    assert fmt.days(6.3) == "6.3 d"
    assert fmt.days(46, decimals=0) == "46 d"
    assert (
        fmt.date_short(dt.date(2026, 11, 1)) == "Sun 1 Nov"
    )  # boards say "Sat"; 1 Nov 2026 is a Sunday
    assert fmt.day_month(dt.date(2026, 8, 14)) == "14 Aug"
    # 12-hour (owner, 16 Nov 2026)
    assert fmt.time_hm(dt.time(6, 0)) == "6:00 AM"
    assert fmt.time_hm(dt.time(0, 5)) == "12:05 AM"
    assert fmt.time_hm(dt.time(12, 0)) == "12:00 PM"
    assert fmt.time_hm(dt.time(23, 59, 7), seconds=True) == "11:59:07 PM"
    assert fmt.time_hm("14:30") == "2:30 PM"
    assert fmt.code(482913) == "482 913"


def test_minus_is_a_true_minus_sign() -> None:
    assert "-" not in fmt.inr(-1) and "−" in fmt.inr(-1)


def test_clock_names_its_zone() -> None:
    """Every time of day carries its zone, through the one formatter (M12b)."""
    import datetime as dt

    from othor.core import fmt

    assert fmt.clock("06:00") == "6:00 AM IST"
    assert fmt.clock(dt.time(2, 0)) == "2:00 AM IST"
    # an aware datetime is shown in the display zone: 8:30 PM UTC is 2:00 AM IST next day
    assert fmt.clock(dt.datetime(2026, 10, 31, 20, 30, tzinfo=dt.UTC)) == "2:00 AM IST"
    with fmt.display_tz("Asia/Dubai"):
        assert fmt.clock("06:00") == "6:00 AM UTC+04"
    assert fmt.clock("06:00") == "6:00 AM IST"  # back to the company's zone
    assert fmt.clock(None) == ""


def test_durations_keep_their_unit_on_the_line() -> None:
    assert fmt.ms(114.6) == "115 ms"
    assert fmt.seconds(17.83) == "17.8 s"
    assert fmt.seconds(0.4, decimals=1) == "0.4 s"


def test_a_stored_time_is_shown_in_the_display_zone() -> None:
    """Sources' "Last read" showed UTC (18:01 for 11:31 PM IST, owner, 11 Nov 2026)."""
    import datetime as dt

    from othor.core import fmt

    stored = dt.datetime(2026, 11, 11, 18, 1)  # naive = UTC, as the database keeps it
    with fmt.display_tz("Asia/Kolkata"):
        assert fmt.local(stored) == dt.datetime(
            2026, 11, 11, 23, 31, tzinfo=fmt.ZoneInfo("Asia/Kolkata")
        )
        assert fmt.clock(stored) == "11:31 PM IST"
        late = dt.datetime(2026, 11, 11, 20, 0)  # 1:30 AM IST the next day: the date moves too
        assert fmt.day_month(fmt.local(late).date()) == fmt.day_month(dt.date(2026, 11, 12))


# --- M15.6 · the company's currency ------------------------------------------------------------


def test_inr_is_unchanged_by_default():
    assert fmt.currency() == "INR"
    assert fmt.inr(14_000_000) == "₹1.40 Cr" and fmt.inr(580_000) == "₹5.8 L"
    assert fmt.num(1_234_567) == "12,34,567" and fmt.inr_cr_rounded(572_000_000) == "₹57.2 Cr"


def test_usd_reads_compact_with_thousands_grouping():
    from othor.engine import sweep_text

    with fmt.money_style("USD"):
        assert fmt.currency_symbol() == "$"
        assert fmt.num(1_234_567) == "1,234,567"
        assert fmt.inr(1_234_567, unit="plain", decimals=0) == "$1,234,567"  # tables: in full
        assert sweep_text.money(1_200_000) == "$1.2M"
        assert sweep_text.money(45_000) == "$45K"
        assert sweep_text.money(3_400_000_000) == "$3.4B"
        assert sweep_text.money(523) == "$523"
        assert sweep_text.money(-1_250_000) == "−$1.25M"
        assert fmt.inr(1_200_000, sign=True) == "+$1.2M"
        assert fmt.inr_cr_rounded(57_200_000) == "$57.2M"
        assert fmt.inr_cr_band(55_900_000, 58_500_000) == "$55.9–58.5M"
        assert fmt.inr_cr_band(540_800_000, 592_300_000) == "$541–592M"  # three figures
        assert sweep_text.money_col([1_100_000, 800_000]) == ["$1.1M", "$0.8M"]
    assert fmt.inr(14_000_000) == "₹1.40 Cr"  # the style never leaks past its render


def test_an_unknown_currency_reads_as_inr():
    with fmt.money_style("EUR"):
        assert fmt.currency() == "INR"


def test_a_time_of_day_has_the_abbreviation_of_its_season():
    import datetime as dt

    assert fmt.tz_abbr("America/New_York", dt.date(2026, 7, 1)) == "EDT"
    assert fmt.tz_abbr("America/New_York", dt.date(2026, 1, 15)) == "EST"
    assert fmt.tz_abbr("America/Phoenix", dt.date(2026, 7, 1)) == "MST"  # no daylight saving
    assert fmt.tz_abbr("Asia/Kolkata", dt.date(2026, 7, 1)) == "IST"


def test_a_count_in_short():
    """The AI usage card's tokens (M15.8): a count, never money."""
    from othor.core import fmt

    assert fmt.count_short(0) == "0"
    assert fmt.count_short(950) == "950"
    assert fmt.count_short(12_400) == "12.4k"
    assert fmt.count_short(99_980) == "100k"
    assert fmt.count_short(1_250_000) == "1.2M"  # half-even, as every number here
    assert fmt.count_short(1_260_000) == "1.3M"


def test_a_request_starts_without_a_left_over_currency():
    """14 Nov 2026 (the full test run): an explicit currency left in the context by earlier work
    must not reach a request; the request's company currency rules inside it, and the old value
    comes back after."""
    from othor.core import fmt

    fmt.set_money_style("INR")
    token = fmt.request_holder()
    try:
        fmt.set_request_currency("USD")
        assert fmt.currency() == "USD"
    finally:
        fmt.end_request(token)
    assert fmt.currency() == "INR"


def test_a_negative_small_rupee_amount_puts_the_minus_first():
    """M16.3: '₹−69.5' read oddly on Summary; the minus leads, as for every other amount."""
    from othor.core import fmt

    assert fmt.inr_plain(52.9) == "₹52.9"
    assert fmt.inr_plain(-69.5) == "−₹69.5"
    assert fmt.inr_plain(-0.01) == "₹0"


def test_a_chat_reply_says_when_it_was_written() -> None:
    """0.28.4 (owner, 19 Nov 2026): each Othor AI reply carries its date and time."""
    import datetime as dt

    from othor.core import fmt

    now = dt.datetime(2026, 11, 19, 14, 0, tzinfo=dt.UTC)  # 7:30 PM IST
    with fmt.display_tz("Asia/Kolkata"):
        assert fmt.said_at(now - dt.timedelta(hours=1), now) == "Today, 6:30 PM IST"
        assert fmt.said_at(now - dt.timedelta(days=1), now) == "Yesterday, 7:30 PM IST"
        assert fmt.said_at(now - dt.timedelta(days=3), now) == "16 Nov, 7:30 PM IST"
        assert fmt.said_at(dt.datetime(2025, 12, 1, 3, 0), now) == "1 Dec 2025, 8:30 AM IST"


def test_an_answer_reads_dates_plainly() -> None:
    from othor.chat.ask import plain_dates

    assert plain_dates("As of the last run on 2026-11-18, net revenue") == (
        "As of the last run on 18 Nov 2026, net revenue"
    )
    assert plain_dates("order 2026-13-45 stays") == "order 2026-13-45 stays"
