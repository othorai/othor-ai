# SPDX-License-Identifier: Apache-2.0
"""The sample company's next weeks (docs/plans/sample-demo-events.md, owner 20 Nov 2026).

After October nothing new happened to Indigo Thread: every planted pattern sits in Aug–Oct and
nobody in an invented company fixes anything, so every brief from mid-November read "Still
open". These events give the nights from 23 Nov something to find: new trouble, old trouble
cleared by the data, something worse, a calm week. The horizon also runs on to 31 Mar 2027.

Everything here is applied to the finished timeline (after calibration and every derived flow),
and only to rows dated on or after SEAM (Mon 23 Nov 2026). The data as of any date up to 22 Nov
is byte for byte what it was before (tests/unit/test_sample_events.py holds the hashes): no
random stream of the base timeline is drawn again, and rows after the seam are renumbered after
the seam's last number. Each event's own randomness comes from its own stream.

Dates are data dates: an invoice dated D is first in the sources as of D + 1, so the run of that
night (and the 06:00 brief) is the first to see it.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

from tests.fixtures.sample_company import build as b
from tests.fixtures.sample_company import company as co

D = dt.date
SEAM = b.d64("2026-11-23")
END = b.d64(co.END)  # the base timeline's horizon
EXT_END = D(2027, 3, 31)  # the horizon after the events (owner, 20 Nov 2026: Q1)
SHIFT = 364  # Jan–Mar 2027 follow Jan–Mar 2026, 52 weeks on (same weekdays)
GROWTH = 1.04  # FY27 over FY26 (FY_TOTAL 2026 / 2025, rounded)
FREIGHT_LEVEL = 1.19  # freight per metre since the P6 break (+19%), kept in 2027

# The events (date, what). The plan's table names them E1–E10 and J1–J6.
E1_RAVIRA_BACK = D(2026, 11, 24)  # Ravira Exports invoices again (its old overdue: E6, J1)
E2_MERAI_STOPS = D(2026, 11, 27)  # Merai Apparels places no orders from this day
E3_CLEANUP = D(2026, 12, 1)  # six of the silent "active" customers marked inactive
E3_INACTIVE = (
    "Shree Varada Textiles",
    "Tidewell Garments",
    "Anvika Knit Works",
    "Sanvi Weaves",
    "Kavira Knitwear",
    "Navika Fashions",
)
E5_COST_STEP = D(2026, 12, 4)  # dye and chemicals price rise: raw material +30% (COGS ~+19%)
E5_RAW_MATERIAL = 1.30
E6_DUE = (D(2026, 9, 25), D(2026, 10, 20))  # late payers' invoices stay unpaid past 60 days
E7_SPREAD_BACK = D(2026, 12, 11)  # Coimbatore dispatch times settle back
E9_NEW_CUSTOMER = D(2026, 12, 21)  # a new export customer's week of orders
E9_ADDED = D(2026, 12, 18)  # its customer record is created
J1_PAID = D(2027, 1, 4)  # E6's two invoices paid
J2_MERAI_BACK = D(2027, 2, 1)  # Merai orders again (moved from 8 Jan)
J4_SURAT_STOP = (D(2027, 1, 19), D(2027, 1, 24))  # Surat dispatch stops (boiler repair)
J5_TESSORA_BACK = D(2027, 1, 27)  # Tessora EU orders again
J6_FREIGHT_STEP = D(2027, 2, 1)  # freight rates +10% (fuel)
J6_FREIGHT = 1.10

NEW_CUSTOMER = co.Customer(
    "C0144",
    "Nordvik Tekstil",
    "Export",
    "EUR",
    "mid",
    "cadence",
    3_600_000,
    first=E9_NEW_CUSTOMER,
    city="Bergen",
    terms=45,
    tags={"new"},
)


def d64(x: Any) -> np.datetime64:
    return b.d64(x)


def days(a: Any) -> np.ndarray:
    return np.asarray(a).astype("datetime64[D]")


@dataclasses.dataclass
class Frames:
    inv: pd.DataFrame
    lines: pd.DataFrame
    orders: pd.DataFrame
    dispatch: pd.DataFrame
    rec: pd.DataFrame
    ledger: pd.DataFrame
    approvals: pd.DataFrame
    fx: pd.Series


# ---------------------------------------------------------------------------------------------
# The whole set


def apply(t: Any, seed: int) -> Any:
    """The timeline with the events and the horizon to 31 Mar 2027 (a new Timeline)."""
    by = {c.code: c for c in t.roster}
    code = {c.name: c.code for c in t.roster}
    f = Frames(
        t.inv.copy(),
        t.lines.copy(),
        t.book.orders.copy(),
        t.dispatch.copy(),
        t.rec.copy(),
        t.ledger.copy(),
        t.approvals.copy(),
        extend_fx(t.fx, seed),
    )
    allc = {**by, NEW_CUSTOMER.code: NEW_CUSTOMER}
    tmp = _Ids()

    # E1: Ravira Exports invoices again from 24 Nov, about weekly; its held invoices stay unpaid.
    ravira = code["Ravira Exports"]
    r = co.stream(seed, "events", "E1")
    when = [E1_RAVIRA_BACK, E1_RAVIRA_BACK + dt.timedelta(days=2)]
    while True:
        nxt = when[-1] + dt.timedelta(days=int(r.integers(5, 9)))
        if nxt > co.END:
            break
        when.append(nxt if nxt.weekday() != 6 else nxt + dt.timedelta(days=1))
    tmpl = _recent(f.inv, ravira, before=D(2026, 10, 10), n=len(when))
    _add(f, allc, tmp, tmpl, when, paid="on_time", seed=seed, key="E1")
    held = (f.rec["cust"] == ravira).to_numpy() & (f.rec["paid_true"].to_numpy() >= b.INF)
    held &= days(f.rec["date"]) < SEAM
    f.rec.loc[held, "paid_true"] = d64(J1_PAID)  # E6: the old overdue is cleared only on J1

    # E2: Merai Apparels places no orders from 27 Nov (orders already placed still invoice).
    merai = code["Merai Apparels"]
    _drop_orders(
        f, (f.orders["cust"] == merai) & (days(f.orders["order_date"]) >= d64(E2_MERAI_STOPS))
    )

    # E6: Ravira's old overdue (above) and the late payers' invoices due 25 Sep – 20 Oct still
    # unpaid on 23 Nov stay unpaid: they pass 60 days between 24 Nov and early Jan (receivables
    # over 60 days step up) and are paid on J1. (A key account's invoice can't be used: key
    # accounts pay on time, so one unpaid on 23 Nov would change the data before the seam.)
    due = days(f.rec["due"])
    paid_at = f.rec["paid_true"].to_numpy().astype("datetime64[D]")
    late = (
        (due >= d64(E6_DUE[0]))
        & (due <= d64(E6_DUE[1]))
        & (paid_at >= SEAM)
        & (paid_at < b.INF)
        & (days(f.rec["date"]) < SEAM)
    )
    f.rec.loc[late, "paid_true"] = d64(J1_PAID)

    # E7: Coimbatore dispatch times back to their old spread from 11 Dec (P7 settles).
    at = f.dispatch["dispatched_at"].to_numpy()
    sel = (
        (f.dispatch["plant"] == "Coimbatore").to_numpy()
        & (at >= d64(E7_SPREAD_BACK).astype("datetime64[s]"))
        & (at <= END.astype("datetime64[s]") + np.timedelta64(86399, "s"))
    )
    u = np.interp(f.dispatch.loc[sel, "dispatch_days"], b.Q_AFTER[1], b.Q_AFTER[0])
    new = np.interp(u, b.Q_BEFORE[0], b.Q_BEFORE[1])
    f.dispatch.loc[sel, "dispatch_days"] = new
    f.dispatch.loc[sel, "ready_at"] = f.dispatch.loc[sel, "dispatched_at"] - pd.to_timedelta(
        np.round(new * 86400), unit="s"
    )

    # E9: a new export customer's week of orders (open until they invoice in late Jan–Feb).
    r = co.stream(seed, "events", "E9")
    exp = [c.code for c in t.roster if c.region == "Export" and c.currency == "EUR"]
    pool = f.inv[f.inv["cust"].isin(exp) & (days(f.inv["date"]) >= d64("2026-06-01"))]
    pool = pool[days(pool["date"]) < SEAM]
    order_days = [E9_NEW_CUSTOMER + dt.timedelta(days=k) for k in (0, 1, 2, 3, 5)]
    inv_days = [
        d + dt.timedelta(days=int(x))
        for d, x in zip(order_days, r.integers(35, 52, 5), strict=True)
    ]
    inv_days = [d if d.weekday() != 6 else d + dt.timedelta(days=1) for d in inv_days]
    picks = pool.iloc[r.choice(len(pool), 5, replace=False)]
    target = r.uniform(3_200_000, 4_400_000, 5)
    _add(
        f,
        allc,
        tmp,
        picks,
        inv_days,
        paid="on_time",
        seed=seed,
        key="E9",
        cust=NEW_CUSTOMER.code,
        amounts=target,
        order_days=order_days,
    )

    # The horizon to 31 Mar 2027: Jan–Mar 2026 again, 52 weeks on, for the customers still
    # trading (Merai from J2, Tessora from J5; the silent and the inactive stay silent).
    active = _trading(f, t.roster)
    starts = {c: D(2027, 1, 1) for c in active}
    starts[merai] = J2_MERAI_BACK
    starts[code["Tessora EU"]] = J5_TESSORA_BACK
    _extend(f, allc, tmp, starts, seed)

    # E5 and J6: cost steps (raw material from 4 Dec; freight from 1 Feb).
    week = days(f.ledger["entry_date"])
    sel = (f.ledger["account_code"] == 4100).to_numpy() & (week >= d64(E5_COST_STEP))
    f.ledger.loc[sel, "amount"] = np.round(f.ledger.loc[sel, "amount"] * E5_RAW_MATERIAL, 2)
    sel = (f.ledger["account_code"] == 5210).to_numpy() & (week >= d64(J6_FREIGHT_STEP))
    f.ledger.loc[sel, "amount"] = np.round(f.ledger.loc[sel, "amount"] * J6_FREIGHT, 2)

    # J4: Surat dispatch stops 19–24 Jan and catches up 25–27 Jan.
    at = f.dispatch["dispatched_at"].to_numpy()
    lo, hi = (d64(x).astype("datetime64[s]") for x in J4_SURAT_STOP)
    sel = (
        (f.dispatch["plant"] == "Surat").to_numpy()
        & (at >= lo)
        & (at < hi + np.timedelta64(1, "D"))
    )
    k = np.arange(sel.sum()) % 3
    moved = (d64(J4_SURAT_STOP[1]) + (1 + k) * b.DAY).astype("datetime64[s]") + (
        at[sel] - days(at[sel]).astype("datetime64[s]")
    )
    f.dispatch.loc[sel, "dispatched_at"] = moved
    f.dispatch.loc[sel, "dispatch_days"] = (
        (moved - f.dispatch.loc[sel, "ready_at"].to_numpy()) / np.timedelta64(1, "s") / 86400
    )

    _rebates(f)
    out = _renumber(f, t)
    bank = _bank(t.bank, out.rec, seed)
    return dataclasses.replace(
        t,
        inv=out.inv,
        lines=out.lines,
        book=b.Book(out.orders, t.book.k_lead),
        dispatch=out.dispatch,
        rec=out.rec,
        ledger=out.ledger,
        bank=bank,
        approvals=out.approvals,
        fx=out.fx,
        added=[NEW_CUSTOMER],
    )


# ---------------------------------------------------------------------------------------------
# What the renderers ask


def roster_as_of(t: Any, cut: np.datetime64) -> list[co.Customer]:
    """The customer records as of `cut`: the base roster, plus a customer added after the seam
    once its record exists, with E3's status change from its date."""
    added = [c for c in getattr(t, "added", []) if d64(E9_ADDED) < cut]
    out = []
    for c in [*t.roster, *added]:
        if c.name in E3_INACTIVE and d64(E3_CLEANUP) < cut:
            c = dataclasses.replace(c, status="inactive")
        out.append(c)
    return out


# ---------------------------------------------------------------------------------------------
# Building blocks


class _Ids:
    """Temporary keys for new rows; _renumber gives them their real ones."""

    def __init__(self) -> None:
        self.n = 0

    def take(self) -> int:
        self.n += 1
        return self.n


def _recent(inv: pd.DataFrame, cust: str, before: D, n: int) -> pd.DataFrame:
    rows = inv[(inv["cust"] == cust) & (days(inv["date"]) < d64(before))]
    rows = rows.tail(max(n, 1))
    return rows.iloc[np.arange(n) % len(rows)]


def _sun(d: np.datetime64) -> np.datetime64:
    return d + b.DAY if b.is_sunday(np.array([d]))[0] else d


def _add(
    f: Frames,
    allc: dict[str, co.Customer],
    tmp: _Ids,
    templates: pd.DataFrame,
    dates: list[D],
    *,
    paid: str,
    seed: int,
    key: str,
    cust: str | None = None,
    amounts: np.ndarray | None = None,
    scale: float = 1.0,
    order_days: list[D] | None = None,
    order_floor: D | None = None,
    seam: np.datetime64 = SEAM,
) -> None:
    """New invoices copied from template invoices (their lines, order, dispatch, receivable),
    dated `dates`, every date of the copy on or after the seam."""
    r = co.stream(seed, "events", key, "pay")
    floor = d64(order_floor) if order_floor else seam
    o_by_inv = f.orders.dropna(subset=["invoice_no"]).set_index("invoice_no")
    l_by_inv = {k: g for k, g in f.lines.groupby("invoice_no", sort=False)}
    d_by_inv = {k: g for k, g in f.dispatch.groupby("invoice_no", sort=False)}
    rec_by_id = f.rec.set_index("invoice_id")
    new_inv, new_lines, new_orders, new_disp, new_rec = [], [], [], [], []
    for i, (row, day) in enumerate(zip(templates.itertuples(index=False), dates, strict=True)):
        c = allc[cust or row.cust]
        nd = d64(day)
        delta = nd - d64(row.date)
        k = tmp.take()
        no, oid = f"TMP/{k:07d}", -k
        amt = float(amounts[i]) if amounts is not None else float(row.amt) * scale
        ratio = amt / float(row.amt)
        x = row._asdict()
        x.update(
            cust=c.code,
            date=pd.Timestamp(nd),
            amt=round(amt, 2),
            pin="",
            region=c.region,
            currency=c.currency,
            segment=c.segment,
            month=str(nd.astype("datetime64[M]")),
            erp=True,
            ktn="ktn" in c.tags,
            top4="top4" in c.tags,
            cn_date=pd.Timestamp(d64(row.cn_date) + delta),
            cn_posted=pd.Timestamp(d64(row.cn_posted) + delta),
            invoice_no=no,
            invoice_id=oid,
        )
        x["cn_month"] = str(d64(x["cn_date"]).astype("datetime64[M]"))
        if c.currency == "EUR":
            rate = float(f.fx.loc[pd.Timestamp(nd)])
            fc = round(amt / rate, 2)
            x.update(fx_rate=rate, amount_fc=fc, net=round(fc * rate, 2), gst=0.0)
        else:
            x.update(fx_rate=1.0, amount_fc=np.nan, net=round(amt, 2))
            x["gst"] = round(x["net"] * 0.05, 2)
        x["gross"] = round(x["net"] + x["gst"], 2)
        x["rebate"] = 0.006 if (c.segment in ("key", "mid") and c.currency == "INR") else 0.0
        new_inv.append(x)
        # lines: the template's, scaled to the new net
        ln = l_by_inv[row.invoice_no].copy()
        ln["net"] = np.round(ln["net"].to_numpy() * x["net"] / ln["net"].sum(), 2)
        ln.iloc[-1, ln.columns.get_loc("net")] = round(x["net"] - ln["net"].iloc[:-1].sum(), 2)
        ln["qty_m"] = np.round(ln["net"] / ln["rate"], 1)
        ln["invoice_id"], ln["invoice_no"], ln["date"] = oid, no, pd.Timestamp(nd)
        new_lines.append(ln)
        # the order
        okey = f"TMPO/{k:07d}"
        if row.invoice_no in o_by_inv.index:
            o = o_by_inv.loc[row.invoice_no]
            od = d64(order_days[i]) if order_days else d64(o["order_date"]) + delta
            od = max(od, floor)
            new_orders.append(
                {
                    "cust": c.code,
                    "order_date": pd.Timestamp(od),
                    "close": pd.Timestamp(nd),
                    "amount": round(float(o["amount"]) * ratio, 2),
                    "invoice_no": no,
                    "kind": "invoiced",
                    "fixed_status": None,
                    "order_no": okey,
                    "region": c.region,
                }
            )
        else:
            od = nd
        # dispatch: the template's, moved with the invoice, never before the order or the seam
        if row.invoice_no in d_by_inv:
            dd = d_by_inv[row.invoice_no].copy()
            at = dd["dispatched_at"].to_numpy() + delta.astype("timedelta64[s]")
            lo = max(od, floor).astype("datetime64[s]") + np.timedelta64(10 * 3600, "s")
            at = np.maximum(at, lo)
            dd["dispatched_at"] = at
            dd["day"] = days(at)
            dd["ready_at"] = at - np.round(dd["dispatch_days"].to_numpy() * 86400).astype(
                "timedelta64[s]"
            )
            dd["qty_m"] = np.round(dd["qty_m"].to_numpy() * ratio, 1)
            dd["dispatch_mtrs"] = np.round(dd["dispatch_mtrs"].to_numpy() * ratio, 1)
            dd["invoice_no"], dd["order_no"] = no, okey
            new_disp.append(dd)
        # the receivable
        rr = rec_by_id.loc[row.invoice_id] if row.invoice_id in rec_by_id.index else None
        rec = dict(x)
        rec["due"] = pd.Timestamp(nd + c.terms * b.DAY)
        late = int(np.round(r.normal(4.0, 4.0)))
        if paid == "copy" and rr is not None and d64(rr["paid_true"]) < b.INF:
            pdue = d64(rr["paid_true"]) + delta
        else:
            pdue = d64(rec["due"]) + late * b.DAY
        pdue = _sun(max(pdue, nd + b.DAY))
        rec.update(paid_true=pdue, partial_paid=0.0)
        new_rec.append(rec)
    f.inv = pd.concat([f.inv, pd.DataFrame(new_inv)], ignore_index=True)
    f.lines = pd.concat([f.lines, *new_lines], ignore_index=True)
    if new_orders:
        f.orders = pd.concat([f.orders, pd.DataFrame(new_orders)], ignore_index=True)
    if new_disp:
        f.dispatch = pd.concat([f.dispatch, *new_disp], ignore_index=True)
    f.rec = pd.concat([f.rec, pd.DataFrame(new_rec)], ignore_index=True)


def _drop_orders(f: Frames, sel: pd.Series, seam: np.datetime64 = SEAM) -> None:
    """Orders that are never placed, with everything that followed them."""
    gone = f.orders[sel.to_numpy()]
    if (days(gone["order_date"]) < seam).any():
        raise ValueError("an event may only drop orders placed on or after the seam")
    nos = set(gone["invoice_no"].dropna())
    f.orders = f.orders[~sel.to_numpy()]
    ids = set(f.inv.loc[f.inv["invoice_no"].isin(nos), "invoice_id"])
    f.inv = f.inv[~f.inv["invoice_no"].isin(nos)]
    f.lines = f.lines[~f.lines["invoice_no"].isin(nos)]
    f.dispatch = f.dispatch[
        ~(f.dispatch["invoice_no"].isin(nos) | f.dispatch["order_no"].isin(set(gone["order_no"])))
    ]
    f.rec = f.rec[~f.rec["invoice_id"].isin(ids)]


def _trading(f: Frames, roster: list[co.Customer]) -> list[str]:
    """Customers with an invoice in Oct–Dec 2026 (after the events) or a resume date."""
    d = days(f.inv["date"])
    recent = set(f.inv.loc[(d >= d64("2026-10-01")) & (d <= END), "cust"])
    out = []
    for c in roster:
        if c.name in E3_INACTIVE or "quiet" in c.tags or c.status != "active":
            continue
        if c.code in recent or c.resume:
            out.append(c.code)
    return out


def _extend(
    f: Frames, allc: dict[str, co.Customer], tmp: _Ids, starts: dict[str, D], seed: int
) -> None:
    """Jan–Mar 2027 from Jan–Mar 2026, 52 weeks on, at FY27's level; each customer from its
    start date. Ledgers and approvals follow the same way."""
    d = days(f.inv["date"])
    src = f.inv[(d >= d64("2026-01-01")) & (d <= d64("2026-03-31"))]
    src = src[src["pin"] == ""]
    nd = days(src["date"]) + SHIFT * b.DAY
    start = src["cust"].map(lambda c: d64(starts[c]) if c in starts else b.INF).to_numpy()
    keep = (nd >= start.astype("datetime64[D]")) & (nd <= d64(EXT_END))
    src, nd = src[keep], nd[keep]
    # each month at the plan's level (as build.calibrate holds every month to the plan): the
    # customers who come back (Merai, Tessora) at FY27's growth, the new customer's invoices as
    # they are, the rest scaled to fill the month
    month = nd.astype("datetime64[M]").astype(str)
    late = np.array([starts[c] > D(2027, 1, 1) for c in src["cust"]])
    amt = src["amt"].to_numpy() * GROWTH
    dd = days(f.inv["date"])
    scale = np.ones(len(src))
    for m in sorted(set(month)):
        inm = month == m
        lo = np.datetime64(m, "D")
        hi = (np.datetime64(m, "M") + np.timedelta64(1, "M")).astype("datetime64[D]")
        already = f.inv.loc[(dd >= lo) & (dd < hi), "amt"].sum()  # E9's invoices
        rest = b.plan_total(m) - already - amt[inm & late].sum()
        scale[inm & ~late] = rest / amt[inm & ~late].sum()
    amounts = np.round(amt * scale, 2)
    floors = [starts[c] for c in src["cust"]]
    for fl in sorted(set(floors)):
        m = np.array([x == fl for x in floors])
        _add(
            f,
            allc,
            tmp,
            src[m],
            [x.astype(object) for x in nd[m]],
            paid="copy",
            seed=seed,
            key=f"ext-{fl}",
            amounts=amounts[m],
            order_floor=max(fl, D(2026, 12, 1)) if fl > D(2027, 1, 1) else None,
        )
    # ledgers: weekly costs and freight 52 weeks on; monthly admin at each month's end
    lg = f.ledger
    w = days(lg["entry_date"])
    weekly = lg["account_code"].isin([4100, 4200, 4300, 5210]).to_numpy()
    src = lg[weekly & (w >= d64("2026-01-10")) & (w <= d64("2026-03-31") + 7 * b.DAY)].copy()
    src["entry_date"] = pd.to_datetime(days(src["entry_date"]) + SHIFT * b.DAY)
    src = src[days(src["entry_date"]) <= d64(EXT_END) + 6 * b.DAY]
    src["amount"] = np.round(src["amount"] * GROWTH, 2)
    fr = src["account_code"] == 5210
    src.loc[fr, "amount"] = np.round(src.loc[fr, "amount"] * FREIGHT_LEVEL, 2)
    src["narration"] = [
        f"{'Freight week' if a == 5210 else 'Week'} to {pd.Timestamp(e).date()}"
        for a, e in zip(src["account_code"], src["entry_date"], strict=True)
    ]
    r = co.stream(seed, "events", "admin")
    admin = []
    for mth in pd.period_range("2027-01", "2027-03", freq="M"):
        admin.append(
            {
                "entry_date": pd.Timestamp(mth.end_time.date()),
                "account_code": 5300,
                "account_name": "Administrative expenses",
                "plant": None,
                "cust": None,
                "amount": round(float(r.uniform(9e5, 1.2e6)) * GROWTH, 2),
                "narration": "Monthly admin",
            }
        )
    f.ledger = pd.concat([lg, src, pd.DataFrame(admin)], ignore_index=True)
    ap = f.approvals
    rd = days(ap["request_date"])
    src = ap[(rd >= d64("2026-01-01")) & (rd <= d64("2026-03-31"))].copy()
    src["request_date"] = days(src["request_date"]) + SHIFT * b.DAY
    src["decided_at"] = days(src["decided_at"]) + SHIFT * b.DAY
    src = src[days(src["request_date"]) <= d64(EXT_END)]
    f.approvals = pd.concat([ap, src], ignore_index=True)


def extend_fx(fx: pd.Series, seed: int) -> pd.Series:
    idx = pd.date_range(pd.Timestamp(co.END) + pd.Timedelta(days=1), pd.Timestamp(EXT_END))
    r = co.stream(seed, "events", "fx")
    steps = r.normal(0.004, 0.17, len(idx))
    tail = pd.Series(np.round(float(fx.iloc[-1]) + np.cumsum(steps), 4), index=idx)
    return pd.concat([fx, tail])


def _rebates(f: Frames) -> None:
    """Rebates 4410 posted at each month's end from November (they follow the invoices)."""
    lg = f.ledger
    end = days(lg["entry_date"])
    lg = lg[~((lg["account_code"] == 4410).to_numpy() & (end >= SEAM))]
    rb = f.inv[(f.inv["rebate"] > 0) & (days(f.inv["date"]) >= d64("2026-11-01"))].copy()
    rb["end"] = (days(rb["date"]).astype("datetime64[M]") + np.timedelta64(1, "M")).astype(
        "datetime64[D]"
    ) - b.DAY
    v = rb.assign(v=rb["net"] * rb["rebate"]).groupby(["end", "cust"])["v"].sum()
    rows = [
        {
            "entry_date": pd.Timestamp(e),
            "account_code": 4410,
            "account_name": "Rebates and discounts",
            "plant": None,
            "cust": c,
            "amount": round(float(x), 2),
            "narration": "Volume rebate",
        }
        for (e, c), x in v.items()
    ]
    f.ledger = pd.concat([lg, pd.DataFrame(rows)], ignore_index=True)


def _renumber(f: Frames, t: Any, seam: np.datetime64 = SEAM) -> Frames:
    """Rows on or after the seam take the numbers after the seam's last ones, in date order, as
    the base generator numbers them; rows before the seam keep theirs (and their order)."""
    # invoices
    inv = f.inv
    pre = days(inv["date"]) < seam
    a, z = inv[pre], inv[~pre].sort_values(["date", "cust", "amt"], kind="stable")
    fy = np.array([b.fiscal_year(x) for x in days(z["date"])])
    start = {
        y: int((np.array([b.fiscal_year(x) for x in days(a["date"])]) == y).sum()) for y in set(fy)
    }
    seq = pd.Series(1, index=z.index).groupby(fy).cumsum().to_numpy()
    nos = [
        f"BI/{y % 100:02d}{(y + 1) % 100:02d}/{s + start[y]:06d}"
        for y, s in zip(fy, seq, strict=True)
    ]
    no_map = dict(zip(z["invoice_no"], nos, strict=True))
    id_map = dict(zip(z["invoice_id"], range(len(a) + 1, len(a) + len(z) + 1), strict=True))
    z = z.assign(invoice_no=nos, invoice_id=np.arange(len(a) + 1, len(a) + len(z) + 1))
    inv = pd.concat([a, z], ignore_index=True)

    def remap(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if "invoice_no" in df:
            df["invoice_no"] = df["invoice_no"].map(lambda v: no_map.get(v, v))
        if "invoice_id" in df:
            df["invoice_id"] = df["invoice_id"].map(lambda v: id_map.get(v, v)).astype("int64")
        return df

    # lines and receivables follow their invoices
    lines = remap(f.lines)
    pre = days(lines["date"]) < seam
    lines = pd.concat(
        [lines[pre], lines[~pre].sort_values(["invoice_id", "line_no"], kind="stable")],
        ignore_index=True,
    )
    rec = remap(f.rec)
    pre = days(rec["date"]) < seam
    rec = pd.concat(
        [rec[pre], rec[~pre].sort_values("invoice_id", kind="stable")], ignore_index=True
    )
    # orders
    o = remap(f.orders)
    pre = days(o["order_date"]) < seam
    a, z = o[pre], o[~pre].sort_values(["order_date", "cust", "amount"], kind="stable")
    yy = z["order_date"].astype("datetime64[s]").dt.year % 100
    onos = [
        f"SO-{y:02d}{i + 1:06d}" for i, y in zip(range(len(a), len(a) + len(z)), yy, strict=True)
    ]
    o_map = dict(zip(z["order_no"], onos, strict=True))
    z = z.assign(order_no=onos)
    orders = pd.concat([a, z], ignore_index=True)
    # dispatch
    dsp = remap(f.dispatch)
    dsp["order_no"] = dsp["order_no"].map(lambda v: o_map.get(v, v))
    pre = dsp["dispatched_at"].to_numpy() < seam.astype("datetime64[s]")
    a, z = dsp[pre], dsp[~pre].sort_values(["dispatched_at", "order_no"], kind="stable")
    z = z.assign(dispatch_key=np.arange(len(a) + 1, len(a) + len(z) + 1))
    dsp = pd.concat([a, z], ignore_index=True)
    # ledger
    lg = f.ledger
    pre = days(lg["entry_date"]) < seam
    a = lg[pre]
    z = lg[~pre].sort_values(
        ["entry_date", "account_code", "plant"], kind="stable", na_position="last"
    )
    z = z.assign(entry_id=np.arange(len(a) + 1, len(a) + len(z) + 1))
    ledger = pd.concat([a, z], ignore_index=True)
    ledger["entry_date"] = pd.to_datetime(ledger["entry_date"])
    ledger["entry_id"] = ledger["entry_id"].astype("int64")
    # approvals
    ap = f.approvals
    pre = days(ap["request_date"]) < seam
    a, z = ap[pre], ap[~pre].sort_values("request_date", kind="stable")
    z = z.assign(approval_id=np.arange(len(a) + 1, len(a) + len(z) + 1))
    approvals = pd.concat([a, z], ignore_index=True)
    approvals["approval_id"] = approvals["approval_id"].astype("int64")
    inv["invoice_id"] = inv["invoice_id"].astype("int64")
    dsp["dispatch_key"] = dsp["dispatch_key"].astype("int64")
    return Frames(inv, lines, orders, dsp, rec, ledger, approvals, f.fx)


def _bank(
    base: pd.DataFrame, rec: pd.DataFrame, seed: int, seam: np.datetime64 = SEAM
) -> pd.DataFrame:
    """The bank ledger after the seam, rebuilt from the receivables the way build.bank does."""
    r = co.stream(seed, "events", "bank")
    ed = days(base["entry_date"])
    keep = base[ed < seam]
    paid = rec[(days(rec["paid_true"]) >= seam) & (days(rec["paid_true"]) < b.INF)]
    credits = pd.DataFrame(
        {
            "entry_date": days(paid["paid_true"]),
            "bank": "HDFC current",
            "description": "Receipt " + paid["invoice_no"],
            "debit": 0.0,
            "credit": np.round(paid["gross"].to_numpy() - paid["partial_paid"].to_numpy(), 2),
        }
    )
    # the week that straddles the seam counts its receipts from before the seam too
    week0 = b.week_end(np.array([seam]))[0] - 6 * b.DAY
    early = keep[(days(keep["entry_date"]) >= week0) & (keep["bank"] == "HDFC current")]
    early = early[early["credit"] > 0]
    allc = pd.concat([early[["entry_date", "credit"]], credits[["entry_date", "credit"]]])
    wk = allc.groupby(b.week_end(days(allc["entry_date"])))["credit"].sum()
    wk = wk[wk.index <= d64(EXT_END) + 6 * b.DAY]
    debits = pd.DataFrame(
        {
            "entry_date": wk.index.to_numpy(),
            "bank": "HDFC current",
            "description": "Supplier and payroll payments",
            "debit": np.round(wk.to_numpy() * 0.985 * r.lognormal(0, 0.05, len(wk)), 2),
            "credit": 0.0,
        }
    )
    sbi_base = base[(ed >= seam) & (base["bank"] == "SBI cash credit")][
        ["entry_date", "bank", "description", "debit", "credit"]
    ]
    months = pd.period_range("2027-01", pd.Timestamp(EXT_END).strftime("%Y-%m"), freq="M")
    sbi = pd.DataFrame(
        {
            "entry_date": [d64(p.end_time.date()) for p in months],
            "bank": "SBI cash credit",
            "description": "Interest on cash credit",
            "debit": np.round(r.uniform(90_000, 130_000, len(months)), 2),
            "credit": 0.0,
        }
    )
    new = pd.concat([credits, debits, sbi_base, sbi], ignore_index=True)
    new["entry_date"] = days(new["entry_date"])
    new = new.sort_values(["entry_date", "bank", "credit", "debit"], kind="stable").reset_index(
        drop=True
    )
    flow = new["credit"] - new["debit"]
    last = keep.groupby("bank")["balance"].last()
    new["balance"] = np.round(
        new["bank"].map(last).to_numpy() + flow.groupby(new["bank"]).cumsum().to_numpy(), 2
    )
    new["entry_id"] = np.arange(len(keep) + 1, len(keep) + len(new) + 1)
    new["entry_date"] = pd.to_datetime(new["entry_date"])
    out = pd.concat([keep, new], ignore_index=True)
    return out
