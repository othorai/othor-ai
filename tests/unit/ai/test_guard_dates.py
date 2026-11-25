# SPDX-License-Identifier: Apache-2.0
"""#123: a date the input holds as 2026-11-01 may be written in the app's display forms."""

from __future__ import annotations

import pytest

from othor.ai.guards import numbers_grounded

CARD = {"run": {"date": "2026-11-01", "checks": 11}}


@pytest.mark.parametrize(
    "answer",
    [
        "All 11 checks ran on 1 Nov.",
        "All 11 checks ran on 1 Nov 2026.",
        "All 11 checks ran on Nov 1.",
        "All 11 checks ran on 1 November 2026.",
        "All 11 checks ran on 2026-11-01.",
    ],
)
def test_display_forms_of_an_input_date_are_grounded(answer: str) -> None:
    assert numbers_grounded(answer, CARD)


def test_a_datetime_in_the_input_counts_too() -> None:
    assert numbers_grounded("Last run 1 Nov.", {"at": "2026-11-01T06:00:00+05:30"})


@pytest.mark.parametrize(
    "answer",
    [
        "All 11 checks ran on 2 Nov.",
        "All 11 checks ran on 1 Nov 2025.",
        "All 12 checks ran on 1 Nov.",
    ],
)
def test_a_date_or_figure_not_in_the_input_still_fails(answer: str) -> None:
    assert not numbers_grounded(answer, CARD)
