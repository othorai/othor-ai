# SPDX-License-Identifier: Apache-2.0
"""Othor AI drafts the areas (M16.1, `metric_areas` v1): after seal, from the listed numbers'
names and definitions only, as one Home proposal. Nothing changes until the owner accepts it; a
key the model invents or an area off the short list is dropped; a decline is never asked again;
with no model there is no proposal; an area the owner set wins over the draft."""

from __future__ import annotations

import datetime as dt
import json

import sqlalchemy as sa

from othor.ai.gateway import ModelGateway, ProviderReply, TemplateProvider
from othor.ai.models import ModelCall
from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.engine import proposals
from othor.engine.models import Proposal
from othor.home import areas_ai
from othor.ontology import service
from othor.web.home_view import proposal_rows

NOW = dt.datetime(2026, 11, 15, 9, tzinfo=dt.UTC)


class Scripted:
    name = "fake"
    configured = True

    def __init__(self, out: dict) -> None:
        self.out = out
        self.sent: list = []

    def call(self, system, messages, schema, max_tokens=0):
        self.sent.append(messages)
        return ProviderReply(json.dumps(self.out), "fake-model", 10, 10)


DRAFT = {
    "areas": [
        {"key": "net_revenue", "area": "Sales"},
        {"key": "orders", "area": "Sales"},
        {"key": "dso", "area": "Collections"},
        {"key": "cash_balance", "area": "Finance"},
        {"key": "metres_dispatched", "area": "Operations"},
        {"key": "made_up", "area": "Sales"},  # not a number of theirs
        {"key": "returns_rate", "area": "Quality"},  # off the short list
        {"key": "active_customers", "area": "Other"},  # stays without an area
    ]
}


def _no_areas(world) -> None:
    """The sample company as a company set up before M16.1: no metric has an area."""
    with world.sf() as s, tenant_scope(world.cid):
        changes = [
            service.put("metric", r.key, {k: v for k, v in r.body.items() if k != "area"})
            for r in service.active(s, ["metric"])
            if r.body.get("area")
        ]
        service.apply(s, changes, actor="o", reason="before M16.1")
        s.commit()


def _area(s, key):
    return next(r for r in service.active(s, ["metric"]) if r.key == key).body.get("area")


def test_the_draft_arrives_as_one_proposal_and_applies_on_accept(world) -> None:
    _no_areas(world)
    fake = Scripted(DRAFT)
    gw = ModelGateway(world.sf, fake)
    with world.sf() as s, tenant_scope(world.cid):
        before = service.current_number(s)
        p = areas_ai.propose_areas(s, gw, world.cid)
        assert p is not None and p.kind == "areas" and p.text == "Group your numbers into areas"
        assert service.current_number(s) == before  # nothing changes before Accept
        assert p.evidence["areas"] == {
            "cash_balance": "Finance",
            "dso": "Collections",
            "metres_dispatched": "Operations",
            "net_revenue": "Sales",
            "orders": "Sales",
        }
        assert p.evidence["line"].startswith("Sales: Net revenue, Orders · Collections: DSO")
        (row,) = [r for r in proposal_rows(s) if r["kind"] == "Areas"]
        assert row["from"] == "Ontology · your numbers"
        assert s.scalars(sa.select(Event).where(Event.action == "proposal.created")).first()

        # the owner set DSO's area meanwhile: theirs wins
        rec = next(r for r in service.active(s, ["metric"]) if r.key == "dso")
        service.apply(
            s,
            [service.put("metric", "dso", {**rec.body, "area": "Finance"})],
            actor="o",
            reason="Area changed",
        )
        proposals.decide(s, p, accept=True, actor="owner", now=NOW)
        assert p.status == "accepted" and p.applied_version == service.current_number(s)
        assert _area(s, "net_revenue") == "Sales"
        assert _area(s, "dso") == "Finance"
        assert _area(s, "active_customers") is None
        assert _area(s, "returns_rate") is None
        s.commit()

    sent = json.dumps(fake.sent)
    assert "Net revenue" in sent and "net_revenue" in sent
    for no in ("value", "rows", "points", "table", "measure", "formula"):
        assert f'"{no}"' not in sent  # names and definitions only
    with world.sf() as s, tenant_scope(world.cid):
        (call,) = s.scalars(sa.select(ModelCall).where(ModelCall.prompt_id == "metric_areas"))
        assert call.prompt_version == "v1"
        # what the model left in Other is not asked again
        assert areas_ai.propose_areas(s, gw, world.cid) is None


def test_a_decline_is_not_asked_again(world) -> None:
    _no_areas(world)
    gw = ModelGateway(world.sf, Scripted(DRAFT))
    with world.sf() as s, tenant_scope(world.cid):
        p = areas_ai.propose_areas(s, gw, world.cid)
        assert p is not None
        assert areas_ai.propose_areas(s, gw, world.cid) is None  # one open at a time
        proposals.decide(s, p, accept=False, actor="owner", now=NOW)
        assert areas_ai.propose_areas(s, gw, world.cid) is None
        assert _area(s, "net_revenue") is None


def test_no_model_no_proposal(world) -> None:
    _no_areas(world)
    with world.sf() as s, tenant_scope(world.cid):
        gw = ModelGateway(world.sf, TemplateProvider())
        assert areas_ai.propose_areas(s, gw, world.cid) is None
        assert not s.scalars(sa.select(Proposal).where(Proposal.kind == "areas")).first()


def test_every_number_has_an_area_no_call(world) -> None:
    fake = Scripted(DRAFT)
    with world.sf() as s, tenant_scope(world.cid):
        assert areas_ai.propose_areas(s, ModelGateway(world.sf, fake), world.cid) is None
    assert fake.sent == []


def test_one_companys_proposal_never_shows_in_another(world, two_companies) -> None:
    _no_areas(world)
    _, other = two_companies
    with world.sf() as s, tenant_scope(world.cid):
        assert areas_ai.propose_areas(s, ModelGateway(world.sf, Scripted(DRAFT)), world.cid)
        s.commit()
    with world.sf() as s, tenant_scope(other):
        assert not [r for r in proposal_rows(s) if r["kind"] == "Areas"]
