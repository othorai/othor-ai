# SPDX-License-Identifier: Apache-2.0
"""A plug-in's proposal kind (Advanced Intelligence Phase 0 §2): it shows on Home with its own
tag and "from" line, Accept writes its Ontology change as a version, Undo takes it back, and a
core kind can't be taken."""

from __future__ import annotations

import datetime as dt

import pytest

from othor.core.tenancy import tenant_scope
from othor.engine import proposals
from othor.engine.models import Proposal
from othor.ontology import service
from othor.web.home_view import proposal_rows

NOW = dt.datetime(2026, 11, 17, 9, tzinfo=dt.UTC)


def _changes(s, p):
    rec = next(r for r in service.active(s, ["metric"]) if r.key == p.evidence["metric"])
    ref = {"proposal": str(p.id)}
    return [service.put("metric", rec.key, {**rec.body, "area": "Operations"}, ref)]


@pytest.fixture
def kind():
    k = proposals.register_proposal(
        "demo_link", "Outside link", lambda p: "Outside · " + p.evidence["metric"], _changes
    )
    yield k
    proposals.PROPOSALS.pop("demo_link", None)


def test_a_core_kind_cant_be_taken():
    for core in ("goal", "areas", "join", "suppression"):
        with pytest.raises(ValueError):
            proposals.register_proposal(core, "x", lambda p: "", lambda s, p: [])


def test_a_taken_kind_cant_be_replaced(kind):
    with pytest.raises(ValueError):
        proposals.register_proposal("demo_link", "x", lambda p: "", lambda s, p: [])


def test_a_plugin_kind_shows_applies_and_undoes(world, kind):
    with world.sf() as s, tenant_scope(world.cid):
        before = service.current_number(s)
        area = next(r for r in service.active(s, ["metric"]) if r.key == "net_revenue").body.get(
            "area"
        )
        p = Proposal(
            kind="demo_link",
            text="Link diesel to Net revenue",
            evidence={"metric": "net_revenue", "line": "computed line", "confidence": "high"},
            origin={},
            fingerprint="demo_link:net_revenue",
            status="open",
        )
        s.add(p)
        s.flush()
        (row,) = [r for r in proposal_rows(s) if r["id"] == str(p.id)]
        assert row["kind"] == "Outside link"
        assert row["from"] == "Outside · net_revenue"
        assert row["confidence"] == "High"

        proposals.decide(s, p, accept=True, actor="owner", now=NOW)
        assert p.status == "accepted" and p.applied_version == service.current_number(s) > before
        rec = next(r for r in service.active(s, ["metric"]) if r.key == "net_revenue")
        assert rec.body["area"] == "Operations"

        proposals.undo(s, p, actor="owner", now=NOW)
        assert p.status == "open"
        rec = next(r for r in service.active(s, ["metric"]) if r.key == "net_revenue")
        assert rec.body.get("area") == area
        s.commit()
