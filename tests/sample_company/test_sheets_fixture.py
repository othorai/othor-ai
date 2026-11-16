# SPDX-License-Identifier: Apache-2.0
"""The Sheets API stand-in has every quirk the tab check (10c) must handle."""

import datetime as dt
import re

from tests.fixtures.sample_company.generate import SampleCompany


def _tab(company: SampleCompany, book: str, tab: str) -> dict:  # type: ignore[type-arg]
    sheet = next(s for s in company.sheets.values() if s["properties"]["title"] == book)
    return next(t for t in sheet["sheets"] if t["properties"]["title"] == tab)


def test_spreadsheets_and_tabs(company: SampleCompany) -> None:
    titles = {
        s["properties"]["title"]: [t["properties"]["title"] for t in s["sheets"]]
        for s in company.sheets.values()
    }
    assert titles["MIS-Weekly"] == ["Sales", "Receivables", "Notes", "Old-2024"]
    assert titles["Order tracker FY27"] == [
        "Open orders",
        "Deals",
        "Activities",
        "Products",
        "Contacts",
        "Summary",
    ]


def test_open_orders_shape(company: SampleCompany, expected: dict) -> None:  # type: ignore[type-arg]
    v = _tab(company, "Order tracker FY27", "Open orders")["values"]
    assert v[0][0] == "Order tracker FY27" and v[1][0].startswith("Updated by Priya")
    assert v[2] == [
        "Order date",
        "Customer",
        "Region",
        "Metres",
        "Value (₹ L)",
        "",
        "Stage",
        "Expected invoice",
    ]
    data = v[3:88]
    assert len(data) == 85
    assert v[88][0] == "TOTAL"
    assert len(v) == 89
    assert all(row[5] == "" for row in data)  # blank column F
    assert data[1][1] == "" and data[37][1] == ""  # rows 5 and 41: merged, empty in values
    merges = _tab(company, "Order tracker FY27", "Open orders")["merges"]
    assert {(m["startRowIndex"], m["endRowIndex"], m["startColumnIndex"]) for m in merges} == {
        (3, 5, 1),
        (39, 41, 1),
    }
    fmts = {
        (
            "dmy"
            if re.fullmatch(r"\d\d-\d\d-\d{4}", r[0])
            else "iso"
            if re.fullmatch(r"\d{4}-\d\d-\d\d", r[0])
            else "other"
        )
        for r in data
    }
    assert fmts == {"dmy", "iso"}
    metres = sum(int(r[3].replace(",", "")) for r in data)
    lakh = sum(float(r[4]) for r in data)
    assert metres == expected["board_extras"]["open_orders_sheet_total_m"]
    assert round(lakh, 1) == expected["board_extras"]["open_orders_sheet_total_lakh"]
    assert v[88][3] == "1,284,000" and v[88][4] == "1,512.4"
    assert {r[6] for r in data} == {"Confirmed", "In production", "Dispatched", "On hold"}
    assert data[0][:3] == ["02-10-2026", "Ravira Exports", "South"]


def test_deals_tab(company: SampleCompany) -> None:
    v = _tab(company, "Order tracker FY27", "Deals")["values"]
    header, rows = v[0], v[1:]
    assert header[:5] == ["Deal ID", "Customer", "Created", "Closed", "Outcome"]
    assert len(rows) == 1980
    assert sum(1 for r in rows if r[2] == "") == 312
    assert {r[4] for r in rows} == {"Won", "Lost"}


def test_ignored_tabs(company: SampleCompany) -> None:
    notes = _tab(company, "MIS-Weekly", "Notes")["values"]
    assert all(len(r) == 1 for r in notes)  # free text, no table
    old = _tab(company, "MIS-Weekly", "Old-2024")
    edited = dt.datetime.fromisoformat(old["x_lastEditedTime"]).date()
    assert (dt.date(2026, 11, 1) - edited).days > 365
    summary = _tab(company, "Order tracker FY27", "Summary")
    assert summary["values"][0][0].lower().startswith("summary")


def test_mis_sales_includes_gst_and_auth(company: SampleCompany) -> None:
    v = _tab(company, "MIS-Weekly", "Sales")["values"]
    assert v[0] == ["Month", "Week", "Region", "Sales incl. GST (₹ L)"]
    assert company.sheets_auth["account"] == "uday@indigothread.example"
    assert company.sheets_auth["revoked_at"] is None
    assert company.sheets_auth_revoked["revoked_at"].startswith("2026-10-31")
