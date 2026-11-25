# SPDX-License-Identifier: Apache-2.0
"""The goal line (decided 6 Nov 2026): the goal named, no false precision, the real band, red
only when the whole band misses the target, amber when the target is inside the band."""

from __future__ import annotations

from othor.briefs.assemble import goal_lines

SPEC = {
    "metrics": {
        "net_revenue": {"name": "Net revenue", "unit": "inr"},
        "dso": {"name": "DSO", "unit": "days"},
    }
}


def _g(**kw):
    base = {
        "key": "fy27_net_revenue",
        "name": "₹60 Cr net revenue in FY27, by 31 Mar",
        "metric": "net_revenue",
        "aggregate": "fiscal_year_total",
        "direction": "at_least",
        "target": "600000000",
        "by": "2027-03-31",
        "projection": "571948559.00",
        "lower": "559000000.00",
        "upper": "585000000.00",
    }
    return {**base, **kw}


def test_the_fiscal_year_goal_line():
    (g,) = goal_lines(SPEC, ["fy27_net_revenue"], [_g()])
    assert g["text"] == (
        "Net revenue FY27 · ₹60 Cr target · tracking ₹57.2 Cr (80% band ₹55.9–58.5 Cr)"
    )
    assert g["tone"] == "off"  # the whole band is under the target
    (amber,) = goal_lines(SPEC, ["fy27_net_revenue"], [_g(upper="612000000.00")])
    assert amber["tone"] == "risk"
    (ok,) = goal_lines(SPEC, ["fy27_net_revenue"], [_g(lower="601000000.00", upper="640000000")])
    assert ok["tone"] == "ok"


def test_a_value_goal_at_most():
    dso = _g(
        key="dso_under_45",
        name="DSO under 45 days by 31 Mar",
        metric="dso",
        aggregate="value",
        direction="at_most",
        target="45",
        projection="41.2",
        lower="38.1",
        upper="44.3",
    )
    (g,) = goal_lines(SPEC, ["dso_under_45"], [dso])
    assert g["text"] == "DSO · 45 d target by 31 Mar · tracking 41 d (80% band 38–44 d)"
    assert g["tone"] == "ok"
    assert not goal_lines(SPEC, [], [dso])  # a goal the brief doesn't watch


def test_the_likelihood_sets_the_label_and_the_line_says_it():
    """21 Nov 2026 (indigodemo): the tile, the goal row and What's ahead read one forecast; the
    label comes from its likelihood (80% or more on track, 20% or less off track, else at risk),
    not from whether the band touches the target."""
    dso = _g(
        key="dso_under_45",
        metric="dso",
        aggregate="value",
        direction="at_most",
        target="45",
        projection="43.0",
        lower="34.0",
        upper="52.0",
        probability=61,
    )
    (g,) = goal_lines(SPEC, ["dso_under_45"], [dso])
    assert g["text"] == (
        "DSO · 45 d target by 31 Mar · tracking 43 d (80% band 34–52 d) · 61% likely"
    )
    assert g["tone"] == "risk"
    (ok,) = goal_lines(SPEC, ["dso_under_45"], [{**dso, "probability": 80}])
    assert ok["tone"] == "ok"
    (off,) = goal_lines(SPEC, ["dso_under_45"], [{**dso, "probability": 20}])
    assert off["tone"] == "off"


SPEC2 = {
    "metrics": {
        "issue_days": {"name": "Days from login to issue", "unit": "days"},
        "policies": {"name": "Policies issued", "unit": "count"},
    }
}


def test_a_day_target_keeps_its_own_decimals():
    """#147: 'at most 3.5 days' printed '4 d'; a goal figure keeps the target's precision."""
    g = _g(
        key="issue_days",
        metric="issue_days",
        aggregate="value",
        direction="at_most",
        target="3.5",
        projection="3.62",
        lower="3.41",
        upper="3.87",
    )
    (line,) = goal_lines(SPEC2, ["issue_days"], [g])
    assert line["text"] == (
        "Days from login to issue · 3.5 d target by 31 Mar · tracking 3.6 d (80% band 3.4–3.9 d)"
    )


def test_a_count_goal_is_whole():
    """#148: '2,27,200.0 target · tracking 2,09,772.8'; counts are whole in goal lines."""
    g = _g(
        key="policies",
        metric="policies",
        target="227200",
        projection="209772.8",
        lower="205000.4",
        upper="214500.6",
    )
    (line,) = goal_lines(SPEC2, ["policies"], [g])
    assert line["text"] == (
        "Policies issued FY27 · 2,27,200 target · tracking 2,09,773 (80% band 2,05,000–2,14,501)"
    )


def test_goal_figures_by_unit():
    from othor.briefs import values
    from othor.ontology.goal_targets import goal_text

    assert values.rounded("days", "3.62", like="3.5") == "3.6 d"
    assert values.rounded("days", "41.2", like="45") == "41 d"
    assert values.goal_target("days", "3.5") == "3.5 d"
    assert values.goal_target("days", "45.00") == "45 d"
    assert values.rounded("days", "41.2") == "41 d"  # no target named: as before
    assert values.rounded("count", "209772.8", like="227200") == "2,09,773"
    assert values.goal_target("count", "227200.0") == "2,27,200"
    assert values.goal_value("count", "209772.8", like="227200") == "2,09,773"
    assert values.goal_value("inr", "600000000", like="600000000") == values.text("inr", 6e8)
    body = {"metric": "issue_days", "direction": "at_most", "target": "3.5", "by": "2027-03-31"}
    assert goal_text(body, "days", {"issue_days": "Days from login to issue"}) == (
        "Days from login to issue at most 3.5 d by 31 Mar 2027"
    )
