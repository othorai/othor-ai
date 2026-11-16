# SPDX-License-Identifier: Apache-2.0
"""Breakdowns found by rule (12 Nov 2026): Indigo Thread on production confirmed 16 numbers with no
breakdown, so 10 of 11 Sweep checks couldn't run and the first brief was quiet. Setup now finds
the ways numbers can be split from the scan alone (column names and table uses, never row values,
never personal columns), with the one-hop joins they need."""

from __future__ import annotations

from othor.core.tenancy import tenant_scope
from othor.ontology.compiler.catalog import load_catalog
from othor.setup.discover import splits
from tests.fixtures.sample_company.catalog import catalog_from_generator


def _cat(session_factory, two_companies, sample):
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        catalog_from_generator(s, sample)
        return load_catalog(s)


def _records(found):
    return {(t, k): b for d in found for t, k, b in d.rules}


def test_splits_propose_breakdowns_and_the_joins_they_need(session_factory, two_companies, sample):
    cat = _cat(session_factory, two_companies, sample)
    found = splits(cat)
    assert len(found) == 1 and found[0].type == "dimension" and found[0].key == "splits"
    rec = _records(found)
    bds = {k: b for (t, k), b in rec.items() if t == "breakdown"}
    # region and segment live on the customer tables; customer is the customer's name there
    assert {"region", "segment", "customer"} <= set(bds)
    assert bds["region"]["column"] == "region"
    tables = {
        bds["region"].get("table"),
        *(a["table"] for a in bds["region"].get("alternatives", [])),
    }
    assert "customers" in tables
    assert bds["customer"]["column"] in ("name", "customer_name")
    # the joins: fact tables to the customer table on the key they share, one hop
    joins = {k: b for (t, k), b in rec.items() if t == "rule" and b.get("kind") == "join"}
    inv = next(b for b in joins.values() if b["table"]["table"] == "invoices")
    assert inv["to"]["table"] == "customers" and inv["on"] == [["customer_id", "customer_id"]]
    # never a personal column, and plant is left to the item-code rule
    for b in bds.values():
        assert b["column"] not in ("contact_email", "contact_phone")
    assert "plant" not in bds
    # the sentence comes from the names found
    assert "Region" in found[0].text and "Customer" in found[0].text


def test_splits_skip_what_the_ontology_already_has(session_factory, two_companies, sample):
    cat = _cat(session_factory, two_companies, sample)
    found = splits(cat, known={"region", "segment", "customer", "product_family"})
    rec = _records(found)
    assert not {k for (t, k) in rec if t == "breakdown"} & {"region", "segment", "customer"}


def test_confirm_gives_metrics_the_breakdowns_they_can_reach(
    session_factory, two_companies, sample
):
    """Drafting runs before the breakdowns exist, so metrics came out with none; at Confirm each
    gets the breakdowns its tables reach (the compiler decides), at most 4, in a fixed order."""
    from othor.setup.compute import MAX_BREAKDOWNS, attach_breakdowns
    from othor.setup.rules_draft import rule_drafts

    cat = _cat(session_factory, two_companies, sample)
    rec = _records(splits(cat))
    metrics = {}
    for d in rule_drafts(cat, [], rules=[], vocabulary=[]):
        body = {**d.body, "status": "active", "breakdowns": []}
        metrics[("metric", d.key)] = body
    assert metrics, "the rules draft some metrics from the sample's tables"
    company = {"currency": "INR", "fiscal_year_start": 4, "timezone": "Asia/Kolkata"}
    got = attach_breakdowns({}, {**rec, **metrics}, cat, company)
    assert got, "at least one metric reaches a breakdown"
    for _key, bks in got.items():
        assert 1 <= len(bks) <= MAX_BREAKDOWNS
        assert bks == sorted(
            bks,
            key=lambda k: (
                ["plant", "region", "segment", "customer", "product_family", "channel"].index(k)
                if k in ("plant", "region", "segment", "customer", "product_family", "channel")
                else 99
            ),
        )
    # a revenue-like metric on invoices gets region and customer
    rev = next((k for k in got if "revenue" in k or "invoice" in k), None)
    assert rev is not None and {"region", "customer"} <= set(got[rev]), got


def test_a_company_already_set_up_gets_the_splits_as_a_home_proposal(
    session_factory, two_companies, sample
):
    """Indigo Thread was set up before splits were found by rule: its numbers get them through
    one Home proposal. Nothing changes until Accept; Accept writes the breakdowns, the joins and
    each metric's breakdowns as one version; it is never proposed twice."""
    import datetime as dt

    from othor.core.models import Company
    from othor.engine import proposals
    from othor.ontology import service
    from othor.ontology.types import validate_body
    from othor.setup.discover import propose_splits
    from othor.setup.rules_draft import rule_drafts

    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        catalog_from_generator(s, sample)
        cat = load_catalog(s)
        changes = [
            service.put(
                "metric",
                d.key,
                validate_body("metric", {**d.body, "status": "active", "breakdowns": []}),
                {"test": True},
            )
            for d in rule_drafts(cat, [], rules=[], vocabulary=[])
        ]
        service.apply(s, changes, actor="test", reason="Setup", allow_empty=True)
        service.confirm(s, actor="test")
        company = s.get(Company, cid)
        before = service.current_number(s)

        p = propose_splits(s, company)
        assert p is not None and p.kind == "breakdowns" and p.status == "open"
        assert p.text.startswith("Split your numbers by ") and "Region" in p.text
        assert service.current_number(s) == before  # nothing changes before Accept

        proposals.decide(
            s, p, accept=True, actor="test", now=dt.datetime(2026, 11, 12, 9, tzinfo=dt.UTC)
        )
        assert p.status == "accepted" and service.current_number(s) == before + 1
        bds = {r.key for r in service.active(s, ["breakdown"])}
        assert {"region", "customer"} <= bds
        split = [r for r in service.active(s, ["metric"]) if (r.body or {}).get("breakdowns")]
        assert split, "metrics now carry breakdowns"
        assert propose_splits(s, company) is None  # proposed once


def test_offer_splits_never_fails_its_caller(two_companies, session_factory, monkeypatch) -> None:
    """Called after every scan and every Add: an error is swallowed (logged by type only) and
    the session stays usable."""
    import sqlalchemy as sa

    from othor.core.tenancy import tenant_scope
    from othor.setup import discover

    def boom(s, company):
        raise RuntimeError("secret row value")

    monkeypatch.setattr(discover, "propose_splits", boom)
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        assert discover.offer_splits(s, cid) is None
        assert s.execute(sa.text("select 1")).scalar() == 1
