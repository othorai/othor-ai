# SPDX-License-Identifier: Apache-2.0
"""0.28 (M28.3): the files' quoted targets as draft goals. The model says which quote is a goal
on which drafted number; code keeps one only when the figure is the quote's own."""

from __future__ import annotations

import datetime as dt

from othor.setup import goal_drafts as gd
from othor.setup.parse import Ref

TODAY = dt.date(2026, 11, 1)
FY_END = dt.date(2027, 3, 31)
BOARD = "Board-review-Q2-FY27.pdf"
QS = [
    (BOARD, Ref("target", "slide 4", "Net revenue: ₹60 Cr in FY27, by 31 Mar 2027.")),
    (BOARD, Ref("target", "slide 4", "DSO under 45 days by 31 Mar 2027.")),
]
NUMS = [
    {"key": "net_revenue", "name": "Net revenue", "unit": "inr", "kind": "flow"},
    {"key": "dso", "name": "DSO", "unit": "days", "kind": "flow"},
]


def _g(**kw):  # type: ignore[no-untyped-def]
    base = {"by": None, "direction": None, "aggregate": None}
    return {**base, **kw}


def test_a_quoted_target_becomes_a_goal_with_its_own_figure_and_source():
    out = {
        "goals": [
            _g(quote=0, metric="net_revenue", target="60 Cr", by="2027-03-31"),
            _g(quote=1, metric="dso", target="45 days", direction="at_most", aggregate="value"),
        ]
    }
    got = {k: (b, c, q) for k, b, c, q in gd.checked(out, QS, NUMS, TODAY, FY_END)}
    body, cite, quote = got["net_revenue_by_2027_03_31"]
    assert body["target"] == "600000000" and body["direction"] == "at_least"
    assert body["aggregate"] == "fiscal_year_total" and cite == "Board-review-Q2-FY27 · slide 4"
    assert quote == QS[0][1].text
    dso = got["dso_by_2027_03_31"][0]
    assert dso["target"] == "45" and dso["direction"] == "at_most" and dso["aggregate"] == "value"


def test_a_figure_not_in_the_quote_or_a_number_not_drafted_is_dropped():
    out = {
        "goals": [
            _g(quote=0, metric="net_revenue", target="75 Cr"),  # not the quote's figure
            _g(quote=0, metric="ebitda", target="60 Cr"),  # not a drafted number
            _g(quote=5, metric="net_revenue", target="60 Cr"),  # no such quote
        ]
    }
    assert gd.checked(out, QS, NUMS, TODAY, FY_END) == []
    assert gd.checked(None, QS, NUMS, TODAY, FY_END) == []


def test_a_level_is_never_a_years_total_and_the_date_defaults_to_the_years_end():
    out = {"goals": [_g(quote=1, metric="dso", target="45 days", aggregate="fiscal_year_total")]}
    ((_key, body, _c, _q),) = gd.checked(out, QS, NUMS, TODAY, FY_END)
    assert body["aggregate"] == "value" and body["by"] == "2027-03-31"
    assert body["direction"] == "at_most"  # "under 45 days", read from the quote itself


def test_only_quotes_with_a_figure_go_to_the_model():
    refs = [*QS, (BOARD, Ref("target", "slide 4", "Grow exports")), ("x.pdf", Ref("term", "", "9"))]
    assert gd.quotes(refs) == QS
    assert gd.model_input([], NUMS, TODAY, FY_END) is None
    sent = gd.model_input(QS, NUMS, TODAY, FY_END)
    assert sent is not None and [q["id"] for q in sent["quotes"]] == [0, 1]
