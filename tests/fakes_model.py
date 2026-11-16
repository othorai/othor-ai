# SPDX-License-Identifier: Apache-2.0
"""A fake Claude for setup tests: returns a fixed draft_metrics output, records what it was sent."""

from __future__ import annotations

import json
from typing import Any

from othor.ai.gateway import ProviderReply


def metric(key: str, **kw: Any) -> dict[str, Any]:
    base = {
        "key": key,
        "name": key.replace("_", " ").capitalize(),
        "description": "",
        "source": "",
        "schema": "",
        "table": "",
        "column": "",
        "agg": "sum",
        "date_column": "",
        "grain": "month",
        "unit": "inr",
        "kind": "flow",
        "breakdowns": [],
        "formula_op": "",
        "formula_a": "",
        "formula_b": "",
        "times_days_in_period": False,
        "filter_column": "",
        "filter_op": "",
        "filter_value": "",
        "refs": [],
        "confidence": "high",
        "question": "",
        "area": "Sales",
    }
    return {**base, **kw}


DRAFT = {
    "metrics": [
        metric(
            "net_revenue",
            name="Net revenue",
            description="Invoice value net of credit notes and GST, by invoice date.",
            source="erp_db",
            schema="erp_db",
            table="invoices",
            column="net_amount",
            date_column="invoice_date",
            breakdowns=["region", "currency"],
            filter_column="currency",
            filter_op="=",
            filter_value="INR",
            refs=[{"file": "MIS-Weekly-27Oct.xlsx", "locator": "tab Sales!D1"}],
        ),
        metric(
            "receivables_over_60",
            name="Receivables over 60 days",
            description="Open invoices more than 60 days past due.",
            source="erp_db",
            schema="erp_db",
            table="receivables",
            column="amount",
            date_column="invoice_date",
            grain="day",
            kind="flow",
        ),
        metric(
            "metres_dispatched",
            name="Metres dispatched",
            description="Metres shipped, target 4,00,000 a month.",  # 4,00,000 isn't an input
            source="sales_dw",
            schema="sales_dw",
            table="fact_dispatch",
            column="dispatch_mtrs",
            date_column="dispatch_date",
            unit="metres",
        ),
        metric(
            "cash_ratio",
            name="Cash ratio",
            formula_op="divide",
            formula_a="net_revenue",
            formula_b="metres_dispatched",
            unit="ratio",
        ),
        metric(
            "headcount",
            name="Headcount by plant",
            source="sales_dw",
            schema="public",
            table="employee",
            column="name",
            agg="count_distinct",
        ),
        metric(
            "ghost",
            name="Ghost sales",
            source="erp_db",
            schema="erp_db",
            table="ghost_table",
            column="amount",
            date_column="d",
        ),
        metric(
            "bad_column",
            name="Bad column",
            source="erp_db",
            schema="erp_db",
            table="invoices",
            column="no_such_column",
            date_column="invoice_date",
            confidence="check",
            question="Is this the right column?",
        ),
    ],
    "missing": [{"name": "Customer satisfaction score", "why": "No survey data"}],
}


class FakeClaude:
    name = "anthropic"
    configured = True

    def __init__(self, out: dict[str, Any] | None = None, fail: Exception | None = None) -> None:
        self.out = out or DRAFT
        self.fail = fail
        self.sent: list[Any] = []

    def call(
        self, system: str, messages: list[Any], schema: dict[str, Any] | None, max_tokens: int = 0
    ):
        self.sent.append(messages)
        if self.fail is not None:
            raise self.fail
        return ProviderReply(json.dumps(self.out), "claude-opus-5-5", 100, 100)
