# SPDX-License-Identifier: Apache-2.0
"""Home proposals (M10a, docs/04 §10): a goal at risk and a contradiction answered twice are
proposed once; Accept writes one Ontology version, Decline is remembered, Undo writes a version
putting the records back (the compiled spec is the one before the Accept)."""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import sqlalchemy as sa

from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.engine import proposals
from othor.engine.detect import GOAL_AT_RISK
from othor.engine.models import Proposal
from othor.ontology import service
from othor.ontology.compiler import compile_version
from othor.ontology.models import Question

NOW = dt.datetime(2026, 11, 2, 9, 0, tzinfo=dt.UTC)
RUN = SimpleNamespace(id=uuid.uuid4(), run_date=dt.date(2026, 11, 1), number=31)


def _goal_result(**slots):
    return {
        "metric": "net_revenue",
        "reason": GOAL_AT_RISK,
        "slots": {
            "kind": "goal",
            "goal": "fy27_net_revenue",
            "name": "Net revenue FY27",
            "target": "600000000",
            "projection": "571948559.00",
            "lower": "559000000.00",
            "upper": "585000000.00",
            "short_by": "28051441.00",
            "by": "2027-03-31",
            "direction": "at_least",
            **slots,
        },
    }


def _content(spec):
    """The compiled spec without its version number (a new version has a new number)."""
    return {k: v for k, v in spec.compiled.items() if k != "ontology_version"}


def _goal_body(s):
    return next(r.body for r in service.active(s, ["goal"]) if r.key == "fy27_net_revenue")


def test_a_goal_at_risk_is_proposed_once_with_computed_figures(world):
    with world.sf() as s, tenant_scope(world.cid):
        spec = compile_version(s, world.company(s), actor="t").compiled
        (p,) = proposals.goal(s, RUN, spec, [_goal_result(), {"slots": {}, "reason": None}])
        assert p.kind == "goal" and p.fingerprint == "goal:fy27_net_revenue:FY27"
        assert p.text == "Net revenue FY27 target ₹60 Cr → tracking ₹57.2 Cr"
        assert p.evidence["line"].startswith("Mark as at risk · short by ₹2.81 Cr · 80% band ₹55.9")
        assert p.evidence["confidence"] == "high"  # the whole band is under the target
        assert proposals.goal(s, RUN, spec, [_goal_result()]) == []  # never proposed twice
        inside = _goal_result(goal="other", upper="610000000.00")
        (q,) = proposals.goal(s, RUN, spec, [inside])
        assert q.evidence["confidence"] == "medium"


def test_accept_marks_the_goal_at_risk_and_undo_puts_it_back(world):
    with world.sf() as s, tenant_scope(world.cid):
        company = world.company(s)
        before = compile_version(s, company, actor="t")
        spec = before.compiled
        (p,) = proposals.goal(s, RUN, spec, [_goal_result()])
        n = service.current_number(s)
        proposals.decide(s, p, accept=True, actor="owner", now=NOW)
        assert (p.status, p.applied_version, p.decided_by) == ("accepted", n + 1, "owner")
        assert _goal_body(s)["at_risk_since"] == "2026-11-01"
        assert _content(compile_version(s, company, actor="t")) != _content(before)
        proposals.undo(s, p, actor="owner", now=NOW)
        assert p.status == "open" and p.applied_version is None
        assert "at_risk_since" not in _goal_body(s)
        assert _content(compile_version(s, company, actor="t")) == _content(before)
        acts = [e.action for e in s.scalars(sa.select(Event).order_by(Event.at))]
        assert "proposal.accepted" in acts and "proposal.undone" in acts


def test_accept_a_suppression_writes_a_confirmed_annual_pattern(world):
    with world.sf() as s, tenant_scope(world.cid):
        p = Proposal(
            kind="suppression",
            text="Net revenue: the same changepoint finding around 26 Dec – 31 Dec. Mark it?",
            evidence={
                "years": [2024, 2025, 2026],
                "window": ["2026-12-26", "2026-12-31"],
                "fingerprint": "fp1",
                "metric": "net_revenue",
                "name": "Net revenue, quarter-end push around 26–31 Dec",
                "confidence": "high",
            },
            origin={"rule": "recurs_on_calendar_window", "finding": "fp1"},
            fingerprint="suppression:changepoint|net_revenue|week",
            status="open",
        )
        s.add(p)
        s.flush()
        proposals.decide(s, p, accept=True, actor="owner", now=NOW)
        (rec,) = [r for r in service.active(s, ["known_pattern"]) if r.key.startswith("proposed_")]
        assert rec.body["window"] == {
            "kind": "annual",
            "start": "12-26",
            "end": "12-31",
            "dates": [],
        }
        assert rec.body["metrics"] == ["net_revenue"] and rec.body["confirmed"] is True
        assert rec.source_ref == {"proposal": str(p.id)}
        spec = compile_version(s, world.company(s), actor="t").compiled
        assert any(x["key"] == rec.key for x in spec["suppressions"])


def test_decline_is_remembered_and_can_be_undone(world):
    with world.sf() as s, tenant_scope(world.cid):
        spec = compile_version(s, world.company(s), actor="t").compiled
        (p,) = proposals.goal(s, RUN, spec, [_goal_result()])
        n = service.current_number(s)
        proposals.decide(s, p, accept=False, actor="owner", now=NOW)
        assert p.status == "declined" and service.current_number(s) == n  # no version
        proposals.decide(s, p, accept=True, actor="owner", now=NOW)  # decided: no-op
        assert p.status == "declined"
        assert proposals.goal(s, RUN, spec, [_goal_result()]) == []
        proposals.undo(s, p, actor="owner", now=NOW)
        assert p.status == "open" and service.current_number(s) == n


def _answered(s, side, n):
    for _i in range(n):
        s.add(
            Question(
                text="Which figure is right?",
                why="",
                origin={},
                quick_replies=[],
                fingerprint=f"q{side}{uuid.uuid4().hex[:8]}",
                kind="needs_confirmation",
                subject={"metric": "net_revenue", "sides": ["erp_db", "MIS"]},
                status="answered",
                answer_text=side,
            )
        )
    s.flush()


def test_a_contradiction_answered_the_same_way_twice_proposes_a_book_of_record(world):
    with world.sf() as s, tenant_scope(world.cid):
        spec = compile_version(s, world.company(s), actor="t").compiled
        spec = {
            **spec,
            "rules": [r for r in spec.get("rules", []) if r["kind"] != "book_of_record"],
        }
        _answered(s, "erp_db", 1)
        _answered(s, "MIS", 1)
        assert proposals.definition(s, RUN, spec) == []
        _answered(s, "MIS", 1)
        (p,) = proposals.definition(s, RUN, spec)
        assert p.kind == "definition_change" and p.evidence["rule"]["source"] == "MIS"
        assert p.evidence["rule"]["over"] == ["erp_db"]
        proposals.decide(s, p, accept=True, actor="owner", now=NOW)
        assert any(r.key == p.evidence["key"] for r in service.active(s, ["rule"]))
