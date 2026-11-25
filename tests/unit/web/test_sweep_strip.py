# SPDX-License-Identifier: Apache-2.0
"""Sweep's detector strip (M15.7b, owner 13 Nov 2026): every detector has the owner's short name
and a one-line "what it finds"; the website's list is the same words."""

from __future__ import annotations

from othor.engine import sweep_text
from othor.engine.detectors.base import core
from othor.web.sweep_view import BOARD_ORDER

OWNER = [
    "Net/gross",
    "Shift",
    "Skew",
    "Breach",
    "Volatile",
    "Decoupled",
    "Conflict",
    "Gaps",
    "Lag",
    "Bimodal",
    "Artefact",
]


def test_every_detector_has_the_owners_short_name_in_the_boards_order() -> None:
    # the core's own checks: a plug-in's (External Signals) may be registered by an earlier test
    assert set(sweep_text.SHORT) == set(core())
    assert [sweep_text.SHORT[k] for k in BOARD_ORDER] == OWNER


def test_what_each_detector_finds_is_one_source_for_sweep_and_the_website() -> None:
    # the website's side of this check lives with the website (othor_site/tests, M20.2)
    assert set(sweep_text.CATCHES) == set(core())
    assert all("\n" not in t and len(t) <= 80 for t in sweep_text.CATCHES.values())
