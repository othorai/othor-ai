# SPDX-License-Identifier: Apache-2.0
"""Rule drafts without a model (decided 4 Nov): from upload columns, amount columns by date and
row counts, with the confirmed rules applied. Low confidence, Needs confirmation, rule cited."""

from __future__ import annotations

import sqlalchemy as sa

from othor.ai.models import ModelCall
from othor.core.tenancy import tenant_scope
from othor.ontology.compiler.catalog import load_catalog
from othor.setup import parse
from othor.setup.rules_draft import MAX_DRAFTS, rule_drafts
from tests.fixtures.sample_company.catalog import catalog_from_generator
from tests.fixtures.sample_company.uploads import DICTIONARY, MIS, RECV_SQL


def _refs(upload_files):
    out = []
    for name in (MIS, RECV_SQL, DICTIONARY):
        got = parse.parse_file(upload_files[name].read_bytes(), parse.kind_of(name), name)
        out += [(name, r) for r in got.refs]
    return out


def test_rule_drafts_on_the_sample_company(session_factory, two_companies, sample, upload_files):
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        catalog_from_generator(s, sample)
        cat = load_catalog(s)
        drafts = rule_drafts(cat, _refs(upload_files), rules=[], vocabulary=[])
        assert s.scalar(sa.select(sa.func.count()).select_from(ModelCall)) == 0
    keys = [d.key for d in drafts]
    assert {"net_revenue", "receivables_over_60", "metres_dispatched"} <= set(keys), keys
    assert len(drafts) <= MAX_DRAFTS
    assert all(d.confidence == "low" and d.state == "needs_confirmation" for d in drafts)
    assert all(d.origin["by"] == "rules" and d.origin["rule"].startswith("Rule:") for d in drafts)
    by = {d.key: d for d in drafts}
    # matched an upload column: cited, and ranked first
    nr = by["net_revenue"]
    assert "matched MIS column 'Sales incl. GST (₹ L)'" in nr.origin["rule"]
    assert any(r["locator"] == "tab Sales!D1" for r in nr.refs)
    assert keys.index("net_revenue") < keys.index("metres_dispatched")
    # receivables over 60 days: the threshold read from the SQL, cited by line
    ro = by["receivables_over_60"]
    assert ro.body["kind"] == "snapshot"
    assert any(r["locator"] == "line 12" for r in ro.refs)
    # metres: the amount-like column of the dispatch table, by its date
    md = by["metres_dispatched"]
    assert (
        md.body["measure"]["expr"] == {"col": "dispatch_mtrs"}
        and md.body["date"] == "dispatch_date"
    )
    assert any(r["upload"] == DICTIONARY and r["locator"] == "p. 12" for r in md.refs)
    # never a personal column, never an Excluded or ignored table
    personal = {(t.name, c) for t in cat.tables.values() for c in t.personal}
    for d in drafts:
        t = d.body["table"]["table"]
        assert cat.by_name(d.body["table"]["source"], t).use in ("metric_source", "dimension")
        cols = {n["col"] for n in _nodes(d.body) if "col" in n}
        assert not any((t, c.lower()) in personal for c in cols)


def test_drafts_are_deterministic(session_factory, two_companies, sample, upload_files):
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        catalog_from_generator(s, sample)
        cat = load_catalog(s)
        a = rule_drafts(cat, _refs(upload_files), rules=[], vocabulary=[])
        b = rule_drafts(cat, list(reversed(_refs(upload_files))), rules=[], vocabulary=[])
    assert [(d.key, d.body) for d in a] == [(d.key, d.body) for d in b]


def test_confirmed_rules_are_applied(session_factory, two_companies, sample, upload_files):
    """(c): a filter rule on invoices is carried into the drafts that read invoices."""
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        catalog_from_generator(s, sample)
        cat = load_catalog(s)
        rule = {
            "kind": "filter",
            "table": {"source": "erp_db", "schema": "erp_db", "table": "invoices"},
            "expr": {"op": "<>", "args": [{"col": "status"}, {"lit": "X"}]},
        }
        drafts = rule_drafts(
            cat, _refs(upload_files), rules=[("cancelled_invoices", rule)], vocabulary=[]
        )
    nr = next(d for d in drafts if d.key == "net_revenue")
    assert "cancelled_invoices" in nr.origin["rule"]


def _nodes(body):
    from othor.ontology.types import walk

    out = []
    for e in [body.get("measure", {}).get("expr"), *body.get("filters", [])]:
        if e:
            out += walk(e)
    return out


def test_a_discount_amount_is_money_not_a_count() -> None:
    """2o (M12b): "discount_amount" contains "count" but is ₹."""
    from othor.setup.rules_draft import _unit_of

    assert _unit_of("discount_amount") == "inr"
    assert _unit_of("qty") == "count" and _unit_of("order_count") == "count"
    assert _unit_of("qty_mtr") == "metres"


def test_acronyms_stay_upper_case_in_drafted_names() -> None:
    from othor.setup.rules_draft import _first_upper, _humanise

    assert _first_upper(_humanise("ar_open_items")) == "AR open items"
    assert _first_upper(_humanise("bank_ledger")) == "Bank ledger"


def test_plain_metric_names() -> None:
    """M11c: a rule-drafted metric is named in words, not table.column. Only the name: the key,
    the source column and the definition stay as they were."""
    from othor.setup.rules_draft import plain_name

    for table, col, name in (
        ("fact_order", "amount", "Order value"),
        ("fact_invoice", "amount", "Invoice value"),
        ("fact_dispatch", "dispatch_mtrs", "Metres dispatched"),
        ("fact_returns", "qty_mtr", "Metres returned"),
        ("approvals", "discount_amount", "Discounts approved"),
        ("bank_ledger", "credit", "Bank credits"),
        ("cost_ledger", "amount", "Costs"),
        ("credit_notes", "net_amount", "Credit notes"),
        ("ar_open_items", "amount_open", "AR open items"),
        ("agg_sales_monthly", "amount", "Sales"),
        ("fact_order", None, "Orders · count"),
    ):
        assert plain_name(table, col) == name, (table, col)
