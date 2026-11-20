# SPDX-License-Identifier: Apache-2.0
"""generate(seed, as_of) -> SampleCompany: the Indigo Thread sample company, pure and deterministic.

`erp` holds the MySQL erp_db tables, `dw` the PostgreSQL sales_dw tables by schema, `sheets`
the Google Sheets API stand-in (one JSON document per spreadsheet). Load them with load.py.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

from tests.fixtures.sample_company import build as b
from tests.fixtures.sample_company import company as co
from tests.fixtures.sample_company import events, extras, sheets

GENERATOR_VERSION = 1


@dataclass
class SampleCompany:
    seed: int
    as_of: dt.date
    erp: dict[str, pd.DataFrame]
    dw: dict[str, dict[str, pd.DataFrame]]
    sheets: dict[str, dict[str, Any]]
    sheets_auth: dict[str, Any]
    sheets_auth_revoked: dict[str, Any]
    notes: dict[str, Any] = field(default_factory=dict)
    generator_version: int = GENERATOR_VERSION

    def personal_columns(self) -> list[tuple[str, str, str, str]]:
        """(source, schema, table, column) for every personal column planted in the sources."""
        return extras.PERSONAL

    def data_hash(self) -> str:
        h = hashlib.sha256()
        for name in sorted(self.erp):
            _hash_frame(h, f"erp_db.{name}", self.erp[name])
        for schema in sorted(self.dw):
            for name in sorted(self.dw[schema]):
                _hash_frame(h, f"sales_dw.{schema}.{name}", self.dw[schema][name])
        h.update(json.dumps(self.sheets, sort_keys=True, ensure_ascii=False).encode())
        return h.hexdigest()


def _hash_frame(h: Any, name: str, df: pd.DataFrame) -> None:
    h.update(name.encode() + b"\n")
    h.update(df.to_csv(index=False, lineterminator="\n", date_format="%Y-%m-%dT%H:%M:%S").encode())


# ---------------------------------------------------------------------------------------------


@dataclass
class Timeline:
    """The full-horizon business, before any as_of cut."""

    roster: list[co.Customer]
    items: list[co.Item]
    fx: pd.Series
    inv: pd.DataFrame
    lines: pd.DataFrame
    book: b.Book
    dispatch: pd.DataFrame
    rec: pd.DataFrame
    ledger: pd.DataFrame
    bank: pd.DataFrame
    approvals: pd.DataFrame
    deals: pd.DataFrame
    kappa: float
    notes: dict[str, Any]
    added: list[co.Customer] = field(default_factory=list)  # customers created after the seam


@lru_cache(maxsize=4)
def timeline(seed: int) -> Timeline:
    roster = co.customers(seed)
    items = co.items()
    dem = b.demand(seed)
    fx = b.fx_rates(seed)
    target = b.EXPECTED["P2"]["share_over_60_after"]
    scale = b.rate_scale(seed, roster, dem)

    def run(p2_amount: float) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
        inv = b.attach(b.invoices(seed, roster, dem, p2_amount, scale), roster, seed)
        cal = b.calibrate(inv, roster)
        inv = b.festive_boost(inv, roster)
        inv = b.currency(inv, fx)
        rec = b.receivables(seed, inv, roster)
        return inv, rec, cal

    # P2: size the mid-June batch so receivables over 60 days step to 14% (secant search).
    a0, a1 = 450_000.0, 650_000.0
    f0 = run(a0)[1].attrs["share_after"] - target
    for _ in range(6):
        inv, rec, cal = run(a1)
        f1 = rec.attrs["share_after"] - target
        if abs(f1) < 0.002 or f1 == f0:
            break
        a0, a1, f0 = a1, max(a1 - f1 * (a1 - a0) / (f1 - f0), 100_000.0), f1
    lines = b.invoice_lines(inv, roster, items, seed)
    book = b.orders(seed, inv, roster)
    dsp = b.dispatch(seed, inv, lines, book, items)
    ledger = b.costs(seed, inv, lines, dsp, cal["kappa"])
    notes = {
        "rate_scale": scale,
        "p2_batch_amount": a1,
        "order_lead_k": book.k_lead,
        **rec.attrs,
        **cal,
    }
    return Timeline(
        roster,
        items,
        fx,
        inv,
        lines,
        book,
        dsp,
        rec,
        ledger,
        b.bank(seed, rec),
        b.approvals(seed, roster),
        b.deals(seed, inv, roster),
        cal["kappa"],
        notes,
    )


@lru_cache(maxsize=4)
def with_events(seed: int) -> Timeline:
    """The timeline with the events from 23 Nov 2026 and the horizon to 31 Mar 2027 (events.py);
    the data before 23 Nov is the timeline's own, unchanged."""
    return events.apply(timeline(seed), seed)


def generate(seed: int = 20261101, as_of: dt.date = dt.date(2026, 11, 4)) -> SampleCompany:
    t = with_events(seed)
    cut = b.d64(as_of)
    erp = render_erp(t, cut, seed)
    dw = render_dw(t, cut, seed)
    books = sheets.render(t, seed)
    erp.update(extras.erp_fillers(seed, 64 - len(erp)))
    dw["sales_dw"].update(extras.dw_fillers(seed, "sales_dw", 36 - len(dw["sales_dw"])))
    dw["public"].update(extras.dw_fillers(seed, "public", 12 - len(dw["public"])))
    return SampleCompany(
        seed,
        as_of,
        erp,
        dw,
        books,
        sheets.auth(revoked=False),
        sheets.auth(revoked=True),
        notes=t.notes,
    )


# ---------------------------------------------------------------------------------------------
# Rendering as of a date


def _dates(a: np.ndarray) -> list[dt.date]:
    return [x.astype(object) for x in a.astype("datetime64[D]")]


def _ts(day: np.ndarray, seed: int, key: str, lo_h: int = 9, hi_h: int = 19) -> pd.Series:
    r = co.stream(seed, "ts", key)
    secs = r.integers(lo_h * 3600, hi_h * 3600, len(day))
    return pd.Series(
        day.astype("datetime64[D]").astype("datetime64[s]") + secs.astype("timedelta64[s]")
    )


def _ord(code: str) -> int:
    return int(code[1:])


def render_erp(t: Timeline, cut: np.datetime64, seed: int) -> dict[str, pd.DataFrame]:
    roster = events.roster_as_of(t, cut)
    by = {c.code: c for c in roster}
    out: dict[str, pd.DataFrame] = {}
    r = co.stream(seed, "erp-customers")
    out["customers"] = pd.DataFrame(
        {
            "customer_id": [_ord(c.code) for c in roster],
            "customer_code": [c.code for c in roster],
            "name": [c.name for c in roster],
            "gstin": [co.gstin(seed, c) for c in roster],
            "region": [c.region for c in roster],
            "city": [c.city for c in roster],
            "segment": [c.segment for c in roster],
            "currency": [c.currency for c in roster],
            "status": [c.status for c in roster],
            "payment_terms_days": [c.terms for c in roster],
            "credit_limit": [float(round(c.value * 30, -5)) for c in roster],
            "contact_email": [extras.fake_email(c.name, r) for c in roster],
            "contact_phone": [extras.fake_phone(r, c.currency) for c in roster],
            "created_at": _ts(
                np.array([b.d64(c.first) - 30 * b.DAY for c in roster]), seed, "cust"
            ),
            "updated_at": _ts(
                np.array([min(b.d64(c.first) + 400 * b.DAY, cut - b.DAY) for c in roster]),
                seed,
                "cust-u",
            ),
        }
    )
    out["items"] = pd.DataFrame(
        {
            "item_code": [i.code for i in t.items],
            "description": [i.description for i in t.items],
            "family": [i.family for i in t.items],
            "plant": [i.plant for i in t.items],
            "uom": "MTR",
            "std_rate": [i.rate for i in t.items],
            "created_at": pd.Timestamp("2024-03-28 11:00:00"),
        }
    )
    inv = t.inv[t.inv["erp"] & (t.inv["date"].to_numpy().astype("datetime64[D]") < cut)]
    order_of = t.book.orders.dropna(subset=["invoice_no"]).set_index("invoice_no")["order_no"]
    created = _ts(inv["date"].to_numpy().astype("datetime64[D]"), seed, "inv", 10, 20)
    out["invoices"] = pd.DataFrame(
        {
            "invoice_id": inv["invoice_id"].to_numpy(),
            "invoice_no": inv["invoice_no"].to_numpy(),
            "invoice_date": _dates(inv["date"].to_numpy().astype("datetime64[D]")),
            "customer_id": inv["cust"].map(_ord).to_numpy(),
            "currency": inv["currency"].to_numpy(),
            "fx_rate": inv["fx_rate"].to_numpy(),
            "amount_fc": inv["amount_fc"].to_numpy(),
            "net_amount": inv["net"].to_numpy(),
            "gst_amount": inv["gst"].to_numpy(),
            "gross_amount": inv["gross"].to_numpy(),
            "status": "posted",
            "order_ref": inv["invoice_no"].map(order_of).to_numpy(),
            "created_at": created.to_numpy(),
            "updated_at": created.to_numpy(),
        }
    )
    ln = t.lines[np.isin(t.lines["invoice_id"].to_numpy(), inv["invoice_id"].to_numpy())]
    out["invoice_lines"] = pd.DataFrame(
        {
            "line_id": np.arange(1, len(ln) + 1),
            "invoice_id": ln["invoice_id"].to_numpy(),
            "line_no": ln["line_no"].to_numpy(),
            "item_code": ln["item_code"].to_numpy(),
            "qty_m": ln["qty_m"].to_numpy(),
            "rate": ln["rate"].to_numpy(),
            "net_amount": ln["net"].to_numpy(),
        }
    )
    # credit notes are in erp_db from the day they're posted (P16: one posted late)
    cn = t.inv[
        (t.inv["cn_frac"] > 0) & (t.inv["cn_posted"].to_numpy().astype("datetime64[D]") < cut)
    ]
    cn = cn.sort_values(["cn_date", "invoice_id"], kind="stable")
    cn_net = np.round(t.kappa * cn["cn_frac"].to_numpy() * cn["net"].to_numpy(), 2)
    out["credit_notes"] = pd.DataFrame(
        {
            "cn_id": np.arange(1, len(cn) + 1),
            "cn_no": [f"CN/{i + 1:06d}" for i in range(len(cn))],
            "cn_date": _dates(cn["cn_date"].to_numpy().astype("datetime64[D]")),
            "invoice_id": cn["invoice_id"].to_numpy(),
            "customer_id": cn["cust"].map(_ord).to_numpy(),
            "net_amount": cn_net,
            "gst_amount": np.where(cn["currency"] == "EUR", 0.0, np.round(cn_net * 0.05, 2)),
            "reason": cn["cn_reason"].to_numpy(),
            "created_at": _ts(
                cn["cn_posted"].to_numpy().astype("datetime64[D]"), seed, "cn"
            ).to_numpy(),
        }
    )
    rec = t.rec[t.rec["date"].to_numpy().astype("datetime64[D]") < cut]
    paid = rec["paid_true"].to_numpy().astype("datetime64[D]") < cut
    out["receivables"] = pd.DataFrame(
        {
            "receivable_id": np.arange(1, len(rec) + 1),
            "invoice_id": rec["invoice_id"].to_numpy(),
            "customer_id": rec["cust"].map(_ord).to_numpy(),
            "invoice_date": _dates(rec["date"].to_numpy().astype("datetime64[D]")),
            "due_date": _dates(rec["due"].to_numpy().astype("datetime64[D]")),
            "amount": rec["gross"].to_numpy(),
            "paid_amount": np.where(paid, rec["gross"].to_numpy(), rec["partial_paid"].to_numpy()),
            "paid_date": [
                d if p else None
                for d, p in zip(
                    _dates(
                        np.where(
                            paid,
                            rec["paid_true"].to_numpy().astype("datetime64[D]"),
                            rec["date"].to_numpy().astype("datetime64[D]"),
                        )
                    ),
                    paid,
                    strict=True,
                )
            ],
            "status": np.where(paid, "P", "O"),
            "updated_at": _ts(
                np.where(
                    paid,
                    rec["paid_true"].to_numpy().astype("datetime64[D]"),
                    rec["date"].to_numpy().astype("datetime64[D]"),
                ),
                seed,
                "rec",
            ).to_numpy(),
        }
    )
    bk = t.bank[t.bank["entry_date"].to_numpy().astype("datetime64[D]") < cut]
    out["bank_ledger"] = pd.DataFrame(
        {
            "entry_id": bk["entry_id"].to_numpy(),
            "bank": bk["bank"].to_numpy(),
            "entry_date": _dates(bk["entry_date"].to_numpy().astype("datetime64[D]")),
            "description": bk["description"].to_numpy(),
            "debit": bk["debit"].to_numpy(),
            "credit": bk["credit"].to_numpy(),
            "balance": bk["balance"].to_numpy(),
            "created_at": _ts(
                bk["entry_date"].to_numpy().astype("datetime64[D]"), seed, "bank", 18, 22
            ).to_numpy(),
        }
    )
    lg = t.ledger[t.ledger["entry_date"].to_numpy().astype("datetime64[D]") < cut]
    out["cost_ledger"] = pd.DataFrame(
        {
            "entry_id": lg["entry_id"].to_numpy(),
            "entry_date": _dates(lg["entry_date"].to_numpy().astype("datetime64[D]")),
            "account_code": lg["account_code"].to_numpy(),
            "account_name": lg["account_name"].to_numpy(),
            "plant": lg["plant"].to_numpy(),
            "customer_id": [(_ord(c) if isinstance(c, str) else None) for c in lg["cust"]],
            "amount": lg["amount"].to_numpy(),
            "narration": lg["narration"].to_numpy(),
            "created_at": _ts(
                lg["entry_date"].to_numpy().astype("datetime64[D]"), seed, "ledger", 19, 23
            ).to_numpy(),
        }
    )
    ap = t.approvals[t.approvals["request_date"].to_numpy().astype("datetime64[D]") < cut]
    out["approvals"] = pd.DataFrame(
        {
            "approval_id": ap["approval_id"].to_numpy(),
            "request_date": _dates(ap["request_date"].to_numpy().astype("datetime64[D]")),
            "customer_id": ap["cust"].map(_ord).to_numpy(),
            "discount_amount": ap["discount_amount"].to_numpy(),
            "approval_limit": ap["approval_limit"].to_numpy(),
            "approved_by_role": ap["approved_by_role"].to_numpy(),
            "status": ap["status"].to_numpy(),
            "decided_at": [
                d if d < cut.astype(object) else None
                for d in _dates(ap["decided_at"].to_numpy().astype("datetime64[D]"))
            ],
        }
    )
    fx = t.fx[t.fx.index < pd.Timestamp(cut.astype(object))]
    out["fx_rates"] = pd.DataFrame(
        {"rate_date": _dates(fx.index.to_numpy()), "eur_inr": fx.to_numpy()}
    )
    del by
    return out


def order_status(t: Timeline, cut: np.datetime64) -> pd.DataFrame:
    o = t.book.orders
    o = o[o["order_date"].to_numpy().astype("datetime64[D]") < cut].copy()
    invoiced = o["close"].to_numpy().astype("datetime64[D]") < cut
    dispatched = set(
        t.dispatch.loc[
            t.dispatch["dispatched_at"].to_numpy() < cut.astype("datetime64[s]"), "order_no"
        ].dropna()
    )
    lead = (
        o["close"].to_numpy().astype("datetime64[D]")
        - o["order_date"].to_numpy().astype("datetime64[D]")
    ).astype("int64")
    elapsed = (cut - o["order_date"].to_numpy().astype("datetime64[D]")).astype("int64")
    stage = np.where(elapsed < 0.5 * np.maximum(lead, 1), "Confirmed", "In production")
    stage = np.where(o["order_no"].isin(dispatched), "Dispatched", stage)
    fixed = o["fixed_status"].to_numpy()
    stage = np.where(pd.notna(fixed), fixed, stage)
    o["status"] = np.where(invoiced, "Invoiced", stage)
    o["invoice_no_asof"] = np.where(invoiced, o["invoice_no"], None)
    return o


def render_dw(t: Timeline, cut: np.datetime64, seed: int) -> dict[str, dict[str, pd.DataFrame]]:
    sd: dict[str, pd.DataFrame] = {}
    pub: dict[str, pd.DataFrame] = {}
    key = {c.code: i + 1 for i, c in enumerate([*t.roster, *t.added])}
    roster = events.roster_as_of(t, cut)
    inv = t.inv[t.inv["date"].to_numpy().astype("datetime64[D]") < cut]
    created = _ts(inv["date"].to_numpy().astype("datetime64[D]"), seed, "dw-inv", 20, 23)
    fi = pd.DataFrame(
        {
            "invoice_key": np.arange(1, len(inv) + 1),
            "invoice_no": inv["invoice_no"].to_numpy(),
            "invoice_date": _dates(inv["date"].to_numpy().astype("datetime64[D]")),
            "customer_key": inv["cust"].map(key).to_numpy(),
            "region": inv["region"].to_numpy(),
            "invoice_type": np.where(inv["currency"] == "EUR", "EXP", "DOM"),
            "currency": inv["currency"].to_numpy(),
            "net_value": inv["net"].to_numpy(),
            "created_at": created.to_numpy(),
            "updated_at": created.to_numpy(),
        }
    )
    sd["fact_invoice"] = fi
    sd["fact_invoice_bak_2023"] = fi[
        [d <= dt.date(2023, 12, 31) for d in fi["invoice_date"]]
    ].reset_index(drop=True)
    ln = t.lines[t.lines["date"].to_numpy().astype("datetime64[D]") < cut]
    sd["fact_invoice_line"] = pd.DataFrame(
        {
            "invoice_no": ln["invoice_no"].to_numpy(),
            "line_no": ln["line_no"].to_numpy(),
            "item_code": ln["item_code"].to_numpy(),
            "qty_mtr": ln["qty_m"].to_numpy(),
            "rate": ln["rate"].to_numpy(),
            "net_value": ln["net"].to_numpy(),
        }
    )
    o = order_status(t, cut)
    r = co.stream(seed, "promised")
    fo = pd.DataFrame(
        {
            "order_key": np.arange(1, len(o) + 1),
            "order_no": o["order_no"].to_numpy(),
            "order_date": _dates(o["order_date"].to_numpy().astype("datetime64[D]")),
            "customer_key": o["cust"].map(key).to_numpy(),
            "region": o["region"].to_numpy(),
            "promised_date": _dates(
                np.where(
                    o["close"].to_numpy().astype("datetime64[D]") < b.INF,
                    o["close"].to_numpy().astype("datetime64[D]"),
                    o["order_date"].to_numpy().astype("datetime64[D]") + 30 * b.DAY,
                )
                + r.integers(-3, 6, len(o)) * b.DAY
            ),
            "amount": o["amount"].to_numpy(),
            "status": o["status"].to_numpy(),
            "invoice_no": o["invoice_no_asof"].to_numpy(),
            "updated_at": _ts(
                np.minimum(
                    np.where(
                        o["close"].to_numpy().astype("datetime64[D]") < cut,
                        o["close"].to_numpy().astype("datetime64[D]"),
                        cut - b.DAY,
                    ),
                    cut - b.DAY,
                ),
                seed,
                "fo",
            ).to_numpy(),
        }
    )
    sd["fact_order"] = fo
    pub["stg_orders"] = (
        fo.drop(columns=["updated_at"]).iloc[: max(len(fo) - 1500, 0)].reset_index(drop=True)
    )
    # Order lines: invoice lines for invoiced/pipeline orders; one line for the others.
    ord_by_inv = o.dropna(subset=["invoice_no"]).set_index("invoice_no")["order_no"]
    il = t.lines[t.lines["invoice_no"].isin(ord_by_inv.index)]
    ol = pd.DataFrame(
        {
            "order_no": il["invoice_no"].map(ord_by_inv).to_numpy(),
            "line_no": il["line_no"].to_numpy(),
            "item_code": il["item_code"].to_numpy(),
            "qty_mtr": il["qty_m"].to_numpy(),
            "rate": il["rate"].to_numpy(),
            "amount": il["net"].to_numpy(),
        }
    )
    other = o[o["invoice_no"].isna()]
    first_item = t.items[0]
    ol2 = pd.DataFrame(
        {
            "order_no": other["order_no"].to_numpy(),
            "line_no": 1,
            "item_code": first_item.code,
            "qty_mtr": np.round(other["amount"].to_numpy() / 117.4, 1),
            "rate": 117.4,
            "amount": other["amount"].to_numpy(),
        }
    )
    sd["fact_order_line"] = (
        pd.concat([ol, ol2], ignore_index=True)
        .sort_values(["order_no", "line_no"], kind="stable")
        .reset_index(drop=True)
    )
    dsp = t.dispatch[t.dispatch["dispatched_at"].to_numpy() < cut.astype("datetime64[s]")]
    inv_date = t.inv.set_index("invoice_no")["date"]
    visible = dsp["invoice_no"].map(inv_date).to_numpy()
    sd["fact_dispatch"] = pd.DataFrame(
        {
            "dispatch_key": dsp["dispatch_key"].to_numpy(),
            "order_no": dsp["order_no"].to_numpy(),
            "invoice_no": np.where(
                pd.notna(visible) & (visible.astype("datetime64[D]") < cut), dsp["invoice_no"], None
            ),
            "plant": dsp["plant"].to_numpy(),
            "item_code": dsp["item_code"].to_numpy(),
            "ready_at": dsp["ready_at"].to_numpy(),
            "dispatched_at": dsp["dispatched_at"].to_numpy(),
            "dispatch_date": _dates(dsp["dispatched_at"].to_numpy()),
            "dispatch_mtrs": dsp["dispatch_mtrs"].to_numpy(),
            "uom": dsp["uom"].to_numpy(),
        }
    )
    ret = t.inv[
        (t.inv["cn_frac"] > 0)
        & (t.inv["cn_reason"] == "Return")
        & (t.inv["cn_date"].to_numpy().astype("datetime64[D]") < cut)
    ]
    first_line = t.lines.drop_duplicates("invoice_no").set_index("invoice_no")
    val = np.round(t.kappa * ret["cn_frac"].to_numpy() * ret["net"].to_numpy(), 2)
    rate = ret["invoice_no"].map(first_line["rate"]).to_numpy()
    sd["fact_returns"] = pd.DataFrame(
        {
            "return_key": np.arange(1, len(ret) + 1),
            "invoice_no": ret["invoice_no"].to_numpy(),
            "return_date": _dates(ret["cn_date"].to_numpy().astype("datetime64[D]")),
            "item_code": ret["invoice_no"].map(first_line["item_code"]).to_numpy(),
            "qty_mtr": np.round(val / rate, 1),
            "reason": "Return",
            "value": val,
        }
    )
    sd["dim_customer"] = pd.DataFrame(
        {
            "customer_key": [key[c.code] for c in roster],
            "customer_code": [c.code for c in roster],
            "customer_name": [c.name for c in roster],
            "gstin": [co.gstin(seed, c) for c in roster],
            "region": [c.region for c in roster],
            "segment": [c.segment for c in roster],
            "currency": [c.currency for c in roster],
            "is_active": [c.status == "active" for c in roster],
            "owner_code": [
                "PM" if c.segment == "key" else ("RK" if c.region in ("South", "East") else "SV")
                for c in roster
            ],
            "updated_at": pd.Timestamp("2026-10-30 23:10:00"),
        }
    )
    sd["dim_item"] = pd.DataFrame(
        {
            "item_key": np.arange(1, len(t.items) + 1),
            "item_code": [i.code for i in t.items],
            "item_desc": [i.description for i in t.items],
            "category": [i.family for i in t.items],
            "plant": [i.plant for i in t.items],
            "uom": ["YD" if i.plant == "Coimbatore" else "MTR" for i in t.items],
            "rate": [i.rate for i in t.items],
        }
    )
    sd["dim_invoice_type"] = pd.DataFrame(
        {
            "type_code": [
                "DOM",
                "EXP",
                "CRN",
                "DBN",
                "PRO",
                "SMP",
                "SEZ",
                "DEX",
                "JOB",
                "STR",
                "ADV",
                "CAN",
                "RCM",
                "OTH",
            ],
            "type_name": [
                "Domestic",
                "Export",
                "Credit note",
                "Debit note",
                "Proforma",
                "Sample",
                "SEZ supply",
                "Deemed export",
                "Job work",
                "Stock transfer",
                "Advance receipt",
                "Cancelled",
                "Reverse charge",
                "Other",
            ],
        }
    )
    sd["fact_invoice_2019"] = extras.old_invoices(seed, t.roster)
    sd["tmp_load"] = pd.DataFrame(
        {"load_id": pd.Series([], dtype="int64"), "payload": pd.Series([], dtype="str")}
    )
    sd["invoice_contacts"] = extras.contacts(seed, "invoice-contacts", 3410, t.roster)
    sd["crm_contacts"] = extras.contacts(seed, "crm-contacts", 18420, t.roster)
    pub["employee"] = extras.employees(seed, 2140)
    pub["dim_region"] = pd.DataFrame({"region_key": [1, 2, 3, 4, 5], "region": co.REGIONS})
    rec = t.rec[
        (t.rec["date"].to_numpy().astype("datetime64[D]") < cut)
        & (t.rec["paid_true"].to_numpy().astype("datetime64[D]") >= cut)
    ]
    pub["ar_open_items"] = pd.DataFrame(
        {
            "customer_code": rec["cust"].to_numpy(),
            "invoice_no": rec["invoice_no"].to_numpy(),
            "due_date": _dates(rec["due"].to_numpy().astype("datetime64[D]")),
            "amount_open": np.round(rec["gross"].to_numpy() - rec["partial_paid"].to_numpy(), 2),
            "days_overdue": np.maximum(
                (cut - b.DAY - rec["due"].to_numpy().astype("datetime64[D]")).astype("int64"), 0
            ),
            "snapshot_date": (cut - b.DAY).astype(object),
        }
    )
    pub["etl_log"] = extras.etl_log(seed, cut)
    pub["vendor_rating"] = extras.vendor_rating(seed)
    pub["invoice_print_log"] = extras.print_log(seed, fi)
    return {"public": pub, "sales_dw": sd}
