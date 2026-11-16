# SPDX-License-Identifier: Apache-2.0
"""What we found in your data (2n), rules only, on the sample sources."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from othor.connectors.models import SourceColumn, SourceTable
from othor.connectors.sql.engine import make_engine
from othor.connectors.sql.query_log import QueryLog
from othor.core.tenancy import tenant_scope
from othor.ontology.compiler.catalog import load_catalog
from othor.setup.discover import discover, make_query
from tests.fixtures.sample_company.catalog import catalog_from_generator
from tests.fixtures.sample_company.generate import generate
from tests.integration.test_sql_connectors import my, pg


@pytest.fixture
def found(session_factory, two_companies, db_url, sample_sources_seeded):
    if not db_url.startswith("sqlite"):
        pytest.skip("one metadata DB is enough")
    from othor.connectors.sql.scan import check_connection
    from tests.conftest import require_services

    if check_connection(my("othor_ro")).message.startswith("Can't reach"):
        require_services("sample sources")
    sample = generate()
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        catalog_from_generator(s, sample)
        import sqlalchemy as sa

        # the scan's date statistics the overlap and gap rules read
        for name, col, lo, hi in (
            ("invoices", "invoice_date", "2024-04-01", "2026-11-03"),
            ("fact_invoice", "invoice_date", "2021-04-01", "2026-11-03"),
        ):
            c = s.scalars(
                sa.select(SourceColumn)
                .join(SourceTable, SourceTable.id == SourceColumn.table_id)
                .where(SourceTable.name == name, SourceColumn.name == col)
            ).one()
            c.stats = {"min": lo, "max": hi}
        s.flush()
        cat = load_catalog(s)
        engines = {"erp_db": make_engine(my("othor_ro")), "sales_dw": make_engine(pg("othor_ro"))}
        log = QueryLog(
            personal={t.name.lower(): t.personal for t in cat.tables.values() if t.personal}
        )
        try:
            out = discover(s, cat, {"erp_db"}, make_query(log, engines), dt.date(2026, 11, 4))
        finally:
            for e in engines.values():
                e.dispose()
    # one finding per kind, except dimension: the plant codes and the splits (12 Nov 2026)
    return {(d.key if d.type == "dimension" else d.type): d for d in out}, sample


def test_five_kinds(found):
    by, _ = found
    assert set(by) == {"overlap", "currency", "plant_codes", "splits", "pattern", "gap"}
    assert by["overlap"].text == (
        "Invoices are in both erp_db and sales_dw from Apr 2024. "
        "erp_db is used as the book of record."
    )
    assert by["gap"].text == "sales_dw.fact_invoice has no rows before Apr 2021."
    assert (
        by["plant_codes"].text
        == "Item codes starting CB- and SU- map to the Coimbatore and Surat plants."
    )
    assert by["plant_codes"].rules[0][2]["entries"] == {"CB-": "Coimbatore", "SU-": "Surat"}
    assert by["splits"].text == (
        "Your numbers can be split by Region, Segment, Product family and Customer."
    )
    assert by["pattern"].rules[0][1] == "quarter_end_push"


def test_currency_share_is_computed(found):
    by, sample = found
    inv = sample.erp["invoices"]
    eur = Decimal(str(inv.loc[inv["currency"] == "EUR", "net_amount"].sum()))
    share = eur / Decimal(str(inv["net_amount"].sum()))
    assert Decimal(by["currency"].evidence["share"]).quantize(Decimal("0.0001")) == share.quantize(
        Decimal("0.0001")
    )
    assert "billed in EUR" in by["currency"].text


def test_every_proposed_rule_is_a_valid_record(found):
    from othor.ontology.types import validate_body

    by, _ = found
    for d in by.values():
        for type_, _key, body in d.rules:
            validate_body(type_, body)
