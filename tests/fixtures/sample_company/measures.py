# SPDX-License-Identifier: Apache-2.0
"""Reference measurements on a generated SampleCompany, from the rendered tables only.

Each function computes a figure the way a direct SQL check would, so tests prove the planted
signal is in the data the connectors will read. Later milestones reuse these as oracles
(e.g. M4 "every Metrics value matches a direct SQL check").
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

import numpy as np
import pandas as pd

from tests.fixtures.sample_company import company as co
from tests.fixtures.sample_company.generate import SampleCompany

D = dt.date
YARD = co.YARD


def _month(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.strftime("%Y-%m")


def _cust(c: SampleCompany) -> pd.DataFrame:
    return c.erp["customers"].set_index("customer_id")


def _net_rows(c: SampleCompany) -> pd.DataFrame:
    """Signed net revenue rows (invoices, credit notes, rebates 4410) with date and customer."""
    inv = c.erp["invoices"]
    cn = c.erp["credit_notes"]
    rb = c.erp["cost_ledger"]
    rb = rb[rb["account_code"] == 4410]
    return pd.concat(
        [
            pd.DataFrame(
                {
                    "day": inv["invoice_date"],
                    "customer_id": inv["customer_id"],
                    "v": inv["net_amount"],
                }
            ),
            pd.DataFrame(
                {"day": cn["cn_date"], "customer_id": cn["customer_id"], "v": -cn["net_amount"]}
            ),
            pd.DataFrame(
                {"day": rb["entry_date"], "customer_id": rb["customer_id"], "v": -rb["amount"]}
            ),
        ],
        ignore_index=True,
    )


def net_revenue_by_region_month(c: SampleCompany) -> pd.DataFrame:
    rows = _net_rows(c)
    rows["region"] = rows["customer_id"].map(_cust(c)["region"])
    rows["month"] = _month(rows["day"])
    return rows.pivot_table(
        index="region", columns="month", values="v", aggfunc="sum", fill_value=0.0
    )


def net_revenue_by_customer_month(c: SampleCompany) -> pd.DataFrame:
    rows = _net_rows(c)
    rows["name"] = rows["customer_id"].map(_cust(c)["name"])
    rows["month"] = _month(rows["day"])
    return rows.pivot_table(
        index="name", columns="month", values="v", aggfunc="sum", fill_value=0.0
    )


def monthly_net_revenue(c: SampleCompany, source: str = "erp") -> pd.Series:
    if source == "erp":
        return net_revenue_by_region_month(c).sum()
    return monthly_invoiced(c, "dw")


def monthly_invoiced(c: SampleCompany, source: str) -> pd.Series:
    if source == "erp":
        inv = c.erp["invoices"]
        return inv.groupby(_month(inv["invoice_date"]))["net_amount"].sum()
    fi = c.dw["sales_dw"]["fact_invoice"]
    return fi.groupby(_month(fi["invoice_date"]))["net_value"].sum()


def daily_invoicing(c: SampleCompany, source: str = "dw") -> pd.Series:
    if source == "dw":
        fi = c.dw["sales_dw"]["fact_invoice"]
        s = fi.groupby(pd.to_datetime(fi["invoice_date"]))["net_value"].sum()
    else:
        inv = c.erp["invoices"]
        s = inv.groupby(pd.to_datetime(inv["invoice_date"]))["net_amount"].sum()
    return s.asfreq("D", fill_value=0.0)


def invoice_dates(c: SampleCompany, name: str, before: D) -> list[D]:
    dim = c.dw["sales_dw"]["dim_customer"]
    key = dim.loc[dim["customer_name"] == name, "customer_key"].iloc[0]
    fi = c.dw["sales_dw"]["fact_invoice"]
    days = sorted(d for d in fi.loc[fi["customer_key"] == key, "invoice_date"] if d < before)
    return days


def last_invoice_dates(c: SampleCompany, before: D) -> dict[str, D]:
    inv = c.erp["invoices"]
    inv = inv[[d < before for d in inv["invoice_date"]]]
    last = inv.groupby("customer_id")["invoice_date"].max()
    names = _cust(c)["name"]
    return {names[k]: v for k, v in last.items()}


def _open_receivables(c: SampleCompany, day: pd.Timestamp) -> pd.DataFrame:
    r = c.erp["receivables"]
    inv_d = pd.to_datetime(r["invoice_date"])
    paid = pd.to_datetime(r["paid_date"])
    open_ = (inv_d <= day) & (paid.isna() | (paid > day))
    out = r[open_].copy()
    out["open_amount"] = np.where(
        out["paid_date"].isna(), out["amount"] - out["paid_amount"], out["amount"]
    )
    return out


def share_over_60(c: SampleCompany, days: Iterable[pd.Timestamp]) -> pd.Series:
    out = {}
    for d in days:
        o = _open_receivables(c, pd.Timestamp(d))
        age = (pd.Timestamp(d) - pd.to_datetime(o["due_date"])).dt.days
        out[pd.Timestamp(d)] = o.loc[age > 60, "open_amount"].sum() / o["open_amount"].sum()
    return pd.Series(out)


def overdue(c: SampleCompany, name: str, run: D) -> float:
    cid = _cust(c).index[_cust(c)["name"] == name][0]
    o = _open_receivables(c, pd.Timestamp(run) - pd.Timedelta(days=1))
    o = o[(o["customer_id"] == cid) & (pd.to_datetime(o["due_date"]) < pd.Timestamp(run))]
    return float(o["open_amount"].sum())


def dso_days(c: SampleCompany, day: D) -> float:
    o = _open_receivables(c, pd.Timestamp(day))
    month = pd.Timestamp(day).strftime("%Y-%m")
    days_in = pd.Timestamp(day).days_in_month
    return float(o["open_amount"].sum() / monthly_net_revenue(c)[month] * days_in)


def order_book(c: SampleCompany, day: pd.Timestamp) -> pd.Series:
    """Open order value by customer at `day`: placed, not yet invoiced, not cancelled."""
    fo = c.dw["sales_dw"]["fact_order"]
    fi = c.dw["sales_dw"]["fact_invoice"].set_index("invoice_no")["invoice_date"]
    od = pd.to_datetime(fo["order_date"])
    inv_d = pd.to_datetime(fo["invoice_no"].map(fi))
    open_ = (od <= day) & (inv_d.isna() | (inv_d > day)) & (fo["status"] != "Cancelled")
    return fo[open_].groupby("customer_key")["amount"].sum()


def top4_order_book_share(c: SampleCompany, days: Iterable[pd.Timestamp]) -> pd.Series:
    out = {}
    for d in days:
        s = order_book(c, pd.Timestamp(d))
        out[pd.Timestamp(d)] = s.nlargest(4).sum() / s.sum()
    return pd.Series(out)


def quiet_active_customers(c: SampleCompany, run: D, days: int) -> pd.DataFrame:
    cust = _cust(c)
    inv = c.erp["invoices"]
    inv = inv[[d < run for d in inv["invoice_date"]]]
    last = inv.groupby("customer_id")["invoice_date"].max()
    lo = D(run.year - 1, run.month, run.day)
    in12 = inv[[lo <= d < run for d in inv["invoice_date"]]]
    l12 = in12.groupby("customer_id")["net_amount"].sum()
    active = cust[cust["status"] == "active"].index
    quiet = [k for k in active if k in last.index and (run - last[k]).days >= days]
    return pd.DataFrame(
        {
            "last_invoice": [last[k] for k in quiet],
            "last_12m": [float(l12.get(k, 0.0)) for k in quiet],
        },
        index=pd.Index([cust.loc[k, "name"] for k in quiet], name="name"),
    )


def _metres(df: pd.DataFrame) -> pd.Series:
    return pd.Series(
        np.where(df["uom"] == "YD", df["dispatch_mtrs"] * YARD, df["dispatch_mtrs"]), index=df.index
    )


def weekly_freight_and_metres(c: SampleCompany) -> pd.DataFrame:
    led = c.erp["cost_ledger"]
    fr = led[led["account_code"] == 5210]
    freight = fr.groupby(pd.to_datetime(fr["entry_date"]))["amount"].sum().resample("W-SAT").sum()
    d = c.dw["sales_dw"]["fact_dispatch"]
    metres = _metres(d).groupby(pd.to_datetime(d["dispatch_date"])).sum().resample("W-SAT").sum()
    w = pd.DataFrame({"freight": freight, "metres": metres}).dropna()
    return w[(w["freight"] > 0) & (w["metres"] > 0)]


def daily_metres(c: SampleCompany, plant: str) -> pd.Series:
    d = c.dw["sales_dw"]["fact_dispatch"]
    d = d[d["plant"] == plant]
    s = _metres(d).groupby(pd.to_datetime(d["dispatch_date"])).sum()
    s = s.asfreq("D", fill_value=0.0)
    return s[s.index.day_of_week != 6]


def dispatch_days(c: SampleCompany, plant: str) -> pd.Series:
    d = c.dw["sales_dw"]["fact_dispatch"]
    d = d[d["plant"] == plant]
    days = (
        pd.to_datetime(d["dispatched_at"]) - pd.to_datetime(d["ready_at"])
    ).dt.total_seconds() / 86400
    return pd.Series(
        days.to_numpy(), index=pd.to_datetime(d["dispatched_at"]).to_numpy()
    ).sort_index()


def mis_sales_by_month(c: SampleCompany) -> pd.Series:
    sheet = next(s for s in c.sheets.values() if s["properties"]["title"] == "MIS-Weekly")
    tab = next(t for t in sheet["sheets"] if t["properties"]["title"] == "Sales")
    rows = tab["values"][1:]
    df = pd.DataFrame(rows, columns=tab["values"][0])
    df["month"] = pd.to_datetime(df["Month"], format="%b %Y").dt.strftime("%Y-%m")
    return df.groupby("month")["Sales incl. GST (₹ L)"].apply(lambda s: s.astype(float).sum() * 1e5)


def dispatched_without_invoice(c: SampleCompany, run: D, days: int) -> pd.DataFrame:
    d = c.dw["sales_dw"]["fact_dispatch"]
    cutoff = run - dt.timedelta(days=days)
    erp_inv = c.erp["invoices"]
    erp_nos = set(erp_inv.loc[[x < run for x in erp_inv["invoice_date"]], "invoice_no"])
    sent = d[[co.ERP_START <= x <= cutoff for x in d["dispatch_date"]]]  # erp_db era only
    fo = c.dw["sales_dw"]["fact_order"].set_index("order_no")
    orders = sent["order_no"].dropna().unique()
    out = fo.loc[orders]
    linked = out["invoice_no"].where(out["invoice_no"].notna(), None)
    missing = [
        o for o, inv in zip(out.index, linked, strict=True) if inv is None or inv not in erp_nos
    ]
    return fo.loc[missing, ["amount", "status"]]


def order_value_modes(c: SampleCompany) -> tuple[float, float]:
    """The two modes of the order value distribution (last 12 months), on a log scale."""
    from scipy import stats  # type: ignore[import-untyped]

    fo = c.dw["sales_dw"]["fact_order"]
    fo = fo[[D(2025, 11, 1) <= d < D(2026, 11, 1) for d in fo["order_date"]]]
    x = np.log10(fo["amount"].astype(float).to_numpy())
    grid = np.linspace(x.min(), x.max(), 600)
    dens = stats.gaussian_kde(x, bw_method=0.12)(grid)
    peaks = [i for i in range(1, len(grid) - 1) if dens[i] > dens[i - 1] and dens[i] >= dens[i + 1]]
    top = sorted(sorted(peaks, key=lambda i: -dens[i])[:2])
    lo, hi = (10 ** grid[i] for i in top)
    return float(lo), float(hi)


def lagged_deals_vs_invoicing(c: SampleCompany, lags: Iterable[int]) -> pd.Series:
    from scipy import stats  # type: ignore[import-untyped]

    sheet = next(s for s in c.sheets.values() if s["properties"]["title"] == "Order tracker FY27")
    tab = next(t for t in sheet["sheets"] if t["properties"]["title"] == "Deals")
    df = pd.DataFrame(tab["values"][1:], columns=tab["values"][0])
    won = df[df["Outcome"] == "Won"]
    wv = (
        won.groupby(pd.to_datetime(won["Closed"]))["Value (₹ L)"]
        .apply(lambda s: s.astype(float).sum())
        .resample("W-SAT")
        .sum()
    )
    inv = daily_invoicing(c, "dw").resample("W-SAT").sum()
    x = inv / inv.rolling(13, center=True, min_periods=7).median()
    out = {}
    for lag in lags:
        pair = pd.DataFrame({"won": wv, "inv": x.shift(-lag)}).dropna()
        out[lag] = stats.spearmanr(pair["won"], pair["inv"]).statistic
    return pd.Series(out)


def gross_margin_per_metre(c: SampleCompany, month: str) -> float:
    rev = monthly_net_revenue(c)[month]
    led = c.erp["cost_ledger"]
    cogs = led[led["account_code"].isin([4100, 4200, 4300]) & (_month(led["entry_date"]) == month)]
    d = c.dw["sales_dw"]["fact_dispatch"]
    metres = _metres(d)[_month(d["dispatch_date"]) == month].sum()
    return float((rev - cogs["amount"].sum()) / metres)


def cash_balance(c: SampleCompany, day: D) -> float:
    b = c.erp["bank_ledger"]
    b = b[[d <= day for d in b["entry_date"]]]
    return float(b.groupby("bank")["balance"].last().sum())


def credit_note_share(c: SampleCompany) -> float:
    inv, cn = c.erp["invoices"], c.erp["credit_notes"]
    lo, hi = D(2025, 11, 1), D(2026, 11, 1)
    g = inv.loc[[lo <= d < hi for d in inv["invoice_date"]], "net_amount"].sum()
    return float(cn.loc[[lo <= d < hi for d in cn["cn_date"]], "net_amount"].sum() / g)


def top10_revenue_share(c: SampleCompany) -> float:
    inv = c.erp["invoices"]
    inv = inv[[D(2025, 11, 1) <= d < D(2026, 11, 1) for d in inv["invoice_date"]]]
    s = inv.groupby("customer_id")["net_amount"].sum()
    return float(s.nlargest(10).sum() / s.sum())


def known_pattern_weeks(index: pd.DatetimeIndex) -> pd.Series:
    """True for weeks touched by a known pattern (quarter-end push, Diwali, Surat shutdown).

    These are suppressed before detection (docs/04 §4), so clean-series checks skip them too.
    """
    import json
    from pathlib import Path

    e = json.loads((Path(__file__).parent / "expected.json").read_text())["P14"]
    out = []
    for w in index:
        days = pd.date_range(w - pd.Timedelta(days=6), w)
        qe = any(d.month in (3, 6, 9, 12) and d.days_in_month - d.day < 5 for d in days)
        diwali = any(
            pd.Timestamp(s) <= d < pd.Timestamp(s) + pd.Timedelta(days=7)
            for s in e["diwali_week_start"].values()
            for d in days
        )
        shut = any((8, 10) <= (d.month, d.day) <= (8, 24) for d in days)
        out.append(qe or diwali or shut)
    return pd.Series(out, index=index)


def clean_series(c: SampleCompany, n: int) -> pd.Series:
    """C1–C10: weekly series of similar size and noise with nothing planted (to 24 Oct 2026)."""
    fi = c.dw["sales_dw"]["fact_invoice"]
    fi = fi.assign(day=pd.to_datetime(fi["invoice_date"]))
    dim = c.dw["sales_dw"]["dim_customer"].set_index("customer_key")
    seg = fi["customer_key"].map(dim["segment"])
    disp = c.dw["sales_dw"]["fact_dispatch"]
    disp = disp.assign(day=pd.to_datetime(disp["dispatch_date"]), m=_metres(disp))

    def wk(s: pd.Series) -> pd.Series:
        return s.resample("W-SAT").sum()

    if n in (1, 2, 3):
        region = {1: "North", 2: "East", 3: "Export"}[n]
        s = wk(fi[fi["region"] == region].groupby("day")["net_value"].sum())
    elif n == 4:  # dispatch lines at Coimbatore (P7 is about their timing, not their count)
        s = wk(disp[disp["plant"] == "Coimbatore"].groupby("day")["m"].count().astype(float))
    elif n == 5:
        s = wk(fi[seg == "small"].groupby("day")["net_value"].count().astype(float))
    elif n == 6:
        t = dispatch_days(c, "Surat")
        s = t.resample("W-SAT").mean()
    elif n == 7:
        b = c.erp["bank_ledger"]
        b = b[b["description"].str.startswith("Receipt")]
        s = wk(b.groupby(pd.to_datetime(b["entry_date"]))["credit"].sum())
    elif n == 8:  # discount approval requests (P8 is about where amounts sit, not how many)
        ap = c.erp["approvals"]
        s = wk(ap.groupby(pd.to_datetime(ap["request_date"]))["approval_id"].count().astype(float))
    elif n == 9:
        cn = c.erp["credit_notes"]
        s = wk(cn.groupby(pd.to_datetime(cn["cn_date"]))["net_amount"].sum())
    else:  # EUR invoices per week
        s = wk(fi[fi["currency"] == "EUR"].groupby("day")["net_value"].count().astype(float))
    s = s[(s.index >= pd.Timestamp("2025-04-05")) & (s.index <= pd.Timestamp("2026-10-24"))]
    s = s[~known_pattern_weeks(s.index)]
    s.name = f"C{n}"
    return s.dropna()
