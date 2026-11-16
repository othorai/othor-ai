# SPDX-License-Identifier: Apache-2.0
"""Where is it? (2nb) and which table? (2nc): candidates found by code across every connected
source, never a personal column, each with what the owner needs to choose."""

from __future__ import annotations

from othor.core.tenancy import tenant_scope
from othor.ontology.compiler.catalog import load_catalog
from othor.setup.candidates import column_candidates, table_candidates
from tests.fixtures.sample_company.catalog import catalog_from_generator


def _cat(session_factory, two_companies, sample):
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        catalog_from_generator(s, sample)
        return load_catalog(s)


def test_metres_shipped_is_found_in_the_dispatch_table(session_factory, two_companies, sample):
    cat = _cat(session_factory, two_companies, sample)
    got = column_candidates(cat, "metres shipped", near=("erp_db", "erp_db", "invoices"))
    first = got[0]
    assert (first.table.name, first.column) == ("fact_dispatch", "dispatch_mtrs")
    assert first.unit == "metres" and first.rows > 0
    assert "invoice_no" in first.joins  # shared key with the metric's table
    assert all(not c.personal for c in got)
    assert len(got) <= 4


def test_orders_tables_in_postgres(session_factory, two_companies, sample):
    cat = _cat(session_factory, two_companies, sample)
    got = table_candidates(cat, "open orders not yet invoiced")
    assert got[0].table.name == "fact_order"
    assert {"status", "amount"} <= set(got[0].useful)


def test_no_personal_columns_ever(session_factory, two_companies, sample):
    cat = _cat(session_factory, two_companies, sample)
    for words in ("phone", "email employee name", "bank account"):
        assert all(not c.personal for c in column_candidates(cat, words))
