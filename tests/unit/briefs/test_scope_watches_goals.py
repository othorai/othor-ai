# SPDX-License-Identifier: Apache-2.0
"""A scoped brief shows a watch or goal only when its metric, or a metric it's computed from, is
in scope; an unscoped brief (Leadership) keeps everything (decided 7 Nov 2026, M12b)."""

from __future__ import annotations

from othor.briefs import scope
from othor.briefs.assemble import goal_lines

SPEC = {
    "metrics": {
        "dso": {"name": "DSO", "unit": "days"},
        "receivables": {"name": "Receivables", "unit": "inr"},
        "cash_cycle": {"name": "Cash cycle", "unit": "days", "components": ["dso", "dpo"]},
        "dpo": {"name": "DPO", "unit": "days"},
        "net_revenue": {"name": "Net revenue", "unit": "inr"},
    }
}
CASH = {"metrics": ["dso", "receivables"], "exclude": {}}
ALL = {"metrics": None, "exclude": {}}


def _goal(key: str, metric: str) -> dict:
    return {
        "key": key,
        "metric": metric,
        "by": "2027-03-31",
        "target": "45",
        "projection": "41",
        "aggregate": "value",
    }


GOALS = [_goal("dso_target", "dso"), _goal("fy27_revenue", "net_revenue")]


def test_a_scoped_brief_keeps_only_the_goal_in_scope() -> None:
    lines = goal_lines(SPEC, ["dso_target", "fy27_revenue"], GOALS, CASH)
    assert [g["key"] for g in lines] == ["dso_target"]


def test_an_unscoped_brief_keeps_every_goal() -> None:
    lines = goal_lines(SPEC, ["dso_target", "fy27_revenue"], GOALS, ALL)
    assert [g["key"] for g in lines] == ["dso_target", "fy27_revenue"]


def test_a_metric_computed_from_one_in_scope_counts() -> None:
    assert scope.goal_in(SPEC, CASH, {"metric": "cash_cycle"})  # computed from dso
    assert not scope.goal_in(SPEC, CASH, {"metric": "net_revenue"})


def test_a_watch_with_no_metric_is_company_wide() -> None:
    silent = {"indicator": {"kind": "finding_slot", "detector": "missing_records"}}
    assert not scope.watch_in(SPEC, CASH, silent)
    assert scope.watch_in(SPEC, ALL, silent)
    assert scope.watch_in(SPEC, CASH, {"indicator": {"kind": "value", "metric": "dso"}})


# --- areas (Advanced Intelligence Phase 0 §8) ---------------------------------------------------

AREAS = {
    "metrics": {
        "dso": {"name": "DSO", "area": "Collections"},
        "net_revenue": {"name": "Net revenue", "area": "Sales"},
        "late_dispatch": {"name": "Late dispatch", "area": "dispatch_fulfilment"},  # a group
        "headcount": {"name": "Headcount"},  # no area: Other numbers
        "cash_cycle": {"name": "Cash cycle", "components": ["dso"]},
    }
}


def _f(metric: str) -> dict:
    return {"metric": metric, "breakdown": "", "breakdown_value": None, "entities": []}


def test_an_area_scope_keeps_its_areas_findings():
    sc = {"metrics": None, "exclude": {}, "areas": ["Collections", "dispatch_fulfilment"]}
    kept = [m for m in AREAS["metrics"] if scope.finding_in(AREAS, sc, _f(m))]
    assert kept == ["dso", "late_dispatch"]
    other = {"metrics": None, "exclude": {}, "areas": ["Other numbers"]}
    assert [m for m in AREAS["metrics"] if scope.finding_in(AREAS, other, _f(m))] == [
        "headcount",
        "cash_cycle",
    ]


def test_areas_and_metrics_both_apply():
    sc = {"metrics": ["dso", "net_revenue"], "exclude": {}, "areas": ["Sales"]}
    assert [m for m in AREAS["metrics"] if scope.finding_in(AREAS, sc, _f(m))] == ["net_revenue"]


def test_an_area_scope_keeps_a_watch_computed_from_its_metrics():
    sc = {"metrics": None, "exclude": {}, "areas": ["Collections"]}
    assert scope.metric_in(AREAS, sc, "cash_cycle")  # computed from DSO
    assert not scope.metric_in(AREAS, sc, "net_revenue")
    assert not scope.metric_in(AREAS, sc, None)  # company-wide: unscoped briefs only
    assert scope.metric_in(AREAS, {"metrics": None, "exclude": {}}, None)
