# SPDX-License-Identifier: Apache-2.0
"""Tab shape detection and extraction on the Order tracker and MIS-Weekly stand-ins (docs/05, 10c).

Acceptance (docs/10 M2): "Open orders" is read with header row 3, the total row and the merged
cells handled.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from othor.connectors.sheets.detect import detect, needs_check, tab_use
from othor.connectors.sheets.extract import extract, header_fingerprint

TODAY = dt.date(2026, 11, 4)
EXPECTED = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures/sample_company/expected.json").read_text()
)


@pytest.fixture(scope="module")
def books(company):
    return {d["properties"]["title"]: d for d in company.sheets.values()}


def tab(books, book, name):
    return next(s for s in books[book]["sheets"] if s["properties"]["title"] == name)


@pytest.fixture(scope="module")
def open_orders(books):
    return tab(books, "Order tracker FY27", "Open orders")


def test_open_orders_shape(open_orders):
    s = detect(open_orders, today=TODAY)
    assert s.kind == "table"
    assert s.header_row == 3
    assert s.title_rows == [1, 2]
    assert s.range_a1 == "A4:H88"
    assert s.row_count == 85
    assert s.total_rows == [89]
    assert s.blank_cols == ["F"]
    assert sorted(s.filled_down) == [("B", 5), ("B", 41)]
    cols = {c.header: c for c in s.columns}
    assert cols["Order date"].kind == "date"
    assert set(cols["Order date"].date_formats) == {"dd-mm-yyyy", "yyyy-mm-dd"}
    assert cols["Value (₹ L)"].kind == "number"
    assert cols["Value (₹ L)"].unit == "lakh"
    assert cols["Metres"].kind == "number"
    assert cols["Stage"].distinct == 4
    assert needs_check(s)
    assert tab_use(s).use == "metric_source"


def test_open_orders_extract_handles_total_and_merges(open_orders):
    s = detect(open_orders, today=TODAY)
    df = extract(open_orders, s)
    assert len(df) == 85
    assert list(df.columns) == [
        "order_date",
        "customer",
        "region",
        "metres",
        "value",
        "stage",
        "expected_invoice",
    ]
    extras = EXPECTED["board_extras"]
    assert sum(df["metres"]) == Decimal(extras["open_orders_sheet_total_m"])
    # Value is stored in rupees; the sheet says lakh.
    assert sum(df["value"]) / Decimal(100_000) == Decimal(
        str(extras["open_orders_sheet_total_lakh"])
    )
    # merged Customer cells are filled down (rows 5 and 41 → index 1 and 37)
    assert df["customer"].iloc[1] == df["customer"].iloc[0] != ""
    assert df["customer"].iloc[37] == df["customer"].iloc[36] != ""
    assert all(isinstance(d, dt.date) for d in df["order_date"])
    assert not df["customer"].isna().any()


def test_header_row_override(open_orders):
    s = detect(open_orders, today=TODAY, header_row=3, last_row=60)
    assert s.range_a1 == "A4:H60"
    assert s.row_count == 57
    assert detect(open_orders, today=TODAY, header_row=2).header_row == 2


def test_fingerprint_is_stable_and_changes_on_rename(open_orders):
    s = detect(open_orders, today=TODAY)
    fp = header_fingerprint(s)
    assert fp == header_fingerprint(detect(copy.deepcopy(open_orders), today=TODAY))
    renamed = copy.deepcopy(open_orders)
    renamed["values"][2][3] = "Meters"
    assert header_fingerprint(detect(renamed, today=TODAY)) != fp
    moved = copy.deepcopy(open_orders)
    for row in moved["values"]:
        row[3], row[4] = row[4], row[3]
    assert header_fingerprint(detect(moved, today=TODAY)) != fp


@pytest.mark.parametrize(
    ("book", "name", "kind", "use", "reason"),
    [
        ("Order tracker FY27", "Deals", "table", "metric_source", None),
        ("Order tracker FY27", "Activities", "table", "metric_source", None),
        ("Order tracker FY27", "Products", "table", "dimension", None),
        ("Order tracker FY27", "Contacts", "table", "excluded", None),
        ("Order tracker FY27", "Summary", "pivot", "ignored", "copy"),
        ("MIS-Weekly", "Sales", "table", "metric_source", None),
        ("MIS-Weekly", "Receivables", "table", "metric_source", None),
        ("MIS-Weekly", "Notes", "free_text", "ignored", "no_table"),
        ("MIS-Weekly", "Old-2024", "stale", "ignored", "old"),
    ],
)
def test_other_tabs(books, book, name, kind, use, reason):
    s = detect(tab(books, book, name), today=TODAY)
    assert s.kind == kind
    got = tab_use(s)
    assert (got.use, got.reason) == (use, reason)


def test_simple_tabs_need_no_check(books):
    deals = detect(tab(books, "Order tracker FY27", "Deals"), today=TODAY)
    assert deals.header_row == 1 and deals.row_count == 1980
    assert not needs_check(deals)


def test_contacts_personal_columns_found_by_name(books):
    s = detect(tab(books, "Order tracker FY27", "Contacts"), today=TODAY)
    assert {c for c, _ in tab_use(s).personal} == {"Name", "Phone", "Email"}


def test_units_in_headers(books):
    rec = detect(tab(books, "MIS-Weekly", "Receivables"), today=TODAY)
    assert {c.unit for c in rec.columns if c.header != "Week"} == {"lakh"}
    prod = detect(tab(books, "Order tracker FY27", "Products"), today=TODAY)
    assert {c.header: c.unit for c in prod.columns}["Rate (₹/m)"] == "inr_per_m"


def test_personal_columns_are_never_extracted(books):
    """A tab used without its personal columns: they don't reach the analytics store."""
    sheet = tab(books, "Order tracker FY27", "Contacts")
    s = detect(sheet, today=TODAY)
    personal = {c for c, _ in tab_use(s).personal}
    df = extract(sheet, s, skip=personal, today=TODAY)
    assert not {"name", "phone", "email"} & set(df.columns)
    assert len(df.columns) == len([c for c in s.columns if c.kind != "blank"]) - len(
        personal
    ) - len([c for c in s.columns if c.letter in s.notes_cols and c.kind != "blank"])


def test_a_week_without_its_year_takes_the_year_of_its_month(books):
    """MIS-Weekly writes "Mon 19 Oct" beside "Oct 2026": each week keeps its own year, so years
    of weeks don't pile into one (the e2e nights found this, M6a)."""
    sheet = tab(books, "MIS-Weekly", "Sales")
    s = detect(sheet, today=TODAY)
    df = extract(sheet, s, today=TODAY)
    weeks = df["week"].dropna()
    months = df["month"].dropna()
    assert {d.year for d in weeks} == {d.year for d in months} and len({d.year for d in weeks}) > 2
    assert all(w.year == m.year for w, m in zip(df["week"], df["month"], strict=True) if w and m)
    # one row per week part and region: no week repeats within a region
    assert not df.duplicated(["week", "region"]).any()


def test_weeks_without_a_year_take_it_from_the_tab_order():
    """No year on the row or in the title (decided 5 Nov 2026): dates run down the tab, Dec → Jan
    rolls the year, the last date is the latest not after today; the tab check asks to confirm."""
    from othor.connectors.sheets.detect import needs_check

    weeks = ["Mon 19 Oct", "Mon 26 Oct", "Mon 2 Nov", "Mon 28 Dec", "Mon 4 Jan", "Mon 11 Jan"]
    sheet = {
        "properties": {"title": "Weekly"},
        "values": [["Week", "Region", "Sales (₹ L)"]]
        + [[w, "South", f"{10 + i}.00"] for i, w in enumerate(weeks)],
    }
    today = dt.date(2027, 1, 20)
    s = detect(sheet, today=today)
    assert s.yearless == ("Weeks", "Oct 2026", "Jan 2027") and needs_check(s)
    df = extract(sheet, s, today=today)
    assert [d.isoformat() for d in df["week"]] == [
        "2026-10-19",
        "2026-10-26",
        "2026-11-02",
        "2026-12-28",
        "2027-01-04",
        "2027-01-11",
    ]
    from types import SimpleNamespace

    from othor.jobs.scan_job import _shape_json
    from othor.web.sources_view import check_view

    tab = SimpleNamespace(shape=_shape_json(s, "metric_source"))
    line = ("Weeks", "Weeks have no year; we read them as Oct 2026 – Jan 2027.", "Please confirm")
    assert line in check_view(tab).read  # type: ignore[arg-type]
