# SPDX-License-Identifier: Apache-2.0
"""#118: a number a model writes as a word is checked like a numeral (non-negotiable 1)."""

from __future__ import annotations

import pytest

from othor.ai.guards import numbers_grounded, numerals


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Fourteen customers went quiet", {"14"}),
        ("twenty-one days late", {"21"}),
        ("three hundred metres", {"300"}),
        ("Seventy two orders", {"72"}),
        ("two lakh at stake", {"2"}),  # the unit is a unit, as "2 L" in the input
        ("one question for Priya Menon", set()),  # a lone "one" is not a figure
        ("None of them, often, someone", set()),
        ("₹4.62 Cr in Oct", {"4.62"}),
    ],
)
def test_number_words_are_numerals(text: str, want: set[str]) -> None:
    assert numerals(text) == want


def test_a_spelled_out_number_not_in_the_input_is_caught() -> None:
    assert not numbers_grounded("Fourteen customers stopped ordering.", {"line": "13 customers"})
    assert numbers_grounded("Fourteen customers stopped ordering.", {"line": "14 customers"})
    assert numbers_grounded("Three accounts carry it.", {"title": "three accounts carry 72%"})
