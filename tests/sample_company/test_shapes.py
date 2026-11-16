# SPDX-License-Identifier: Apache-2.0
"""The two sample databases have the tables, columns and quirks docs/11 and the scan boards show."""

import datetime as dt

from tests.fixtures.sample_company.generate import SampleCompany
from tests.fixtures.sample_company.measures import monthly_invoiced


def test_erp_db_tables(company: SampleCompany) -> None:
    core = {
        "invoices",
        "invoice_lines",
        "credit_notes",
        "customers",
        "receivables",
        "bank_ledger",
        "cost_ledger",
        "items",
        "approvals",
        "fx_rates",
    }
    assert core <= set(company.erp)
    assert len(company.erp) == 64  # "Connected · read-only · 64 tables" (10a1)


def test_sales_dw_schemas_and_tables(company: SampleCompany) -> None:
    dw = company.dw
    assert set(dw) == {"public", "sales_dw"}
    assert len(dw["public"]) == 12 and len(dw["sales_dw"]) == 36  # scan board 2k
    for name in (
        "fact_invoice",
        "fact_invoice_line",
        "fact_order",
        "fact_order_line",
        "fact_dispatch",
        "fact_returns",
        "dim_customer",
        "dim_item",
        "dim_invoice_type",
        "fact_invoice_bak_2023",
        "fact_invoice_2019",
        "tmp_load",
        "invoice_contacts",
        "crm_contacts",
    ):
        assert name in dw["sales_dw"], name
    for name in (
        "employee",
        "ar_open_items",
        "dim_region",
        "stg_orders",
        "etl_log",
        "vendor_rating",
        "invoice_print_log",
    ):
        assert name in dw["public"], name
    assert len(dw["sales_dw"]["tmp_load"]) == 0
    assert list(dw["public"]["dim_region"]["region"]) == [
        "South",
        "West",
        "North",
        "East",
        "Export",
    ]


def test_personal_columns_present(company: SampleCompany) -> None:
    assert {"contact_email", "contact_phone"} <= set(company.erp["customers"].columns)
    emp = company.dw["public"]["employee"]
    assert {
        "employee_id",
        "department",
        "plant",
        "grade",
        "join_date",
        "exit_date",
        "name",
        "phone",
        "email",
        "pan",
        "bank_account",
        "address",
    } == set(emp.columns)
    for t in ("invoice_contacts", "crm_contacts"):
        assert {"name", "phone", "email"} <= set(company.dw["sales_dw"][t].columns)
    assert company.personal_columns()  # the generator declares them, for guard tests later


def test_coimbatore_dispatch_in_yards(company: SampleCompany) -> None:
    d = company.dw["sales_dw"]["fact_dispatch"]
    assert set(d.loc[d["plant"] == "Coimbatore", "uom"]) == {"YD"}
    assert set(d.loc[d["plant"] == "Surat", "uom"]) == {"MTR"}
    items = company.erp["items"]
    assert items["item_code"].str.match(r"^(SU|CB)-").all()


def test_eur_share_and_rate(company: SampleCompany) -> None:
    inv = company.erp["invoices"]
    last12 = inv[
        (inv["invoice_date"] >= dt.date(2025, 11, 1)) & (inv["invoice_date"] < dt.date(2026, 11, 1))
    ]
    share = last12.loc[last12["currency"] == "EUR", "net_amount"].sum() / last12["net_amount"].sum()
    assert abs(share - 0.38) <= 0.01
    eur = inv[inv["currency"] == "EUR"]
    fx = company.erp["fx_rates"].set_index("rate_date")["eur_inr"]
    assert (eur["fx_rate"].to_numpy() == fx.loc[eur["invoice_date"]].to_numpy()).all()
    assert ((eur["amount_fc"] * eur["fx_rate"]).round(2) - eur["net_amount"]).abs().max() <= 1


def test_erp_net_of_gst_and_overlap(company: SampleCompany) -> None:
    inv = company.erp["invoices"]
    assert ((inv["gross_amount"] - inv["net_amount"] - inv["gst_amount"]).abs() <= 0.01).all()
    assert inv["invoice_date"].min() == dt.date(2024, 4, 1)
    fi = company.dw["sales_dw"]["fact_invoice"]
    assert fi["invoice_date"].min() == dt.date(2021, 4, 1)
    overlap = fi[fi["invoice_date"] >= dt.date(2024, 4, 1)]
    assert set(overlap["invoice_no"]) == set(inv["invoice_no"])
    dw, erp = monthly_invoiced(company, "dw"), monthly_invoiced(company, "erp")
    assert abs(dw.loc["2025-06"] - erp.loc["2025-06"]) < 0.01


def test_backup_and_old_tables(company: SampleCompany) -> None:
    bak = company.dw["sales_dw"]["fact_invoice_bak_2023"]
    assert bak["invoice_date"].max() <= dt.date(2023, 12, 31)
    old = company.dw["sales_dw"]["fact_invoice_2019"]
    assert old["invoice_date"].max() <= dt.date(2019, 3, 31)
    stg = company.dw["public"]["stg_orders"]
    assert len(stg) > 0 and set(stg.columns) <= set(company.dw["sales_dw"]["fact_order"].columns)
