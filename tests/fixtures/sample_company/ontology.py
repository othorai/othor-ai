# SPDX-License-Identifier: Apache-2.0
"""The Indigo Thread Ontology as on boards 6a, 6a1 and 6a4: the 16 numbers (and the components they
are built from), breakdowns, rules, names and words, known patterns, goals and facts.

Definitions follow the fixture oracles in `measures.py`, so the compiled SQL can be checked
against them (net revenue = invoices − credit notes − rebates on account 4410; COGS = accounts
4100–4300; freight = 5210; metres with yards converted at Coimbatore).
"""

from __future__ import annotations

from typing import Any

from othor.ontology.service import Change, put

ERP = "erp_db"
DW = "sales_dw"
TRACKER = "Order tracker FY27"
MIS = "MIS-Weekly"
GST_IN_MIS = "0.847457627118644"  # 1 / 1.18: the MIS books sales including GST (P9)
CONFIRMED = {"confirmed_by": "Uday Kamath", "confirmed_at": "2026-10-12"}


def T(source: str, schema: str, table: str) -> dict[str, str]:
    return {"source": source, "schema": schema, "table": table}


def col(name: str, table: str | None = None) -> dict[str, Any]:
    return {"col": name, **({"table": table} if table else {})}


def lit(v: Any) -> dict[str, Any]:
    return {"lit": v}


def op(o: str, *args: Any) -> dict[str, Any]:
    return {"op": o, "args": list(args)}


def m(key: str, missing: str = "not_determinable") -> dict[str, Any]:
    return {"metric": key, "missing": missing}


AS_OF = {"param": "as_of"}


# the area each listed number is grouped under on Summary (M16.1); helpers have none
AREAS = {
    "net_revenue": "Sales",
    "export_share": "Sales",
    "orders": "Sales",
    "average_order_value": "Sales",
    "order_book_value": "Sales",
    "win_rate": "Sales",
    "gross_margin_per_metre": "Finance",
    "cash_balance": "Finance",
    "metres_dispatched": "Operations",
    "freight_per_metre": "Operations",
    "returns_rate": "Operations",
    "dispatch_time": "Operations",
    "receivables_over_60": "Collections",
    "receivables_share_over_60": "Collections",
    "dso": "Collections",
    "collections": "Collections",
    "active_customers": "Customers",
}


def _metric(key: str, **body: Any) -> Change:
    if key in AREAS and body.get("listed", True):
        body = {**body, "area": AREAS[key]}
    return put("metric", key, body, {"from": "sample ontology (boards 6a, 6a1)"})


def metrics(*, order_tracker: bool = False) -> list[Change]:
    inv = T(ERP, "erp_db", "invoices")
    cn = T(ERP, "erp_db", "credit_notes")
    led = T(ERP, "erp_db", "cost_ledger")
    rec = T(ERP, "erp_db", "receivables")
    bank = T(ERP, "erp_db", "bank_ledger")
    lines = T(ERP, "erp_db", "invoice_lines")
    disp = T(DW, "sales_dw", "fact_dispatch")
    orders = T(DW, "sales_dw", "fact_order")
    ret = T(DW, "sales_dw", "fact_returns")
    hidden = {"listed": False, "status": "active"}
    open_amount = {
        "case": [[op("is_null", col("paid_date")), op("-", col("amount"), col("paid_amount"))]],
        "else": col("amount"),
    }
    open_as_of = [
        op("<=", col("invoice_date"), AS_OF),
        op("or", op("is_null", col("paid_date")), op(">", col("paid_date"), AS_OF)),
    ]
    out = [
        # --- net revenue and its components (erp_db) -------------------------------------
        _metric(
            "invoiced_value",
            name="Invoiced value",
            family="net_revenue",
            variant="invoiced",
            table=inv,
            measure={"agg": "sum", "expr": col("net_amount")},
            date="invoice_date",
            breakdowns=["region", "segment", "customer"],
            grain="month",
            evidence=["invoice_no", "invoice_date", "net_amount"],
            **hidden,
        ),
        _metric(
            "credit_note_value",
            name="Credit notes",
            family="net_revenue",
            variant="credit_notes",
            table=cn,
            measure={"agg": "sum", "expr": col("net_amount")},
            date="cn_date",
            breakdowns=["region", "segment", "customer", "plant"],
            grain="month",
            **hidden,
        ),
        _metric(
            "rebates",
            name="Rebates",
            family="net_revenue",
            variant="rebates",
            table=led,
            measure={"agg": "sum", "expr": col("amount")},
            date="entry_date",
            filters=[op("=", col("account_code"), lit(4410))],
            breakdowns=["region", "segment", "customer", "plant"],
            grain="month",
            **hidden,
        ),
        _metric(
            "net_revenue",
            name="Net revenue",
            family="net_revenue",
            grain="month",
            unit="inr",
            description="Invoice value net of credit notes and GST, by invoice date",
            formula=op(
                "-",
                op("-", m("invoiced_value", "zero"), m("credit_note_value", "zero")),
                m("rebates", "zero"),
            ),
            # by plant: invoice lines carry the plant; credit notes and rebates are allocated
            # to plants by rule (decided 4 Nov), so plants add up to the company total
            breakdown_formulas={
                "plant": op(
                    "-",
                    op("-", m("line_revenue", "zero"), m("credit_note_value", "zero")),
                    m("rebates", "zero"),
                )
            },
            breakdowns=["region", "segment", "customer", "plant"],
            headline=True,
            **CONFIRMED,
        ),
        _metric(
            "line_revenue",
            name="Invoiced line value",
            family="net_revenue",
            variant="by_plant",
            table=lines,
            measure={"agg": "sum", "expr": col("net_amount")},
            date="invoices.invoice_date",
            breakdowns=["plant", "product_family"],
            grain="month",
            **hidden,
        ),
        _metric(
            "export_invoiced",
            name="Export invoiced value",
            family="net_revenue",
            variant="export",
            table=inv,
            measure={"agg": "sum", "expr": col("net_amount")},
            date="invoice_date",
            filters=[op("=", col("region", "customers"), lit("Export"))],
            grain="month",
            **hidden,
        ),
        _metric(
            "export_share",
            name="Export share",
            unit="percent",
            grain="month",
            description="Export net revenue ÷ net revenue",
            formula=op("/", m("export_invoiced", "zero"), m("invoiced_value")),
            **CONFIRMED,
        ),
        # --- margin, freight, metres -----------------------------------------------------
        _metric(
            "cogs",
            name="Cost of goods",
            table=led,
            measure={"agg": "sum", "expr": col("amount")},
            date="entry_date",
            filters=[op("in", col("account_code"), lit(4100), lit(4200), lit(4300))],
            breakdowns=["plant"],
            grain="month",
            **hidden,
        ),
        _metric(
            "freight",
            name="Freight expense",
            table=led,
            measure={"agg": "sum", "expr": col("amount")},
            date="entry_date",
            filters=[op("=", col("account_code"), lit(5210))],
            breakdowns=["plant"],
            grain="month",
            **hidden,
        ),
        _metric(
            "metres_dispatched",
            name="Metres dispatched",
            unit="metres",
            grain="day",
            description="Metres from fact_dispatch, yards converted at Coimbatore",
            table=disp,
            measure={"agg": "sum", "expr": col("dispatch_mtrs")},
            date="dispatch_date",
            breakdowns=["plant"],
            evidence=["order_no", "dispatch_date", "plant", "uom"],
            headline=True,  # an output number: a weekly forecast sees a plant shutdown (M7)
            at_stake={"metric": "line_revenue"},  # ₹ through the metres dispatched
            **CONFIRMED,
        ),
        _metric(
            "gross_margin_per_metre",
            name="Gross margin per metre",
            unit="inr_per_metre",
            grain="week",
            description="(Net revenue − cost of goods) ÷ metres dispatched, at every grain; by "
            "plant, credit notes and rebates are allocated to plants (rule)",
            formula=op(
                "/", op("-", m("net_revenue", "zero"), m("cogs", "zero")), m("metres_dispatched")
            ),
            breakdowns=["plant"],
            headline=True,
            **CONFIRMED,
        ),
        _metric(
            "freight_per_metre",
            name="Freight per metre",
            unit="inr_per_metre",
            grain="month",
            description="Freight expense (account 5210) ÷ metres dispatched",
            formula=op("/", m("freight", "zero"), m("metres_dispatched")),
            breakdowns=["plant"],
            **CONFIRMED,
        ),
        _metric(
            "returned_metres",
            name="Returned metres",
            unit="metres",
            table=ret,
            measure={"agg": "sum", "expr": col("qty_mtr")},
            date="return_date",
            breakdowns=["plant"],
            grain="month",
            **hidden,
        ),
        _metric(
            "returns_rate",
            name="Returns rate",
            unit="percent",
            grain="month",
            description="Returned metres ÷ dispatched metres",
            formula=op("/", m("returned_metres", "zero"), m("metres_dispatched")),
            breakdowns=["plant"],
            **CONFIRMED,
        ),
        _metric(
            "dispatch_time",
            name="Dispatch time",
            unit="days",
            grain="day",
            description="Days from order to dispatch, average and spread",
            table=disp,
            measure={
                "agg": "avg",
                "expr": {"fn": "days_between", "args": [col("dispatched_at"), col("ready_at")]},
            },
            date="dispatch_date",
            breakdowns=["plant"],
            at_stake={"metric": "line_revenue"},  # M6: what a slow dispatch puts at risk
            **CONFIRMED,
        ),
        _metric(
            "dispatch_time_spread",
            name="Dispatch time spread",
            unit="days",
            grain="day",
            family="dispatch_time",
            variant="spread",
            table=disp,
            measure={
                "agg": "stddev",
                "expr": {"fn": "days_between", "args": [col("dispatched_at"), col("ready_at")]},
            },
            date="dispatch_date",
            breakdowns=["plant"],
            **hidden,
        ),
        # --- receivables, DSO, cash, collections -----------------------------------------
        _metric(
            "receivables_open",
            name="Open receivables",
            kind="snapshot",
            grain="day",
            table=rec,
            measure={"agg": "sum", "expr": open_amount},
            filters=open_as_of,
            breakdowns=["customer", "region"],
            family="receivables",
            variant="open",
            **hidden,
        ),
        _metric(
            "receivables_over_60",
            name="Receivables over 60 days",
            kind="snapshot",
            grain="day",
            description="Open invoices more than 60 days past due, ₹ and share",
            table=rec,
            measure={"agg": "sum", "expr": open_amount},
            filters=[
                *open_as_of,
                op(">", {"fn": "days_between", "args": [AS_OF, col("due_date")]}, lit(60)),
            ],
            breakdowns=["customer", "region"],
            family="receivables",
            headline=True,
            **CONFIRMED,
        ),
        _metric(
            "receivables_share_over_60",
            name="Share over 60 days",
            unit="percent",
            grain="day",
            family="receivables",
            variant="share",
            formula=op("/", m("receivables_over_60", "zero"), m("receivables_open")),
            **hidden,
        ),
        _metric(
            "dso",
            name="DSO",
            unit="days",
            grain="month",
            description="Receivables ÷ net revenue × days in period",
            formula=op(
                "*", op("/", m("receivables_open"), m("net_revenue")), {"fn": "days_in_period"}
            ),
            headline=True,
            **CONFIRMED,
        ),
        _metric(
            "cash_balance",
            name="Cash balance",
            kind="snapshot",
            grain="day",
            table=bank,
            description="Closing balance across bank ledgers",
            measure={"agg": "last", "expr": col("balance")},
            last_by={"partition": ["bank"], "order": ["entry_date", "entry_id"]},
            filters=[op("<=", col("entry_date"), AS_OF)],
            breakdowns=["bank"],
            **CONFIRMED,
        ),
        _metric(
            "collections",
            name="Collections",
            grain="week",
            description="Receipts against invoices",
            table=rec,
            measure={"agg": "sum", "expr": col("paid_amount")},
            date="paid_date",
            filters=[op("not_null", col("paid_date"))],
            breakdowns=["customer"],
            **CONFIRMED,
        ),
        _metric(
            "active_customers",
            name="Active customers",
            unit="count",
            kind="snapshot",
            grain="month",
            description="Customers with an invoice in the last 90 days",
            table=inv,
            measure={"agg": "count_distinct", "expr": col("customer_id")},
            filters=[
                op("<=", col("invoice_date"), AS_OF),
                op(">", col("invoice_date"), {"fn": "add_days", "args": [AS_OF, lit(-90)]}),
            ],
            breakdowns=["region"],
            **CONFIRMED,
        ),
        # --- orders (sales_dw) -----------------------------------------------------------
        _metric(
            "orders",
            name="Orders",
            unit="count",
            grain="day",
            description="Count and value of orders booked",
            table=orders,
            measure={"agg": "count"},
            date="order_date",
            breakdowns=["region", "customer"],
            family="orders",
            **CONFIRMED,
        ),
        _metric(
            "orders_value",
            name="Order value",
            family="orders",
            variant="value",
            table=orders,
            measure={"agg": "sum", "expr": col("amount")},
            date="order_date",
            breakdowns=["region", "customer", "segment"],
            grain="day",
            **hidden,
        ),
        _metric(
            "orders_segment",
            name="Orders by segment",
            family="orders",
            variant="count_segment",
            table=orders,
            measure={"agg": "count"},
            date="order_date",
            breakdowns=["segment"],
            grain="day",
            **hidden,
        ),
        _metric(
            "average_order_value",
            name="Average order value",
            grain="month",
            description="Order value ÷ orders",
            formula=op("/", m("orders_value"), m("orders")),
            breakdowns=["region"],
            distribution={"metric": "orders_value", "key": "order_no"},  # M6: P11
            **CONFIRMED,
        ),
        _metric(
            "order_book",
            name="Order book",
            kind="snapshot",
            grain="day",
            description="Orders placed and not yet invoiced, as of each day (sales_dw)",
            table=orders,
            measure={"agg": "sum", "expr": col("amount")},
            filters=[
                op("<=", col("order_date"), AS_OF),
                op(
                    "or",
                    op("is_null", col("invoice_date", table="fact_invoice")),
                    op(">", col("invoice_date", table="fact_invoice"), AS_OF),
                ),
            ],
            breakdowns=["customer"],
            family="orders",
            variant="book",
            **hidden,  # Sweep reads it (P3); board 6a1's Order book value waits for the tracker
        ),
    ]
    if order_tracker:
        oo = T(TRACKER, TRACKER, "Open orders")
        deals = T(TRACKER, TRACKER, "Deals")
        out += [
            _metric(
                "order_book_value",
                name="Order book value",
                kind="snapshot",
                grain="day",
                description="Open orders not yet invoiced, by confirmed stage",
                table=oo,
                measure={"agg": "sum", "expr": col("value")},
                breakdowns=["region", "stage"],
                **CONFIRMED,
            ),
            _metric(
                "deals_won",
                name="Deals won",
                unit="count",
                table=deals,
                measure={"agg": "count"},
                date="closed",
                filters=[op("=", col("outcome"), lit("Won"))],
                grain="month",
                **hidden,
            ),
            _metric(
                "deals_closed",
                name="Deals closed",
                unit="count",
                table=deals,
                measure={"agg": "count"},
                date="closed",
                filters=[op("not_null", col("closed"))],
                grain="month",
                **hidden,
            ),
            _metric(
                "win_rate",
                name="Win rate",
                unit="percent",
                grain="month",
                description="Closed won ÷ closed deals",
                formula=op("/", m("deals_won", "zero"), m("deals_closed")),
                **CONFIRMED,
            ),
        ]
    else:
        out += [
            _metric(
                "order_book_value",
                name="Order book value",
                status="needs_source",
                grain="day",
                kind="snapshot",
                description="Open orders not yet invoiced, by confirmed stage",
                needs="Order tracker FY27 · Open orders (Google Sheets)",
                confidence="check",
            ),
            _metric(
                "win_rate",
                name="Win rate",
                status="needs_source",
                grain="month",
                description="Closed won ÷ closed deals",
                needs="Order tracker FY27 · Deals",
                confidence="check",
            ),
        ]
    return out


def rules() -> list[Change]:
    def r(key: str, **body: Any) -> Change:
        return put("rule", key, body, {"from": "sample ontology"})

    inv = T(ERP, "erp_db", "invoices")
    return [
        r(
            "revenue_book_of_record",
            kind="book_of_record",
            concept="revenue",
            source=ERP,
            over=["MIS-Weekly"],
            text="Revenue means net of GST; erp_db wins over the MIS",
        ),
        r(
            "cancelled_invoices",
            kind="filter",
            table=inv,
            expr=op("<>", col("status"), lit("X")),
            text="Invoices with status X are left out",
        ),
        r(
            "yards_to_metres",
            kind="unit_conversion",
            table=T(DW, "sales_dw", "fact_dispatch"),
            column="dispatch_mtrs",
            when=op("=", col("uom"), lit("YD")),
            factor="0.9144",
            to_unit="metres",
            text="Coimbatore dispatch is in yards; converted to metres",
        ),
        r(
            "eur_at_invoice_rate",
            kind="currency",
            table=inv,
            column="net_amount",
            rate_column="fx_rate",
            text="EUR invoices at the invoice-date rate (net_amount is in ₹)",
        ),
        r(
            "late_credits",
            kind="reread",
            table=T(ERP, "erp_db", "credit_notes"),
            days=10,
            text="Months are re-read for 10 days after month end",
        ),
        r(
            "join_invoices_customers",
            kind="join",
            table=inv,
            to=T(ERP, "erp_db", "customers"),
            on=[("customer_id", "customer_id")],
        ),
        r(
            "join_credit_notes_customers",
            kind="join",
            table=T(ERP, "erp_db", "credit_notes"),
            to=T(ERP, "erp_db", "customers"),
            on=[("customer_id", "customer_id")],
        ),
        r(
            "join_ledger_customers",
            kind="join",
            table=T(ERP, "erp_db", "cost_ledger"),
            to=T(ERP, "erp_db", "customers"),
            on=[("customer_id", "customer_id")],
        ),
        r(
            "join_receivables_customers",
            kind="join",
            table=T(ERP, "erp_db", "receivables"),
            to=T(ERP, "erp_db", "customers"),
            on=[("customer_id", "customer_id")],
        ),
        r(
            "join_lines_invoices",
            kind="join",
            table=T(ERP, "erp_db", "invoice_lines"),
            to=inv,
            on=[("invoice_id", "invoice_id")],
        ),
        r(
            "join_lines_items",
            kind="join",
            table=T(ERP, "erp_db", "invoice_lines"),
            to=T(ERP, "erp_db", "items"),
            on=[("item_code", "item_code")],
        ),
        r(
            "join_orders_customers",
            kind="join",
            table=T(DW, "sales_dw", "fact_order"),
            to=T(DW, "sales_dw", "dim_customer"),
            on=[("customer_key", "customer_key")],
        ),
        r(
            "plant_from_item_code_lines",
            kind="join",
            table=T(ERP, "erp_db", "invoice_lines"),
            column="item_code",
            vocabulary="plant_codes",
            text="Plant from the item code prefix",
        ),
        r(
            "plant_from_item_code_returns",
            kind="join",
            table=T(DW, "sales_dw", "fact_returns"),
            column="item_code",
            vocabulary="plant_codes",
            text="Plant from the item code prefix",
        ),
    ]


def breakdowns() -> list[Change]:
    def b(key: str, **body: Any) -> Change:
        return put("breakdown", key, body)

    return [
        b("region", name="Region", column="region", table="customers"),
        b(
            "segment",
            name="Segment",
            column="segment",
            table="customers",
            alternatives=[{"table": "dim_customer", "column": "segment"}],
        ),
        b(
            "customer",
            name="Customer",
            column="name",
            table="customers",
            alternatives=[{"table": "dim_customer", "column": "customer_name"}],
            entity={  # M6: Missing records, Concentration
                "table": T(ERP, "erp_db", "customers"),
                "metric": "invoiced_value",
                "status_column": "status",
                "active_values": ["active"],
                "identity_column": "gstin",
                "created_column": "created_at",
            },
        ),
        b("plant", name="Plant", column="plant", vocabulary="plant_codes"),
        b("product_family", name="Product family", column="family", table="items"),
        b("stage", name="Stage", column="stage"),
        b("bank", name="Bank", column="bank"),
    ]


def names_and_words() -> list[Change]:
    return [
        put(
            "vocabulary",
            "plant_codes",
            {
                "term": "Plants",
                "kind": "prefix_map",
                "column": "item_code",
                "entries": {"SU-": "Surat", "CB-": "Coimbatore"},
                "text": "Surat (item codes SU-), Coimbatore (CB-, dispatch in yards)",
            },
            {"from": "PostgreSQL scan"},
        ),
        put(
            "vocabulary",
            "regions",
            {
                "term": "Regions",
                "kind": "values",
                "values": ["West", "South", "North", "East", "Export"],
            },
        ),
        put(
            "vocabulary",
            "revenue",
            {
                "term": "Revenue",
                "text": "Always means net of GST. Where the MIS and erp_db disagree, erp_db wins.",
            },
            {"answer": "2026-10-12"},
        ),
        put(
            "vocabulary",
            "customer",
            {"term": "Customer", "text": "The billing entity in erp_db, not the buyer contact."},
        ),
        *[
            put("customer", k, {"name": n})
            for k, n in (
                ("ravira_exports", "Ravira Exports"),
                ("tessora_eu", "Tessora EU"),
                ("skarfab", "Skarfab"),
            )
        ],
    ]


def known_patterns() -> list[Change]:
    return [
        put(
            "known_pattern",
            "diwali_week",
            {
                "name": "Diwali week: orders spike the week before and drop the week of",
                "short": "Diwali week",
                "window": {"kind": "week_of", "dates": ["2024-11-01", "2025-10-20", "2026-10-19"]},
            },
        ),
        put(
            "known_pattern",
            "surat_shutdown",
            {
                "name": "Surat plant shutdown, 10–24 August",
                "window": {"kind": "annual", "start": "08-10", "end": "08-24"},
                "settle_days": 7,  # the catch-up week after it (M7, decided 5 Nov 2026)
                "short": "the Surat shutdown",
                "plant": "Surat",
                "confirmed": False,
            },
        ),
        put(
            "known_pattern",
            "quarter_end_push",
            {
                "name": "Quarter-end invoicing push, last 5 days of each quarter",
                "window": {"kind": "quarter_end_days", "days": 5},
            },
        ),
        put(
            "known_pattern",
            "skarfab_pause",
            {
                "name": "Skarfab paused orders until 1 Dec (EU audit)",
                "window": {"kind": "range", "start": "2026-10-09", "end": "2026-12-01"},
                "entity": "Skarfab",
                "answered_on": "2026-10-30",
            },
            {"answer": "2026-10-30"},
        ),
    ]


SURAT_ANSWERED = "2026-10-28"


def surat_answer() -> list[Change]:
    """The owner's Home answer "same dates every year" on 28 Oct (after setup, before 1 Nov):
    the Surat shutdown becomes a confirmed known pattern, as a new Ontology version."""
    (p,) = [c for c in known_patterns() if c.key == "surat_shutdown"]
    body = {**p.body, "confirmed": True, "answered_on": SURAT_ANSWERED}
    return [put("known_pattern", "surat_shutdown", body, {"answer": SURAT_ANSWERED})]


def business() -> list[Change]:
    return [
        put(
            "business_fact",
            "description",
            {
                "text": "Textile manufacturer: cotton and blended fabrics "
                "for EU and US garment makers, with a smaller domestic trade. Two plants, Surat and "
                "Coimbatore."
            },
        ),
        put(
            "goal",
            "fy27_net_revenue",
            {
                "name": "₹60 Cr net revenue in FY27, by 31 Mar",
                "metric": "net_revenue",
                "target": "600000000",
                "by": "2027-03-31",
                "aggregate": "fiscal_year_total",
            },
            {"upload": "Board-review-Q2-FY27.pdf", "locator": "slide 4"},
        ),
        put(
            "goal",
            "gm_per_metre",
            {
                "name": "Gross margin per metre above ₹42",
                "metric": "gross_margin_per_metre",
                "target": "42",
                "by": "2027-03-31",
            },
            {"upload": "Board-review-Q2-FY27.pdf", "locator": "slide 4"},
        ),
        put(
            "goal",
            "dso_under_45",
            {
                "name": "DSO under 45 days by 31 Mar",
                "metric": "dso",
                "target": "45",
                "direction": "at_most",
                "by": "2027-03-31",
            },
            {"upload": "Board-review-Q2-FY27.pdf", "locator": "slide 4"},
        ),
    ]


def sweep() -> list[Change]:
    """What Sweep needs beyond the metrics (M6): owners, a threshold, a reconciliation, a pair."""
    return [
        put(
            "owner",
            "priya",
            {
                "name": "Priya Menon",
                "role": "Head of Sales",
                "areas": [
                    "orders",
                    "order_book",
                    "customer",
                    "missing_records",
                    "average_order_value",
                    "dispatch_invoice",
                ],
            },
        ),
        put(
            "owner",
            "lena",
            {
                "name": "Leela Tandon",
                "role": "Head of Finance",
                "areas": [
                    "net_revenue",
                    "receivables",
                    "dso",
                    "cash_balance",
                    "collections",
                    "freight",
                    "freight_metres",
                    "dispatch_invoice",
                    "mis_erp",
                ],
            },
        ),
        put(
            "owner",
            "arjun",
            {"name": "Aditya Naik", "role": "Operations", "areas": ["dispatch_time", "Coimbatore"]},
        ),
        put(
            "rule",
            "approval_limit",
            {
                "kind": "threshold",
                "text": "Discounts above ₹5 lakh need a second approval",
                "table": T(ERP, "erp_db", "approvals"),
                "column": "discount_amount",
                "limit": "500000",
                "band": "50000",
                "label": "discount approval limit",
            },
        ),
        put(
            "rule",
            "join_orders_dispatch",
            {
                "kind": "join",
                "text": "An order's dispatches, by order number",
                "table": T(DW, "sales_dw", "fact_order"),
                "to": T(DW, "sales_dw", "fact_dispatch"),
                "on": [["order_no", "order_no"]],
            },
        ),
        put(
            "rule",
            "dispatch_invoice",
            {
                "kind": "reconcile",
                "text": "Every dispatched order is invoiced in erp_db within 30 days",
                "table": T(DW, "sales_dw", "fact_order"),
                "to": T(ERP, "erp_db", "invoices"),
                "on": [["order_no", "order_ref"]],
                "date": "fact_dispatch.dispatch_date",
                "column": "amount",
                "when": op("=", col("status"), lit("Dispatched")),
                "days": 30,
                "label": "orders",
            },
        ),
        put(
            "rule",
            "alloc_credit_notes",
            {
                "kind": "allocation",
                "text": "Credit notes go to plants in proportion to the lines of the invoice they credit",
                "metric": "credit_note_value",
                "breakdown": "plant",
                "to": T(ERP, "erp_db", "invoice_lines"),
                "on": [["invoice_id", "invoice_id"]],
                "weight": "net_amount",
            },
        ),
        put(
            "rule",
            "alloc_rebates",
            {
                "kind": "allocation",
                "text": "Rebates go to plants by the customer's revenue by plant in the same month",
                "metric": "rebates",
                "breakdown": "plant",
                "to": T(ERP, "erp_db", "invoice_lines"),
                "on": [["customer_id", "invoices.customer_id"]],
                "weight": "net_amount",
                "grain": "month",
                "date": "invoices.invoice_date",
            },
        ),
        put(
            "rule",
            "join_orders_invoices",
            {
                "kind": "join",
                "text": "An order's invoice in sales_dw, by invoice number",
                "table": T(DW, "sales_dw", "fact_order"),
                "to": T(DW, "sales_dw", "fact_invoice"),
                "on": [["invoice_no", "invoice_no"]],
            },
        ),
        put(
            "rule",
            "freight_metres",
            {
                "kind": "pair",
                "text": "Freight moves with metres dispatched",
                "over": ["freight", "metres_dispatched"],
                "relation": "together",
            },
        ),
    ]


def mis() -> list[Change]:
    """MIS-Weekly as the third source (5a): its Sales tab as a revenue variant, net of GST by
    rule (P9), reconciled with erp_db week by week (P16)."""
    tab = T(MIS, MIS, "Sales")
    return [
        _metric(
            "mis_net_revenue",
            name="Net revenue (MIS)",
            family="net_revenue",
            variant="mis",
            table=tab,
            measure={"agg": "sum", "expr": col("sales_incl_gst")},
            date="week",
            grain="week",
            listed=False,
            status="active",
        ),
        put(
            "rule",
            "mis_gst",
            {
                "kind": "unit_conversion",
                "text": "The MIS books sales including GST: ÷ 1.18",
                "table": tab,
                "column": "sales_incl_gst",
                "when": op("not_null", col("sales_incl_gst")),
                "factor": GST_IN_MIS,
            },
        ),
        put(
            "rule",
            "mis_erp",
            {
                "kind": "reconcile",
                "text": "The MIS and erp_db agree week by week",
                "over": ["net_revenue", "mis_net_revenue"],
                "grain": "week",
            },
        ),
    ]


def sample_ontology(*, order_tracker: bool = False, with_mis: bool = False) -> list[Change]:
    """Every change of the sample Ontology, for one `apply`."""
    return [
        *business(),
        *names_and_words(),
        *breakdowns(),
        *rules(),
        *known_patterns(),
        *metrics(order_tracker=order_tracker),
        *sweep(),
        *(mis() if with_mis else []),
    ]


# Board 6a1 lists these 16 numbers.
BOARD_NUMBERS = [
    "net_revenue",
    "gross_margin_per_metre",
    "receivables_over_60",
    "dso",
    "cash_balance",
    "order_book_value",
    "orders",
    "returns_rate",
    "collections",
    "metres_dispatched",
    "dispatch_time",
    "freight_per_metre",
    "export_share",
    "active_customers",
    "average_order_value",
    "win_rate",
]
