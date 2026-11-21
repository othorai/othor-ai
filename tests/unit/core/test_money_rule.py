# SPDX-License-Identifier: Apache-2.0
"""One money rule (owner, 21 Nov 2026): three significant figures, the unit by size (crore, lakh,
whole rupees; billions, millions, thousands, whole dollars), the same in the brief, a goal line
and Sweep; a figure never forced into a unit where it reads ₹0 Cr or $0.1M; per-metre figures
without a trailing ".0", whole from 100; ranges in one unit with one decimal below 100."""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from othor.briefs import values
from othor.core import fmt
from othor.engine import sweep_text

USD = [
    ("566570000", "$567M"),
    ("600000000", "$600M"),
    ("46200000", "$46.2M"),
    ("45320000", "$45.3M"),
    ("4530000", "$4.53M"),
    ("980000", "$980K"),
    ("999600", "$1M"),  # rounding reaches the next unit
    ("58000", "$58K"),
    ("1250000000", "$1.25B"),
    ("452", "$452"),
    ("-1490000", "−$1.49M"),
]
INR = [
    ("566570000", "₹56.7 Cr"),
    ("600000000", "₹60 Cr"),
    ("46200000", "₹4.62 Cr"),
    ("4530000", "₹45.3 L"),
    ("980000", "₹9.8 L"),
    ("9996000", "₹1 Cr"),  # 99.96 L rounds to a crore
    ("58000", "₹58,000"),
    ("149000000", "₹14.9 Cr"),
    ("123456000000", "₹12,346 Cr"),
    ("-14900000", "−₹1.49 Cr"),
]


@pytest.mark.parametrize(("v", "want"), USD)
def test_usd(v: str, want: str) -> None:
    with fmt.money_style("USD"):
        assert fmt.money(D(v)) == want
        # the same in a brief item, a goal line and Sweep
        assert values.text("inr", D(v)) == want
        assert values.rounded("inr", D(v)) == want
        assert sweep_text.money(D(v)) == want


@pytest.mark.parametrize(("v", "want"), INR)
def test_inr(v: str, want: str) -> None:
    with fmt.money_style("INR"):
        assert fmt.money(D(v)) == want
        assert values.text("inr", D(v)) == want
        assert values.rounded("inr", D(v)) == want
        assert sweep_text.money(D(v)) == want


def test_zero_and_sign() -> None:
    with fmt.money_style("INR"):
        assert fmt.money(D(0)) == "₹0"
        assert fmt.money(D("4530000"), sign=True) == "+₹45.3 L"


def test_per_metre() -> None:
    with fmt.money_style("INR"):
        assert fmt.inr_plain(D(42)) == "₹42"
        assert fmt.inr_plain(D("52.6")) == "₹52.6"
        assert fmt.inr_plain(D("1834")) == "₹1,834"
        assert fmt.inr_plain(D("-52.697")) == "−₹52.7"


def test_ranges_share_one_unit() -> None:
    with fmt.money_style("INR"):
        assert values.band("inr", D("44000000"), D("50000000")) == "₹4.4–5.0 Cr"
    with fmt.money_style("USD"):
        assert values.band("inr", D("54100000"), D("59200000")) == "$54.1–59.2M"


def test_a_range_end_takes_the_other_ends_unit() -> None:
    with fmt.money_style("INR"):
        assert fmt.money_like(D("8080000"), D("12400000")) == "₹0.81 Cr"
        assert fmt.money_like(D("15000000"), D("12400000")) == "₹1.5 Cr"
    with fmt.money_style("USD"):
        assert fmt.money_like(D("808000"), D("1240000")) == "$0.81M"
