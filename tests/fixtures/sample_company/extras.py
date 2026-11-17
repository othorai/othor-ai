# SPDX-License-Identifier: Apache-2.0
"""Tables around the business data: personal-data tables, backups, logs, reference fillers.

Personal values are invented (names from word lists, `example.in` emails, random digits).
They exist so the scan can find and exclude them (M2); they are never read by Othor AI.
"""

from __future__ import annotations

import datetime as dt
import re

import numpy as np
import pandas as pd

from tests.fixtures.sample_company import company as co

# (source, schema, table, column) — the guard tests assert none of these is ever queried.
PERSONAL: list[tuple[str, str, str, str]] = [
    ("erp_db", "erp_db", "customers", "contact_email"),
    ("erp_db", "erp_db", "customers", "contact_phone"),
    *[
        ("sales_dw", "public", "employee", c)
        for c in ("name", "phone", "email", "pan", "bank_account", "address")
    ],
    *[
        ("sales_dw", "sales_dw", t, c)
        for t in ("invoice_contacts", "crm_contacts")
        for c in ("name", "phone", "email")
    ],
    *[("gsheets", "Order tracker FY27", "Contacts", c) for c in ("Name", "Phone", "Email")],
]

FIRST = [
    "Aarav",
    "Ananya",
    "Vikram",
    "Meera",
    "Rohan",
    "Kavya",
    "Arjun",
    "Divya",
    "Sanjay",
    "Lakshmi",
    "Rahul",
    "Nisha",
    "Karthik",
    "Pooja",
    "Suresh",
    "Deepa",
    "Manoj",
    "Asha",
    "Vivek",
    "Priyanka",
    "Ganesh",
    "Shalini",
    "Ajay",
    "Revathi",
    "Naveen",
    "Sneha",
]
LAST = [
    "Iyer",
    "Sharma",
    "Patel",
    "Nair",
    "Reddy",
    "Menon",
    "Rao",
    "Gupta",
    "Pillai",
    "Desai",
    "Joshi",
    "Kumar",
    "Shah",
    "Das",
    "Bose",
    "Verma",
    "Kulkarni",
    "Merai",
    "Naidu",
    "Singh",
]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())[:16]


def fake_email(company_name: str, r: np.random.Generator) -> str:
    return f"{FIRST[int(r.integers(0, len(FIRST)))].lower()}@{_slug(company_name)}.example.in"


def fake_phone(r: np.random.Generator, currency: str = "INR") -> str:
    if currency == "EUR":
        return "+49 " + "".join(str(int(x)) for x in r.integers(0, 10, 10))
    return "+91 9" + "".join(str(int(x)) for x in r.integers(0, 10, 9))


def _person(r: np.random.Generator) -> str:
    return f"{FIRST[int(r.integers(0, len(FIRST)))]} {LAST[int(r.integers(0, len(LAST)))]}"


def contacts(seed: int, key: str, n: int, roster: list[co.Customer]) -> pd.DataFrame:
    r = co.stream(seed, key)
    names = [_person(r) for _ in range(n)]
    cust = [roster[int(i)].code for i in r.integers(0, len(roster), n)]
    return pd.DataFrame(
        {
            "contact_id": np.arange(1, n + 1),
            "customer_code": cust,
            "name": names,
            "phone": [fake_phone(r) for _ in range(n)],
            "email": [
                f"{nm.split()[0].lower()}.{nm.split()[1].lower()}{i}@example.in"
                for i, nm in enumerate(names)
            ],
            "role": r.choice(["Accounts", "Purchase", "Owner", "Logistics"], n),
        }
    )


def employees(seed: int, n: int) -> pd.DataFrame:
    r = co.stream(seed, "employees")
    join = np.datetime64("2008-01-01") + r.integers(0, 6500, n).astype("timedelta64[D]")
    left = r.random(n) < 0.18
    exit_ = join + r.integers(200, 3000, n).astype("timedelta64[D]")
    exit_ok = left & (exit_ < np.datetime64("2026-10-31"))
    names = [_person(r) for _ in range(n)]
    pans = [
        "".join(chr(65 + int(x)) for x in r.integers(0, 26, 5))
        + "".join(str(int(x)) for x in r.integers(0, 10, 4))
        + chr(65 + int(r.integers(0, 26)))
        for _ in range(n)
    ]
    return pd.DataFrame(
        {
            "employee_id": np.arange(10001, 10001 + n),
            "department": r.choice(
                [
                    "Weaving",
                    "Processing",
                    "Dispatch",
                    "Quality",
                    "Sales",
                    "Accounts",
                    "Maintenance",
                ],
                n,
            ),
            "plant": r.choice(["Surat", "Coimbatore"], n, p=[0.55, 0.45]),
            "grade": r.choice(["W1", "W2", "W3", "S1", "S2", "M1"], n),
            "join_date": [d.astype(object) for d in join],
            "exit_date": [
                d.astype(object) if ok else None for d, ok in zip(exit_, exit_ok, strict=True)
            ],
            "name": names,
            "phone": [fake_phone(r) for _ in range(n)],
            "email": [f"{nm.split()[0].lower()}.{i}@example.in" for i, nm in enumerate(names)],
            "pan": pans,
            "bank_account": ["".join(str(int(x)) for x in r.integers(0, 10, 12)) for _ in range(n)],
            "address": [
                f"{int(r.integers(1, 400))}, {r.choice(['Ring Road', 'Station Road', 'Mill Lane', 'Temple Street'])}"
                for _ in range(n)
            ],
        }
    )


def old_invoices(seed: int, roster: list[co.Customer]) -> pd.DataFrame:
    """sales_dw.fact_invoice_2019: invoices to Mar 2019, superseded (scan: Ignored · Old data)."""
    r = co.stream(seed, "old-invoices")
    days = pd.date_range("2017-04-01", "2019-03-31", freq="D")
    days = days[days.day_of_week != 6]
    n = r.poisson(14, len(days))
    d = np.repeat(days.to_numpy().astype("datetime64[D]"), n)
    cust = [roster[int(i)] for i in r.integers(4, len(roster), len(d))]
    return pd.DataFrame(
        {
            "invoice_no": [f"BI-OLD/{i + 1:06d}" for i in range(len(d))],
            "invoice_date": [x.astype(object) for x in d],
            "customer_code": [c.code for c in cust],
            "region": [c.region for c in cust],
            "net_value": np.round(r.lognormal(np.log(60_000), 0.6, len(d)), 2),
        }
    )


def etl_log(seed: int, cut: np.datetime64) -> pd.DataFrame:
    r = co.stream(seed, "etl-log")
    days = pd.date_range(
        "2021-04-02", pd.Timestamp(cut.astype(object)) - pd.Timedelta(days=1), freq="D"
    )
    tables = [
        "fact_invoice",
        "fact_invoice_line",
        "fact_order",
        "fact_order_line",
        "fact_dispatch",
        "fact_returns",
        "dim_customer",
        "dim_item",
    ]
    rows = [
        (d + pd.Timedelta(minutes=30 + 4 * k), t, int(r.integers(0, 900)), "ok")
        for d in days
        for k, t in enumerate(tables)
    ]
    df = pd.DataFrame(rows, columns=["started_at", "table_name", "rows_loaded", "status"])
    df.insert(0, "run_id", np.arange(1, len(df) + 1))
    return df


def vendor_rating(seed: int) -> pd.DataFrame:
    r = co.stream(seed, "vendor-rating")
    n = 1140
    return pd.DataFrame(
        {
            "vendor_code": [f"V{int(x):04d}" for x in r.integers(1, 96, n)],
            "quarter": [f"FY{22 + i % 5}Q{1 + i % 4}" for i in range(n)],
            "score": np.round(r.uniform(2.5, 5.0, n), 1),
        }
    )


def print_log(seed: int, fact_invoice: pd.DataFrame) -> pd.DataFrame:
    r = co.stream(seed, "print-log")
    n = r.integers(1, 3, len(fact_invoice))
    inv = np.repeat(fact_invoice["invoice_no"].to_numpy(), n)
    at = np.repeat(fact_invoice["created_at"].to_numpy(), n) + r.integers(
        60, 86_400, len(inv)
    ).astype("timedelta64[s]")
    return pd.DataFrame(
        {
            "print_id": np.arange(1, len(inv) + 1),
            "invoice_no": inv,
            "printed_at": at,
            "printer": r.choice(["SU-ACC-01", "SU-DSP-02", "CB-ACC-01"], len(inv)),
        }
    )


# Reference and noise tables so the schemas look like the real thing (scan board counts).
ERP_FILLERS = [
    ("gl_accounts", "ref"),
    ("plants", "ref"),
    ("warehouses", "ref"),
    ("tax_codes", "ref"),
    ("payment_terms", "ref"),
    ("currencies", "ref"),
    ("uom", "ref"),
    ("price_lists", "ref"),
    ("vendors", "ref"),
    ("purchase_orders", "fact"),
    ("purchase_order_lines", "fact"),
    ("vendor_invoices", "fact"),
    ("grn", "fact"),
    ("stock_ledger", "fact"),
    ("production_orders", "fact"),
    ("bom", "ref"),
    ("quality_checks", "fact"),
    ("machine_hours", "fact"),
    ("yarn_issues", "fact"),
    ("dye_batches", "fact"),
    ("journal_vouchers", "fact"),
    ("cheque_register", "fact"),
    ("tds_entries", "fact"),
    ("einvoice_irn", "fact"),
    ("eway_bills", "fact"),
    ("budget_lines", "ref"),
    ("cost_centres", "ref"),
    ("asset_register", "ref"),
    ("depreciation", "fact"),
    ("sales_targets", "ref"),
    ("discount_schemes", "ref"),
    ("transporters", "ref"),
    ("routes", "ref"),
    ("lc_register", "fact"),
    ("forex_contracts", "fact"),
    ("invoices_bak_2024", "backup"),
    ("customers_old", "backup"),
    ("tmp_import", "empty"),
    ("tmp_recon", "empty"),
    ("scratch_2023", "empty"),
    ("old_ledger_2019", "old"),
    ("audit_log", "log"),
    ("sync_log", "log"),
    ("job_log", "log"),
    ("login_audit", "log"),
    ("report_cache", "empty"),
    ("menu_config", "ref"),
    ("number_series", "ref"),
    ("approval_matrix", "ref"),
    ("doc_attachments", "ref"),
    ("email_queue", "empty"),
    ("print_templates", "ref"),
    ("holiday_calendar", "ref"),
    ("fy_periods", "ref"),
    ("branch_master", "ref"),
    ("hsn_codes", "ref"),
]
DW_FILLERS = {
    "sales_dw": [
        ("dim_date", "ref"),
        ("dim_plant", "ref"),
        ("dim_family", "ref"),
        ("dim_channel", "ref"),
        ("dim_currency", "ref"),
        ("dim_salesperson_code", "ref"),
        ("fact_quality", "fact"),
        ("fact_production", "fact"),
        ("fact_stock_snapshot", "fact"),
        ("fact_price", "fact"),
        ("agg_sales_monthly", "fact"),
        ("agg_dispatch_weekly", "fact"),
        ("fact_order_bak_2024", "backup"),
        ("fact_dispatch_copy", "backup"),
        ("tmp_recon", "empty"),
        ("tmp_dedupe", "empty"),
        ("tmp_export", "empty"),
        ("tmp_stage_lines", "empty"),
        ("load_audit", "log"),
        ("dq_results", "log"),
        ("refresh_log", "log"),
        ("dim_reason", "ref"),
        ("dim_stage", "ref"),
    ],
    "public": [
        ("dim_plant", "ref"),
        ("dim_calendar", "ref"),
        ("config_kv", "ref"),
        ("tmp_export", "empty"),
        ("load_errors", "log"),
    ],
}


# Procurement for Discover (Advanced Intelligence D1, 17 Nov 2026): real purchase orders in place
# of the filler, so Discover finds its Procurement group. Planted (D1-P): from 1 Aug 2026 one
# supplier, vendor 7, delivers about nine days late, so Supplier days late and Share delivered
# late rise in the last three months; the other suppliers are on time give or take two days.
PO_LATE_VENDOR = 7
PO_LATE_FROM = dt.date(2026, 8, 1)
PO_LAST_RECEIPT = dt.date(2026, 11, 1)


def purchase_orders(seed: int) -> pd.DataFrame:
    r = co.stream(seed, "procurement", "purchase_orders")
    start, days = dt.date(2024, 4, 1), (dt.date(2026, 10, 31) - dt.date(2024, 4, 1)).days + 1
    n = 1200
    po_dates = sorted(start + dt.timedelta(days=int(x)) for x in r.integers(0, days, n))
    vendors = r.integers(1, 26, n)
    lead = r.integers(10, 21, n)
    noise = np.round(r.normal(0, 1.2, n)).astype(int)
    late = np.round(r.normal(9, 2, n)).astype(int)
    amounts = np.round(r.lognormal(11.5, 0.6, n), 2)
    rows = []
    for i in range(n):
        po = po_dates[i]
        expected = po + dt.timedelta(days=int(lead[i]))
        delay = int(noise[i])
        if vendors[i] == PO_LATE_VENDOR and po >= PO_LATE_FROM:
            delay = max(int(late[i]), 4)
        received: dt.date | None = expected + dt.timedelta(days=delay)
        if received > PO_LAST_RECEIPT:  # still on its way
            received = None
        rows.append(
            {
                "po_id": i + 1,
                "po_no": f"PO/{po.year}/{i + 1:05d}",
                "vendor_id": int(vendors[i]),
                "po_date": po,
                "expected_date": expected,
                "received_date": received,
                "amount": float(amounts[i]),
                "status": "received" if received else "open",
            }
        )
    return pd.DataFrame(rows)


def _filler(seed: int, name: str, kind: str) -> pd.DataFrame:
    if name == "purchase_orders":
        return purchase_orders(seed)
    r = co.stream(seed, "filler", name)
    if kind == "empty":
        return pd.DataFrame(
            {"id": pd.Series([], dtype="int64"), "payload": pd.Series([], dtype="str")}
        )
    n = {
        "ref": int(r.integers(4, 60)),
        "fact": int(r.integers(200, 2500)),
        "backup": int(r.integers(500, 3000)),
        "old": int(r.integers(300, 1500)),
        "log": int(r.integers(1000, 6000)),
    }[kind]
    base = dt.date(2019 if kind == "old" else 2023, 1, 1)
    if kind == "log":
        return pd.DataFrame(
            {
                "id": np.arange(1, n + 1),
                "logged_at": pd.Timestamp(base)
                + pd.to_timedelta(np.sort(r.integers(0, 3 * 365 * 86400, n)), unit="s"),
                "event": r.choice(["start", "finish", "retry", "warn"], n),
            }
        )
    return pd.DataFrame(
        {
            "id": np.arange(1, n + 1),
            "code": [f"{name[:3].upper()}{i:05d}" for i in range(1, n + 1)],
            "label": [f"{name.replace('_', ' ')} {i}" for i in range(1, n + 1)],
            "value": np.round(r.uniform(0, 10_000, n), 2),
            "as_of": [base + dt.timedelta(days=int(x)) for x in r.integers(0, 900, n)],
        }
    )


def erp_fillers(seed: int, n: int) -> dict[str, pd.DataFrame]:
    assert n <= len(ERP_FILLERS), n
    return {name: _filler(seed, name, kind) for name, kind in ERP_FILLERS[:n]}


def dw_fillers(seed: int, schema: str, n: int) -> dict[str, pd.DataFrame]:
    pool = DW_FILLERS[schema]
    assert n <= len(pool), (schema, n)
    return {name: _filler(seed, f"{schema}.{name}", kind) for name, kind in pool[:n]}
