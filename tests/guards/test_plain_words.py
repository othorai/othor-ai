# SPDX-License-Identifier: Apache-2.0
"""Plain words (non-negotiable 10, owner 16 Nov 2026): visible copy uses plain words; a
data-science term lives behind an (i) or in a provenance panel. Started with the Discover 2.0
dashboard (0.29): every text the board computes for the page, the write-up's template, and the
dashboard macros' own words. Text behind an (i) (`info`, `other_info`), provenance (`ref`) and
charts' specs are not visible copy and are not checked. Other screens join as they're reworded."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
TERMS = re.compile(
    r"\b(band|variance|std|deviation|regression|p-value|quantiles?|percentiles?|z-score|"
    r"DSO|confidence interval|sigma|ETS|outliers?|changepoint|median|mean|stochastic)\b",
    re.I,
)
HIDDEN = {
    "key",
    "info",
    "other_info",
    "ref",
    "chart",
    "spark",
    "keys",
    "view",
    "hash",
    "run",
    "facts",
}
TEMPLATES = [
    ROOT / "othor" / "web" / "templates" / "components" / "dashboard.html",
    ROOT / "othor" / "web" / "templates" / "components" / "summary.html",  # Summary 1.1
    ROOT / "othor_ee" / "discover" / "templates" / "discover" / "group.html",
]


def _texts(x: Any, key: str = "") -> list[tuple[str, str]]:
    if key in HIDDEN:
        return []
    if key == "made" and isinstance(x, list):  # [title, text, info]: the info is behind an (i)
        return [t for row in x for t in _texts(list(row[:2]), "made_row")]
    if isinstance(x, str):
        return [(key, x)]
    if isinstance(x, dict):
        return [t for k, v in x.items() for t in _texts(v, str(k))]
    if isinstance(x, list | tuple):
        return [t for v in x for t in _texts(v, key)]
    return []


def offenders(texts: list[tuple[str, str]]) -> list[str]:
    return [f"{k}: {t}" for k, t in texts if TERMS.search(t)]


def test_the_dashboards_words_are_plain() -> None:
    pytest.importorskip("othor_ee")
    from othor_ee.discover import board, summary
    from othor_ee.tests.test_discover_board import _inputs

    b = board.board(_inputs())
    texts = _texts({k: v for k, v in b.items() if k not in ("facts",)})
    texts += _texts(summary.template(b))
    assert offenders(texts) == []


def test_the_dashboard_templates_words_are_plain() -> None:
    jinja = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)
    tags = re.compile(r"<[^>]+>")
    bad = []
    for p in TEMPLATES:
        if not p.exists():  # the open core has no othor_ee: its templates aren't there to check
            continue
        text = tags.sub(" ", jinja.sub(" ", p.read_text()))
        for m in TERMS.finditer(text):
            bad.append(f"{p.relative_to(ROOT)}: {m.group(0)}")
    assert bad == []


def test_the_guard_sees_a_term() -> None:
    assert offenders([("sub", "Days to pay, within the forecast band")]) != []
    assert offenders([("sub", "Days to pay, within its usual range")]) == []
