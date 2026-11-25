# SPDX-License-Identifier: Apache-2.0
"""#116: metrics drafted from an uploaded workbook, waiting for a source, map to the same workbook
connected as Google Sheets: the tab with the same name that has the same column. The cell the
draft names must be one the upload's code-parsed refs hold (a model's text alone never maps)."""

from __future__ import annotations

import uuid
from typing import Any

from othor.ontology.compiler.catalog import Catalog, ColumnInfo, TableInfo
from othor.setup import connect
from othor.setup.models import SetupDraft
from othor.setup.parse import Ref

SRC = "Ledger"  # the connected spreadsheet's source name shares no word with the metrics
BOOK = "TS data"  # the spreadsheet's title (the tabs' schema)
UPLOAD = "payments_workbook.xlsx"


def _table(name: str, cols: dict[str, str]) -> TableInfo:
    return TableInfo(
        "00000000-0000-0000-0000-000000000001",
        SRC,
        "gsheets",
        BOOK,
        name,
        f"k-{name}",
        "metric_source",
        False,
        {c.lower(): ColumnInfo(c, k, False) for c, k in cols.items()},
        duck_table=f"sheet_{name.lower()}",
        row_count=1000,
    )


def _cat() -> Catalog:
    tables = [
        _table(
            "Transactions",
            {
                "transaction_date": "date",
                "merchant_id": "text",
                "transaction_amount": "decimal",
                "status": "text",
            },
        ),
        _table("Daily_Targets", {"target_date": "date", "daily_target": "decimal"}),
    ]
    return Catalog(
        tables={(SRC, BOOK, t.name): t for t in tables},
        sources={SRC: ("00000000-0000-0000-0000-000000000001", "gsheets")},
    )


def _ref(tab: str, cell: str, header: str, number: bool = True) -> tuple[str, Ref]:
    return (
        UPLOAD,
        Ref("column", f"tab {tab}!{cell}", header, {"tab": tab, "number": number, "kind": "x"}),
    )


REFS = [
    _ref("Transactions", "A1", "Transaction Date", number=False),
    _ref("Transactions", "G1", "Transaction Amount"),
    _ref("Transactions", "H1", "Status", number=False),
    _ref("Daily_Targets", "F1", "Daily Target"),
    _ref("Settlements", "C1", "Fee"),  # a tab the connected sheet doesn't have
]


def _row(key: str, name: str, needs: str, refs: list[dict[str, str]] | None = None) -> SetupDraft:
    return SetupDraft(
        kind="metric",
        key=key,
        body={"name": name, "status": "needs_source", "needs": needs[:200]},
        origin={"by": "model", "prompt": "draft_metrics@v4"},
        refs=refs or [],
        confidence="check",
        state="needs_source",
        note=needs,
        frozen=False,
        position=0,
    )


def _draft(rows: list[SetupDraft], refs: list[tuple[str, Ref]] = REFS) -> dict[str, Any]:
    got = connect.draft_for_source(
        _cat(),
        SRC,
        uuid.uuid4(),
        {r.key: r for r in rows},
        None,
        rules=[],
        vocabulary=[],
        names=BOOK,
        refs=refs,
    )
    return dict(got.redrafted)


def test_a_missing_metric_naming_the_uploaded_cell_maps_to_the_same_tab() -> None:
    row = _row(
        "total_transaction_value",
        "Total transaction value",
        "Needs transaction_amount from the Transactions tab (Transactions!G1), "
        "but no scanned table holds it",
    )
    body = _draft([row])["total_transaction_value"]
    assert body["table"] == {"source": SRC, "schema": BOOK, "table": "Transactions"}
    assert body["measure"] == {"agg": "sum", "expr": {"col": "transaction_amount"}}
    assert body["date"] == "transaction_date"
    assert body["status"] == "active" and "needs" not in body
    assert body["name"] == "Total transaction value"


def test_a_drafted_ref_to_the_upload_maps_too() -> None:
    """A model's metric whose table wasn't scanned keeps its refs: the upload cell maps it."""
    row = _row(
        "daily_target",
        "Daily target",
        "Held in Daily_Targets, but that tab is not among the scanned tables",
        refs=[{"upload": UPLOAD, "locator": "tab Daily_Targets!F1", "text": ""}],
    )
    body = _draft([row])["daily_target"]
    assert body["table"]["table"] == "Daily_Targets"
    assert body["measure"]["expr"] == {"col": "daily_target"}
    assert body["date"] == "target_date"


def test_what_does_not_match_stays_waiting() -> None:
    rows = [
        # a rate needs more than one column: it stays for the owner's chat
        _row("success_rate", "Success rate", "Needs status (Transactions!H1)"),
        # a text column is never summed
        _row("merchants", "Merchants paid", "Needs merchant names (Transactions!H1)"),
        # the tab isn't in the connected sheet
        _row("fees", "Fees", "Needs Fee (Settlements!C1)"),
        # a cell the upload doesn't hold: the model's text alone never maps
        _row("refunds", "Refunds", "Needs refund_amount (Transactions!K1)"),
    ]
    assert _draft(rows) == {}


def test_without_upload_refs_nothing_new_maps() -> None:
    row = _row(
        "total_transaction_value",
        "Total transaction value",
        "Needs transaction_amount (Transactions!G1), but no scanned table holds it",
    )
    assert _draft([row], refs=[]) == {}
