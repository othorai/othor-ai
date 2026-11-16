# SPDX-License-Identifier: Apache-2.0
"""A Word data dictionary is read by code (python-docx), cited by heading, and its quotes pass
the same check as a PDF's."""

from __future__ import annotations

import io

from docx import Document

from othor.setup import document, parse


def _docx() -> bytes:
    d = Document()
    d.add_heading("Invoices", level=1)
    d.add_paragraph("fact_invoice.net_value: invoice value in ₹; net_value already excludes GST.")
    d.add_paragraph("status X means cancelled; cancelled invoices are left out of every report.")
    d.add_heading("Customers", level=1)
    d.add_paragraph("EXP in dim_customer.region means Export.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_docx_is_read_by_heading():
    assert parse.kind_of("Data dictionary.docx") == "docx"
    got = parse.parse_file(_docx(), "docx", "Data dictionary.docx")
    assert got.status == "read_as_text"
    pages = document.pages_of(got.refs)
    assert [loc for loc, _ in pages] == ["Invoices", "Customers"]
    assert document.quoted("net_value already excludes GST", pages) == "Invoices"
    assert document.quoted("status Z means void", pages) is None
