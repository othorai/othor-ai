# SPDX-License-Identifier: Apache-2.0
"""Upload parsing by code (XLSX, CSV, SQL, text PDFs) and the model path's number checks."""

from __future__ import annotations

import pytest

from othor.setup import parse
from tests.fixtures.sample_company.uploads import BOARD, DASH, DICTIONARY, MIS, RECV_SQL


def test_kind_detection():
    assert parse.kind_of("MIS-Weekly-27Oct.xlsx") == "xlsx"
    assert parse.kind_of("a.CSV") == "csv"
    assert parse.kind_of("receivables_report.sql") == "sql"
    assert parse.kind_of("Board.pdf") == "pdf"
    assert parse.kind_of("Sales-dashboard-Oct.png") == "image"
    assert parse.kind_of("notes.md") == "text"
    assert parse.kind_of("deck.key") == "other"


def test_mis_workbook(upload_files):
    got = parse.parse_file(upload_files[MIS].read_bytes(), "xlsx", MIS)
    assert got.status == "read" and got.read_by == "code"
    assert got.summary["tabs"] == 4
    cols = {r.text: r for r in got.refs if r.kind == "column"}
    sales = cols["Sales incl. GST (₹ L)"]
    assert sales.locator == "tab Sales!D1" and sales.data["unit"] == "lakh"
    assert sales.data["tab"] == "Sales" and sales.data["number"] is True
    assert "Notes" not in {r.data.get("tab") for r in got.refs if r.kind == "column"}  # free text
    assert got.summary["text"].startswith(f"{got.summary['metrics']} metrics, 4 tabs")


def test_csv():
    raw = b"Month,Net Sales (\xe2\x82\xb9 L),Region\nApr 2026,12.5,South\nMay 2026,13.0,West\n"
    got = parse.parse_file(raw, "csv", "sales.csv")
    assert {r.text for r in got.refs if r.kind == "column"} == {
        "Month",
        "Net Sales (₹ L)",
        "Region",
    }


def test_receivables_sql(upload_files):
    got = parse.parse_file(upload_files[RECV_SQL].read_bytes(), "sql", RECV_SQL)
    assert got.status == "read"
    tables = {r.text for r in got.refs if r.kind == "table"}
    assert tables == {"erp_db.receivables", "erp_db.customers"}
    filt = [r for r in got.refs if r.kind == "filter"]
    assert any(r.locator == "line 12" and "> 60" in r.text for r in filt)
    assert got.summary["text"] == f"{got.summary['definitions']} definitions"


def test_text_pdf_is_read_as_text(upload_files):
    got = parse.parse_file(upload_files[DICTIONARY].read_bytes(), "pdf", DICTIONARY)
    assert (got.status, got.read_by) == ("read_as_text", "text")
    pages = {r.locator: r.text for r in got.refs if r.kind == "page"}
    assert "net_value already excludes GST" in pages["p. 7"]
    assert "dispatch_mtrs" in pages["p. 12"]
    assert got.summary["text"] == "Read as text · drafted by rules"


def test_scanned_pdf_and_image_need_the_model(upload_files):
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()  # no text layer
    got = parse.parse_file(bytes(pdf.output()), "pdf", "scan.pdf")
    assert got.status == "needs_model" and got.refs == []
    img = parse.parse_file(upload_files[DASH].read_bytes(), "image", DASH)
    assert img.status == "needs_model"
    assert img.summary["text"] == "Can't be read without the model"


def test_model_read_keeps_only_numbers_found_in_the_pdf(upload_files):
    raw = upload_files[BOARD].read_bytes()
    text = parse.pdf_text(raw)
    model_out = {
        "items": [
            {"kind": "target", "text": "₹60 Cr net revenue in FY27", "locator": "slide 4"},
            {
                "kind": "target",
                "text": "₹75 Cr net revenue in FY28",
                "locator": "slide 4",
            },  # invented
            {"kind": "metric", "text": "Gross margin per metre", "locator": "slide 4"},
        ]
    }
    refs = parse.refs_from_model(model_out, kind="pdf", source_text=text)
    assert [r.text for r in refs] == ["₹60 Cr net revenue in FY27", "Gross margin per metre"]


def test_model_read_of_an_image_drops_every_number():
    refs = parse.refs_from_model(
        {
            "items": [
                {"kind": "metric", "text": "Order book value ₹15.1 Cr", "locator": "panel 1"},
                {"kind": "dimension", "text": "by region", "locator": "panel 1"},
            ]
        },
        kind="image",
        source_text=None,
    )
    assert [r.text for r in refs] == ["Order book value", "by region"]


@pytest.mark.parametrize("name", ["evil.xlsx", "evil.pdf"])
def test_corrupt_files_are_unreadable_not_errors(name):
    got = parse.parse_file(b"not a real file", parse.kind_of(name), name)
    assert got.status in ("unreadable", "needs_model")
