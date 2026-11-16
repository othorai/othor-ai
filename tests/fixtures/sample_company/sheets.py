# SPDX-License-Identifier: Apache-2.0
"""Google Sheets API stand-in: MIS-Weekly and Order tracker FY27 (docs/11, boards 10b, 10c).

Each spreadsheet is one JSON document shaped like `spreadsheets.get` (properties, sheets with
gridProperties and merges) plus each tab's `values.get` result as formatted strings, the way
the API returns FORMATTED_VALUE. Fields the API doesn't give per tab are prefixed `x_`
(the M2 engine reads Drive revisions for them).
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from tests.fixtures.sample_company import build as b
from tests.fixtures.sample_company import company as co
from tests.fixtures.sample_company import extras

if TYPE_CHECKING:
    from tests.fixtures.sample_company.generate import Timeline

MIS_ID = "sample-mis-weekly"
TRACKER_ID = "sample-order-tracker-fy27"
# Google Picker + drive.file only (docs/05-connectors.md, decided 4 Nov 2026).
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def auth(revoked: bool) -> dict[str, Any]:
    return {
        "account": co.OWNER["google_account"],
        "scopes": SCOPES,
        "granted_at": "2026-10-20T11:42:00+05:30",
        "revoked_at": "2026-10-31T23:40:00+05:30" if revoked else None,
    }


def _tab(
    sheet_id: int,
    title: str,
    values: list[list[str]],
    merges: list[dict[str, int]] | None = None,
    edited: str | None = None,
) -> dict[str, Any]:
    cols = max((len(r) for r in values), default=0)
    return {
        "properties": {
            "sheetId": sheet_id,
            "title": title,
            "index": sheet_id,
            "gridProperties": {"rowCount": max(len(values), 1) + 20, "columnCount": max(cols, 1)},
        },
        "merges": merges or [],
        "values": values,
        "x_lastEditedTime": edited,
    }


def _lakh(v: float, nd: int = 2) -> str:
    return f"{v / 1e5:,.{nd}f}"


def _daily_net(t: Timeline) -> pd.DataFrame:
    """Net revenue per day and region (invoices − credit notes − rebates), erp_db era."""
    inv = t.inv[t.inv["erp"]]
    g = pd.DataFrame(
        {
            "day": inv["date"].to_numpy().astype("datetime64[D]"),
            "region": inv["region"].to_numpy(),
            "v": inv["net"].to_numpy(),
        }
    )
    cn = t.inv[t.inv["cn_frac"] > 0]
    c = pd.DataFrame(
        {
            "day": cn["cn_date"].to_numpy().astype("datetime64[D]"),
            "region": cn["region"].to_numpy(),
            "v": -np.round(t.kappa * cn["cn_frac"].to_numpy() * cn["net"].to_numpy(), 2),
        }
    )
    reg = {x.code: x.region for x in t.roster}
    rb = t.ledger[t.ledger["account_code"] == 4410]
    r = pd.DataFrame(
        {
            "day": rb["entry_date"].to_numpy().astype("datetime64[D]"),
            "region": rb["cust"].map(reg).to_numpy(),
            "v": -rb["amount"].to_numpy(),
        }
    )
    return pd.concat([g, c, r], ignore_index=True)


def mis_weekly(t: Timeline) -> dict[str, Any]:
    gst = b.EXPECTED["P9"]["gst_rate_in_mis"]
    daily = _daily_net(t)
    last = np.datetime64("2026-10-31")
    daily = daily[(daily["day"] >= b.d64(co.ERP_START)) & (daily["day"] <= last)]
    day = daily["day"].to_numpy().astype("datetime64[D]")
    month_start = day.astype("datetime64[M]").astype("datetime64[D]")
    monday = day - ((day.astype("int64") + 3) % 7) * b.DAY
    part = np.maximum(monday, month_start)
    daily = daily.assign(part=part)
    grp = daily.groupby(["part", "region"])["v"].sum()
    sales = [["Month", "Week", "Region", "Sales incl. GST (₹ L)"]]
    for (p, reg), v in grp.items():
        d = pd.Timestamp(p)
        sales.append(
            [d.strftime("%b %Y"), d.strftime("%a %-d %b"), reg, f"{v * (1 + gst) / 1e5:.2f}"]
        )
    # Receivables ageing, every Friday.
    rec = t.rec
    rows = [["Week", "0–30 (₹ L)", "31–60 (₹ L)", "61–90 (₹ L)", "90+ (₹ L)"]]
    fri = pd.date_range("2024-04-05", "2026-10-24", freq="W-FRI")
    date, due, paid = (
        rec["date"].to_numpy().astype("datetime64[D]"),
        rec["due"].to_numpy().astype("datetime64[D]"),
        rec["paid_true"].to_numpy().astype("datetime64[D]"),
    )
    gross = rec["gross"].to_numpy()
    for f in fri.to_numpy().astype("datetime64[D]"):
        open_ = (date <= f) & (paid > f)
        age = (f - due).astype("int64")
        buckets = [
            gross[open_ & (age <= 30)].sum(),
            gross[open_ & (age > 30) & (age <= 60)].sum(),
            gross[open_ & (age > 60) & (age <= 90)].sum(),
            gross[open_ & (age > 90)].sum(),
        ]
        rows.append([pd.Timestamp(f).strftime("%d-%m-%Y"), *[_lakh(x) for x in buckets]])
    notes = [
        ["Sales figures include GST (as booked in the MIS template)."],
        ["Receivables from erp_db ageing report, pasted every Friday."],
        ["W1 Oct: Diwali dispatches brought forward at Surat."],
        ["Coimbatore dispatch counted in yards in the DW; converted here by Aditya."],
        ["Skarfab paused orders until 1 Dec (call with Leela, 30 Oct)."],
    ]
    old = [["Month", "Sales (₹ Cr)", "Collections (₹ Cr)"]]
    for i, m in enumerate(pd.period_range("2023-04", "2024-03", freq="M")):
        old.append([m.strftime("%b %Y"), f"{3.7 + 0.08 * i:.2f}", f"{3.5 + 0.07 * i:.2f}"])
    return {
        "spreadsheetId": MIS_ID,
        "properties": {"title": "MIS-Weekly", "locale": "en_IN", "timeZone": "Asia/Kolkata"},
        "x_drive": {
            "modifiedTime": "2026-10-27T10:12:00+05:30",
            "lastModifyingUser": {"displayName": co.PEOPLE["finance"]["name"]},
        },
        "sheets": [
            _tab(0, "Sales", sales, edited="2026-10-27T10:12:00+05:30"),
            _tab(1, "Receivables", rows, edited="2026-10-24T17:40:00+05:30"),
            _tab(2, "Notes", notes, edited="2026-10-30T16:05:00+05:30"),
            _tab(3, "Old-2024", old, edited="2024-04-12T12:00:00+05:30"),
        ],
    }


def _open_orders(t: Timeline) -> tuple[list[list[str]], list[dict[str, int]]]:
    e = b.EXPECTED["board_extras"]
    names = {c.code: c for c in t.roster}
    run = b.d64("2026-11-01")
    o = t.book.orders
    dispatched = set(
        t.dispatch.loc[
            t.dispatch["dispatched_at"].to_numpy() < run.astype("datetime64[s]"), "order_no"
        ].dropna()
    )

    def label(code: str) -> tuple[str, str]:
        c = names[code]
        return c.name, ("EU export" if c.currency == "EUR" else c.region)

    def stage(row: Any) -> str:
        if isinstance(row.fixed_status, str):
            return str(row.fixed_status)
        if row.order_no in dispatched:
            return "Dispatched"
        lead = (pd.Timestamp(row.close) - pd.Timestamp(row.order_date)).days
        elapsed = (pd.Timestamp(run) - pd.Timestamp(row.order_date)).days
        return "Confirmed" if elapsed < 0.5 * lead else "In production"

    def expected(row: Any) -> str:
        if stage(row) == "On hold":
            return "—"
        close, od = pd.Timestamp(row.close), pd.Timestamp(row.order_date)
        when = close if close < pd.Timestamp(b.INF) else od + pd.Timedelta(days=40)
        return pd.Timestamp(when).strftime("%-d %b")

    board = o[o["kind"] == "board"].sort_values("order_date")
    # Priya tracks October's open orders; a few invoiced in the last week aren't updated yet.
    cand = o[
        (o["kind"] != "board")
        & (o["order_date"] >= b.d64("2026-10-05"))
        & (o["order_date"] <= b.d64("2026-10-28"))
        & (o["close"] > b.d64("2026-10-24"))
    ]
    mid = cand.nlargest(81, "amount").sort_values(["order_date", "order_no"], kind="stable")
    # Fixed board rows: 4, 5 (Ravira, merged), 6 (Tessora), 88 (Skarfab).
    fixed_vals = [(18_400, 21.6), (9_200, 10.8), (24_000, 31.2), (12_500, 16.4)]
    tot_m = e["open_orders_sheet_total_m"] - sum(m for m, _ in fixed_vals)
    tot_l = round(e["open_orders_sheet_total_lakh"] - sum(v for _, v in fixed_vals), 1)
    a = mid["amount"].to_numpy()
    r = co.stream(0, "tracker-rates")
    metres = np.round(a / a.sum() * tot_m * r.uniform(0.95, 1.05, len(a)), -2)
    metres[np.argmax(metres)] += tot_m - metres.sum()
    tenths = np.round(a / a.sum() * tot_l * 10)
    tenths[np.argmax(tenths)] += round(tot_l * 10) - tenths.sum()
    rows: list[list[str]] = []
    for (m_, v_), row in zip(fixed_vals[:3], board.iloc[:3].itertuples(), strict=True):
        nm, reg = label(row.cust)
        rows.append([row.order_date, nm, reg, m_, v_, stage(row), expected(row)])
    for row, m_, v_ in zip(mid.itertuples(), metres, tenths, strict=True):
        nm, reg = label(row.cust)
        rows.append([row.order_date, nm, reg, int(m_), v_ / 10, stage(row), expected(row)])
    last = board.iloc[3]
    nm, reg = label(last.cust)
    rows.append(
        [last.order_date, nm, reg, fixed_vals[3][0], fixed_vals[3][1], stage(last), expected(last)]
    )
    if not any(rw[5] == "Dispatched" for rw in rows):
        rows[3 + int(np.argmax(metres))][5] = "Dispatched"
    rows[37][1:3] = rows[36][1:3]  # rows 40–41 are one customer (merged in the sheet)
    values: list[list[str]] = [
        ["Order tracker FY27", "", "", "", "", "", "", ""],
        ["Updated by Priya · weekly", "", "", "", "", "", "", ""],
        [
            "Order date",
            "Customer",
            "Region",
            "Metres",
            "Value (₹ L)",
            "",
            "Stage",
            "Expected invoice",
        ],
    ]
    for i, (d, nm, reg, m_, v_, st, ex) in enumerate(rows):
        sheet_row = i + 4
        day = pd.Timestamp(d)
        ds = day.strftime("%Y-%m-%d") if sheet_row % 4 == 1 else day.strftime("%d-%m-%Y")
        values.append(
            [ds, "" if sheet_row in (5, 41) else nm, reg, f"{int(m_):,}", f"{v_:.1f}", "", st, ex]
        )
    values.append(
        [
            "TOTAL",
            "",
            "",
            f"{e['open_orders_sheet_total_m']:,}",
            f"{e['open_orders_sheet_total_lakh']:,.1f}",
            "",
            "",
            "",
        ]
    )
    merges = [
        {
            "sheetId": 0,
            "startRowIndex": 3,
            "endRowIndex": 5,
            "startColumnIndex": 1,
            "endColumnIndex": 2,
        },
        {
            "sheetId": 0,
            "startRowIndex": 39,
            "endRowIndex": 41,
            "startColumnIndex": 1,
            "endColumnIndex": 2,
        },
    ]
    return values, merges


def order_tracker(t: Timeline, seed: int) -> dict[str, Any]:
    open_values, merges = _open_orders(t)
    d = t.deals
    deals = [["Deal ID", "Customer", "Created", "Closed", "Outcome", "Value (₹ L)", "Owner"]]
    for row in d.itertuples():
        deals.append(
            [
                row.deal_id,
                row.customer,
                "" if pd.isna(row.created) else row.created.strftime("%Y-%m-%d"),
                row.closed.strftime("%Y-%m-%d"),
                row.outcome,
                f"{row.value_lakh:.1f}",
                row.owner,
            ]
        )
    r = co.stream(seed, "activities")
    acts = [["Date", "Deal ID", "Type", "Owner"]]
    for i in sorted(r.integers(0, len(d), 600).tolist()):
        row = d.iloc[i]
        acts.append(
            [
                (row.closed - pd.Timedelta(days=int(r.integers(1, 30)))).strftime("%Y-%m-%d"),
                row.deal_id,
                str(r.choice(["Call", "Visit", "Sample sent", "Quote"])),
                row.owner,
            ]
        )
    products = [["Item code", "Description", "Family", "Plant", "Rate (₹/m)"]]
    products += [[i.code, i.description, i.family, i.plant, f"{i.rate:.2f}"] for i in t.items]
    rc = co.stream(seed, "sheet-contacts")
    contacts = [["Name", "Phone", "Email", "Customer"]]
    for k in range(140):
        c = t.roster[int(rc.integers(0, len(t.roster)))]
        nm = extras._person(rc)
        contacts.append(
            [nm, extras.fake_phone(rc), f"{nm.split()[0].lower()}{k}@example.in", c.name]
        )
    stages = ["Confirmed", "In production", "Dispatched", "On hold"]
    data = open_values[3:-1]
    summary = [["Summary of open orders (pivot)", ""], ["", ""], ["Stage", "Orders", "Value (₹ L)"]]
    for s in stages:
        sel = [rw for rw in data if rw[6] == s]
        summary.append([s, str(len(sel)), f"{sum(float(rw[4]) for rw in sel):,.1f}"])
    summary.append(["Grand total", str(len(data)), open_values[-1][4]])
    return {
        "spreadsheetId": TRACKER_ID,
        "properties": {
            "title": "Order tracker FY27",
            "locale": "en_IN",
            "timeZone": "Asia/Kolkata",
        },
        "x_drive": {
            "modifiedTime": "2026-11-03T09:20:00+05:30",
            "lastModifyingUser": {"displayName": co.PEOPLE["sales"]["name"]},
        },
        "sheets": [
            _tab(0, "Open orders", open_values, merges, edited="2026-11-03T09:20:00+05:30"),
            _tab(1, "Deals", deals, edited="2026-11-01T18:30:00+05:30"),
            _tab(2, "Activities", acts, edited="2026-11-01T18:30:00+05:30"),
            _tab(3, "Products", products, edited="2026-04-02T10:00:00+05:30"),
            _tab(4, "Contacts", contacts, edited="2026-09-15T12:00:00+05:30"),
            _tab(5, "Summary", summary, edited="2026-11-03T09:21:00+05:30"),
        ],
    }


def render(t: Timeline, seed: int) -> dict[str, dict[str, Any]]:
    """Sheets are authored documents, the same whatever as_of (MIS to the week of Mon 26 Oct)."""
    return {MIS_ID: mis_weekly(t), TRACKER_ID: order_tracker(t, seed)}


__all__ = ["auth", "dt", "render"]
