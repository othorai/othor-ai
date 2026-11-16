# SPDX-License-Identifier: Apache-2.0
"""Default use and personal columns, from names, types and statistics only (docs/05, docs/11)."""

from __future__ import annotations

import pytest

from othor.connectors.classify import Col, TableFacts, classify, personal_kind
from tests.fixtures.sample_company import extras
from tests.unit.connectors.conftest import TODAY

EXPECTED = {
    # docs/11 source table and the scan boards 2k–2kd
    "public.employee": ("excluded", None),
    "sales_dw.crm_contacts": ("excluded", None),
    "sales_dw.invoice_contacts": ("excluded", None),
    "public.stg_orders": ("ignored", "staging"),
    "public.etl_log": ("ignored", "log"),
    "public.invoice_print_log": ("ignored", "log"),
    "sales_dw.tmp_load": ("ignored", "empty"),
    "sales_dw.fact_invoice_2019": ("ignored", "old"),
    "sales_dw.fact_invoice_bak_2023": ("ignored", "backup"),
    "sales_dw.fact_invoice": ("metric_source", None),
    "sales_dw.fact_invoice_line": ("metric_source", None),
    "sales_dw.fact_order": ("metric_source", None),
    "sales_dw.fact_order_line": ("metric_source", None),
    "sales_dw.fact_dispatch": ("metric_source", None),
    "sales_dw.fact_returns": ("metric_source", None),
    "public.ar_open_items": ("metric_source", None),
    "sales_dw.dim_customer": ("dimension", None),
    "sales_dw.dim_item": ("dimension", None),
    "sales_dw.dim_invoice_type": ("dimension", None),
    "public.dim_region": ("dimension", None),
    "erp_db.invoices": ("metric_source", None),
    "erp_db.invoice_lines": ("metric_source", None),
    "erp_db.credit_notes": ("metric_source", None),
    "erp_db.receivables": ("metric_source", None),
    "erp_db.bank_ledger": ("metric_source", None),
    "erp_db.cost_ledger": ("metric_source", None),
    "erp_db.customers": ("dimension", None),
    "erp_db.items": ("dimension", None),
}


@pytest.mark.parametrize(("key", "want"), sorted(EXPECTED.items()))
def test_named_tables(all_tables, key, want):
    got = classify(all_tables[key], TODAY)
    assert (got.use, got.reason) == want, key


FILLER_REASON = {"backup": "backup", "empty": "empty", "log": "log", "old": "old"}


def test_fillers(all_tables):
    """Backup, empty, log and old fillers are Ignored with that reason; ref and fact are used."""
    pools = [("erp_db", extras.ERP_FILLERS)] + [(s, pool) for s, pool in extras.DW_FILLERS.items()]
    checked = 0
    for schema, pool in pools:
        for name, kind in pool:
            t = all_tables.get(f"{schema}.{name}")
            if t is None:  # not every filler is loaded
                continue
            got = classify(t, TODAY)
            if kind in FILLER_REASON:
                assert (got.use, got.reason) == ("ignored", FILLER_REASON[kind]), name
            else:
                assert got.use in ("metric_source", "dimension"), name
            checked += 1
    assert checked > 70


def test_personal_columns_match_the_fixture(all_tables):
    """Exactly the generator's personal columns are found, by name, never by value."""
    want = {(s, t, c) for src, s, t, c in extras.PERSONAL if src != "gsheets"}
    got = set()
    for t in all_tables.values():
        for col, _kind in classify(t, TODAY).personal:
            got.add((t.schema, t.name, col))
    assert got == want, (got - want, want - got)


def test_customers_stays_a_dimension_with_two_personal_columns(all_tables):
    got = classify(all_tables["erp_db.customers"], TODAY)
    assert got.use == "dimension"
    assert {c for c, _ in got.personal} == {"contact_email", "contact_phone"}


@pytest.mark.parametrize(
    ("column", "people", "kind"),
    [
        ("email", False, "email"),
        ("contact_email", False, "email"),
        ("phone", False, "phone"),
        ("mobile_no", False, "phone"),
        ("pan", False, "pan"),
        ("pan_no", False, "pan"),
        ("aadhaar", False, "aadhaar"),
        ("bank_account", False, "bank_account"),
        ("ifsc", False, "ifsc"),
        ("address", False, "address"),
        ("dob", False, "dob"),
        ("date_of_birth", False, "dob"),
        ("salary", False, "salary"),
        ("full_name", False, "name"),
        ("name", True, "name"),
        ("name", False, None),  # a customer's or item's name is not personal data
        ("customer_name", False, None),
        ("company", False, None),
        ("gstin", False, None),
        ("panel_count", False, None),
        ("expand", False, None),
    ],
)
def test_personal_kind(column, people, kind):
    assert personal_kind(column, people_table=people) == kind


def test_people_table_with_personal_columns_is_excluded():
    t = TableFacts(
        "hr",
        "staff_master",
        (Col("staff_id", "int"), Col("name", "text"), Col("phone", "text")),
        120,
        None,
    )
    got = classify(t, TODAY)
    assert got.use == "excluded"
    assert {c for c, _ in got.personal} == {"name", "phone"}


def test_three_personal_kinds_exclude_any_table():
    cols = (Col("id", "int"), Col("email", "text"), Col("phone", "text"), Col("pan", "text"))
    assert classify(TableFacts("x", "leads", cols, 10, None), TODAY).use == "excluded"


def test_looks_like_rules(all_tables):
    from othor.connectors.classify import looks_like

    keys = set(all_tables)

    def label(k):
        t = all_tables[k]
        return looks_like(t, classify(t, TODAY), keys)

    assert label("sales_dw.fact_invoice") == "Invoice · amount, customer, region, date"
    assert label("public.employee") == "Names, phones, emails, PAN, bank account, addresses"
    assert label("sales_dw.fact_invoice_bak_2023") == "Backup of sales_dw.fact_invoice"
    assert label("public.stg_orders") == "Copy of sales_dw.fact_order"
    assert label("sales_dw.tmp_load") == "No rows"
    assert label("sales_dw.fact_invoice_2019") == "Old data · up to Mar 2019"


def test_mostly_personal_table_is_excluded():
    """scan_stress: id, name, phone. The name beside a bare phone is a person's."""
    cols = (Col("id", "int"), Col("name", "text"), Col("phone", "text"))
    got = classify(TableFacts("scan_stress", "t0042_vendor_personal_0042", cols, 120, None), TODAY)
    assert got.use == "excluded"
    assert {c for c, _ in got.personal} == {"name", "phone"}
