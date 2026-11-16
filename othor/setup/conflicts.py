# SPDX-License-Identifier: Apache-2.0
"""Conflicts (board 2n/2na): the same concept with two values. v0.1 checks net revenue: the drafted
metric's computed value for last month vs the matching column of an uploaded MIS workbook, totalled
by code for the same month. Above 0.5% apart, the row is a Conflict and Confirm waits for Choose.

Both values are computed (SQL on the source; a sum of the workbook's cells). Choose writes the
rules the owner picked; the chat around it (with the model) never supplies a number.
"""

from __future__ import annotations

import datetime as dt
import io
from decimal import Decimal
from typing import Any

from othor.connectors.sheets.detect import UNIT_FACTOR, header_unit, parse_number
from othor.core import fmt
from othor.setup.parse import Ref

THRESHOLD = Decimal("0.005")
CONCEPT_WORDS = ("sales", "revenue", "turnover")


def _month_label(start: dt.date) -> str:
    return f"{start:%b %Y}"


def upload_month_total(raw: bytes, tab: str, header: str, start: dt.date) -> Decimal | None:
    """Sum of the column `header` on `tab` over the rows of the month starting `start`."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    try:
        if tab not in wb.sheetnames:
            return None
        rows = [list(r) for r in wb[tab].iter_rows(values_only=True)]
    finally:
        wb.close()
    head_i = next((i for i, r in enumerate(rows) if header in [str(c) for c in r if c]), None)
    if head_i is None:
        return None
    col = [str(c) for c in rows[head_i]].index(header)
    label = _month_label(start)
    factor = Decimal(UNIT_FACTOR.get(header_unit(header) or "inr", 1))
    total, n = Decimal(0), 0
    for r in rows[head_i + 1 :]:
        if not any(_in_month(c, start, label) for i, c in enumerate(r) if i != col):
            continue
        v = r[col] if col < len(r) else None
        num = v if isinstance(v, int | float) else parse_number(str(v or ""))
        if num is None:
            continue
        total += Decimal(str(num))
        n += 1
    return (total * factor).quantize(Decimal("0.01")) if n else None


def _in_month(c: Any, start: dt.date, label: str) -> bool:
    if isinstance(c, dt.datetime | dt.date):
        d = c.date() if isinstance(c, dt.datetime) else c
        return (d.year, d.month) == (start.year, start.month)
    return str(c or "").strip() == label


def gst_note(header: str) -> tuple[str, str] | None:
    h = header.lower()
    if "gst" in h and ("incl" in h or "including" in h or "with" in h):
        return "Including GST", "Net of GST"
    return None


def check(
    value: Decimal | None,
    period: dt.date,
    source: str,
    candidates: list[tuple[str, Ref, bytes]],
) -> dict[str, Any] | None:
    """candidates: (upload filename, column ref, workbook bytes) for revenue-like columns."""
    if value is None or not value:
        return None
    for name, ref, raw in candidates:
        words = ref.text.lower()
        if not any(w in words for w in CONCEPT_WORDS) or ref.data.get("stale"):
            continue
        other = upload_month_total(raw, ref.data.get("tab", ""), ref.text, period)
        if other is None:
            continue
        diff = abs(other - value) / value
        if diff <= THRESHOLD:
            continue
        labels = gst_note(ref.text) or (f"As in {name}", f"As in {source}")
        tab = ref.data.get("tab", "")
        if gst_note(ref.text):
            question = f"Your MIS includes GST; {source} excludes it. Which should the brief use?"
        else:
            question = (
                f"{name} and {source} differ by {fmt.pct(diff)} for "
                f"{_month_label(period)}. Which should the brief use?"
            )
        return {
            "period": period.isoformat(),
            "month": _month_label(period),
            "question": question,
            "diff": str(diff),
            "options": [
                {
                    "id": "source",
                    "label": labels[1],
                    "value": str(value),
                    "where": f"{source} · SQL",
                },
                {
                    "id": "upload",
                    "label": labels[0],
                    "value": str(other),
                    "where": f"{name.split('-')[0].split('.')[0]} · tab {tab}",
                },
            ],
            "upload": {"file": name, "locator": ref.locator, "header": ref.text, "tab": tab},
            "source": source,
        }
    return None


def resolution(
    conflict: dict[str, Any], choice: str, body: dict[str, Any], cols: set[str]
) -> tuple[dict[str, Any], list[tuple[str, str, dict[str, Any]]], str]:
    """Choose → (new metric body, records to add at Confirm, the sentence saved to the Ontology).

    choice: source · upload · both. "upload" with a GST column on the table adds GST back so the
    number the brief shows is still computed by SQL from the source."""
    src, file = conflict["source"], conflict["upload"]["file"]
    gst = gst_note(conflict["upload"]["header"]) is not None
    over = [file.rsplit(".", 1)[0]]
    if choice == "upload" and gst and "gst_amount" in cols:
        m = body["measure"]
        new = {
            **body,
            "measure": {
                "agg": "sum",
                "expr": {
                    "op": "+",
                    "args": [
                        m["expr"],
                        {"fn": "coalesce", "args": [{"col": "gst_amount"}, {"lit": 0}]},
                    ],
                },
            },
            "description": body.get("description", "") + " Including GST.",
            "confidence": "high",
        }
        text = f"Revenue includes GST, as in the MIS. Computed from {src} with GST added back."
    else:
        new = {**body, "confidence": "high"}
        if gst:
            text = (
                f"Revenue means net of GST. Where the MIS and {src} disagree, {src} wins. "
                "The MIS figure is GST-inclusive; difference shown as “GST”."
            )
        else:
            text = f"Where {file} and {src} disagree, {src} wins."
        if choice == "both":
            text += " Both figures are shown in the brief."
    records: list[tuple[str, str, dict[str, Any]]] = [
        (
            "rule",
            "revenue_book_of_record",
            {
                "kind": "book_of_record",
                "concept": "revenue",
                "source": src,
                "over": over,
                "text": text,
            },
        ),
        ("vocabulary", "revenue", {"term": "Revenue", "kind": "definition", "text": text}),
    ]
    return new, records, text
