# SPDX-License-Identifier: Apache-2.0
"""A name inside a sentence (a paint maker's demo, 25 Nov 2026: "tiO2 stock"): its first
letter lower-cased, unless its first word is an abbreviation or a formula: a capital after the
first letter (DSO, GST, TiO2) or a digit (B2B, TiO2)."""

from __future__ import annotations

from typing import Any

import pytest

from othor.core.fmt import lower_first


@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("Revenue", "revenue"),
        ("Net revenue", "net revenue"),
        ("Cotton price (Cotlook A index)", "cotton price (Cotlook A index)"),
        ("TiO2 stock", "TiO2 stock"),
        ("DSO", "DSO"),
        ("DSO trend", "DSO trend"),
        ("GST paid", "GST paid"),
        ("B2B orders", "B2B orders"),
        ("Q3 orders", "Q3 orders"),
        ("A/R days", "A/R days"),
        ("revenue", "revenue"),
        ("", ""),
    ],
)
def test_lower_first(name: str, want: str) -> None:
    assert lower_first(name) == want


SPEC: dict[str, Any] = {"metrics": {"tio2_stock": {"name": "TiO2 stock", "unit": "kg"}}}


def test_action_sentences_keep_tio2() -> None:
    from othor.briefs import actions

    assert actions._lower_first("TiO2 stock") == "TiO2 stock"
    assert actions._watch_name("TiO2 stock, share") == "the share of TiO2 stock"
    assert actions._low("TiO2 stock fell 12%", set()) == "TiO2 stock fell 12%"
    assert actions._low("Revenue fell 12%", set()) == "revenue fell 12%"


def test_other_sentences_keep_tio2() -> None:
    from othor.briefs import ordinary, title
    from othor.engine import sweep_text
    from othor.ontology import suggest

    for low in (ordinary._low, sweep_text._lower, suggest._lower_first):
        assert low("TiO2 stock") == "TiO2 stock", low
        assert low("Revenue") == "revenue", low
    assert title._sentence(["Revenue", "TiO2 stock", "DSO"]) == ["Revenue", "TiO2 stock", "DSO"]
