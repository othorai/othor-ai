# SPDX-License-Identifier: Apache-2.0
"""The live demo company's own data stream (docs/plans/live-demo-account.md, owner 20 Nov 2026).

`indigodemo` reads its own two databases (`sales_dw_demo`, `erp_db_demo`), loaded with a full
dump each night before the 02:00 run. Its stream is the shared stream with its events
(`generate.with_events`, events.py) plus two layers of its own:

- **The recent story** (data dates 12 Oct – 19 Nov 2026): fresh versions of the weekly and daily
  patterns that have gone stale in the shared stream, so the first morning (21 Nov) reads like
  the sample's 1 Nov night.
- **The rolling calendar** (21 Nov 2026 – 31 Mar 2027): every week new trouble, something
  getting worse, something cleared by the data, the odd quiet morning.

Nothing in this stream had ever been loaded before 20 Nov, so its history up to then is written
freely. From the first dump on (load date 20 Nov) the **prefix rule** holds: the sources as of
D + 1 extend those as of D, and rows already visible as of D never change later (beyond ordinary
updates: a receivable paid, an order invoiced, a late posting). That holds because the whole
timeline is built once and every as-of is a cut of it (`generate.render`), and
`tests/sample_company/test_demo_stream.py` checks it.

The shared stream (`generate.generate`) doesn't move: the same test holds its hashes.

Dates are data dates: an invoice dated D is first in the sources as of D + 1, so the run of D + 1
and its 06:00 brief see it first. Each plant draws from its own random stream.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

from tests.fixtures.sample_company import build as b
from tests.fixtures.sample_company import company as co
from tests.fixtures.sample_company import events as ev
from tests.fixtures.sample_company import generate as gen

D = dt.date
SEED = 20261101
DEMO_SEAM = b.d64("2026-10-12")  # the recent story starts here; rows after it are renumbered
FIRST_LOAD = D(2026, 11, 20)  # the first dump (setup's first run reads it)
QUIET_MORNINGS: tuple[D, ...] = ()  # planned quiet mornings (Leadership may be empty)
ERP_FROM = D(2024, 4, 1)  # erp_db's first invoice: the rhythms below hold from here
RENUMBER_FROM = b.d64(ERP_FROM)  # re-dated invoices: every number from here on is given again


@dataclasses.dataclass(frozen=True)
class Silence:
    """A customer with a steady rhythm stops invoicing on `start` and is back on `back`."""

    name: str
    start: D
    back: D | None
    catch_up: bool = True  # its held invoices follow from `back` (else they never happen)


@dataclasses.dataclass(frozen=True)
class Hold:
    """Invoices due in `due` left unpaid until `paid` (tranches)."""

    key: str
    due: tuple[D, D]
    share: float
    paid: tuple[D, ...]
    regions: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Spread:
    """A plant's dispatch times `k` times as spread from `start` to `end`."""

    plant: str
    start: D
    end: D
    k: float


@dataclasses.dataclass(frozen=True)
class BankMove:
    """A one-off bank entry: `amount` in (+) or out (−) of `bank` on `day`."""

    day: D
    bank: str
    amount: float
    description: str


@dataclasses.dataclass(frozen=True)
class Slip:
    """A `share` of the week from Sunday `week` invoiced a week late."""

    week: D
    share: float


# The customers who invoice on a steady rhythm in this stream (none of the boards' named
# accounts, none of South's mid accounts that carry P1).
CADENCE: tuple[str, ...] = (
    "Castelino",
    "Elbtal Garn",
    "Skerry Apparel",
    "Avonmere Cloth Co",
    "Bernalp Stoff",
    "Mondelo Têxteis",
    "Vela Moda Srl",
    "Wattmeer Textil",
    "Konvira Overseas",
    "Van Rijt Stoffen",
    "Dalmara Moda",
    "Kempen Linnen",
    "Tatrava Cloth",
    "Solvik Textil",
    "Scheldra Weefsels",
    "Maison Vallin",
)

# Missing records, one customer at a time: New the morning its silence passes its own rhythm
# (about twice its usual gap), cleared by data the morning after it is back. The comment is the
# morning each shows (the nights test checks them).
SILENCES: tuple[Silence, ...] = (
    Silence("Kempen Linnen", D(2026, 10, 10), D(2026, 11, 26)),  # recent story: open on 20 Nov
    Silence("Elbtal Garn", D(2026, 10, 24), D(2026, 11, 28)),  # New Mon 23 Nov, back Sun 29 Nov
    Silence("Skerry Apparel", D(2026, 10, 27), D(2026, 12, 3)),  # New Thu 26 Nov, back Fri 4 Dec
    Silence("Avonmere Cloth Co", D(2026, 10, 25), D(2026, 11, 30)),  # New Sat 28 Nov, back 1 Dec
    Silence("Tatrava Cloth", D(2026, 10, 24), D(2026, 12, 10)),  # New Tue 1 Dec, back Fri 11 Dec
    Silence("Dalmara Moda", D(2026, 10, 28), D(2026, 12, 9)),  # New Sat 5 Dec, back Thu 10 Dec
    Silence("Mondelo Têxteis", D(2026, 11, 1), D(2026, 12, 9)),  # New Sat 5 Dec, back Thu 10 Dec
    Silence("Van Rijt Stoffen", D(2026, 11, 6), D(2026, 12, 14)),  # New Tue 8 Dec, back Tue 15 Dec
    Silence("Solvik Textil", D(2026, 11, 1), D(2026, 12, 21)),  # New Wed 9 Dec, back Tue 22 Dec
    Silence("Wattmeer Textil", D(2026, 11, 10), D(2026, 12, 15)),  # New Sat 12 Dec, back Wed 16 Dec
    Silence("Castelino", D(2026, 11, 12), D(2026, 12, 21)),  # New Mon 14 Dec, back Tue 22 Dec
    Silence("Vela Moda Srl", D(2026, 11, 12), D(2026, 12, 21)),  # New Wed 16 Dec, back Tue 22 Dec
    Silence("Scheldra Weefsels", D(2026, 11, 7), D(2026, 12, 26)),  # New Thu 17 Dec, back 27 Dec
    Silence("Bernalp Stoff", D(2026, 11, 18), D(2027, 1, 1)),  # New Thu 24 Dec, back Sat 2 Jan
    Silence("Maison Vallin", D(2026, 11, 22), D(2027, 1, 1)),  # New Mon 28 Dec, back Sat 2 Jan
    Silence("Konvira Overseas", D(2026, 11, 17), D(2027, 1, 2)),  # New Thu 31 Dec, back Sun 3 Jan
    Silence("Elbtal Garn", D(2026, 12, 9), D(2027, 1, 12)),  # New Fri 8 Jan, back Wed 13 Jan
    Silence("Kempen Linnen", D(2026, 12, 11), D(2027, 1, 13)),  # New Sun 10 Jan, back Thu 14 Jan
    Silence("Skerry Apparel", D(2026, 12, 24), D(2027, 3, 3), catch_up=False),
    Silence("Avonmere Cloth Co", D(2026, 12, 22), D(2027, 2, 24), catch_up=False),
    Silence("Mondelo Têxteis", D(2026, 12, 22), D(2027, 3, 1), catch_up=False),
    Silence("Wattmeer Textil", D(2026, 12, 26), D(2027, 2, 23), catch_up=False),
    Silence("Dalmara Moda", D(2026, 12, 24), D(2027, 3, 16), catch_up=False),
    Silence("Vela Moda Srl", D(2027, 1, 1), D(2027, 3, 14), catch_up=False),
    Silence("Van Rijt Stoffen", D(2027, 1, 8), D(2027, 3, 10), catch_up=False),
    Silence("Tatrava Cloth", D(2027, 1, 6), D(2027, 1, 27), catch_up=False),
    Silence("Castelino", D(2027, 1, 16), D(2027, 3, 18), catch_up=False),
    Silence("Scheldra Weefsels", D(2027, 1, 8), D(2027, 1, 29), catch_up=False),
    Silence("Maison Vallin", D(2027, 1, 16), D(2027, 3, 22), catch_up=False),
    Silence("Solvik Textil", D(2027, 1, 17), D(2027, 2, 7), catch_up=False),
    Silence("Elbtal Garn", D(2027, 1, 26), D(2027, 2, 16), catch_up=False),
    Silence("Konvira Overseas", D(2027, 1, 15), D(2027, 2, 5), catch_up=False),
    Silence("Bernalp Stoff", D(2027, 1, 28), D(2027, 3, 31), catch_up=False),
    Silence("Kempen Linnen", D(2027, 2, 11), D(2027, 3, 4), catch_up=False),
)

# Receivables: invoices due in a window left unpaid, then paid in tranches.
HOLDS: tuple[Hold, ...] = (
    Hold("H1", (D(2026, 8, 1), D(2026, 8, 25)), 0.35, (D(2026, 12, 7), D(2026, 12, 21))),
    Hold("H2", (D(2026, 12, 10), D(2026, 12, 31)), 0.5, (D(2027, 1, 25), D(2027, 2, 8)), ("West",)),
    Hold("H3", (D(2027, 1, 10), D(2027, 1, 25)), 0.5, (D(2027, 3, 1),), ("North",)),
    Hold("H4", (D(2027, 2, 15), D(2027, 3, 5)), 0.5, (D(2027, 4, 15),), ("Export",)),
    Hold("H5", (D(2027, 1, 1), D(2027, 1, 20)), 0.5, (D(2027, 2, 22), D(2027, 3, 8)), ("South",)),
)

# Rising variance: a plant's dispatch times spread wider, the average held.
SPREADS: tuple[Spread, ...] = (
    Spread("Surat", D(2026, 10, 12), D(2026, 12, 12), 2.4),  # recent story
    # Coimbatore's prior weeks still carry P7's wide spread (to 11 Dec): it takes 4×
    Spread("Coimbatore", D(2026, 12, 21), D(2027, 2, 8), 4.0),
    Spread("Surat", D(2027, 2, 22), D(2027, 4, 1), 3.0),
)

# Forecast breach: part of a week invoiced a week late (under, then over).
SLIPS: tuple[Slip, ...] = (
    Slip(D(2026, 11, 8), 0.45),  # recent story
    Slip(D(2026, 12, 13), 0.85),
    Slip(D(2027, 1, 17), 0.85),
    Slip(D(2027, 2, 14), 0.85),
    Slip(D(2027, 3, 14), 0.85),
)

# Cash balance (Changepoint by bank): one-off movements that step a bank's balance. A stock, so
# New about four weeks after the step; a later step the other way opens a new finding and clears
# the first.
BANK_MOVES: tuple[BankMove, ...] = (
    BankMove(D(2026, 12, 21), "HDFC current", -25_000_000.0, "Machinery: new looms (capex)"),
    BankMove(D(2027, 1, 4), "SBI cash credit", 15_000_000.0, "Cash credit drawn"),
    BankMove(D(2027, 2, 1), "HDFC current", 30_000_000.0, "Term loan disbursed"),
    BankMove(D(2027, 2, 15), "SBI cash credit", -20_000_000.0, "Cash credit repaid"),
)


def d64(x: Any) -> np.datetime64:
    return b.d64(x)


def days(a: Any) -> np.ndarray:
    return np.asarray(a).astype("datetime64[D]")


@lru_cache(maxsize=2)
def demo_timeline(seed: int = SEED) -> gen.Timeline:
    """The shared stream's timeline with its events, plus the demo company's own plants."""
    return apply(gen.with_events(seed), seed)


def generate_demo(seed: int = SEED, as_of: dt.date = FIRST_LOAD) -> gen.SampleCompany:
    """The demo company's sources as of `as_of` (everything dated before it)."""
    return gen.render(demo_timeline(seed), seed, as_of)


def apply(t: gen.Timeline, seed: int) -> gen.Timeline:
    """The plants, applied to the finished timeline (after the shared events)."""
    f = ev.Frames(
        t.inv.copy(),
        t.lines.copy(),
        t.book.orders.copy(),
        t.dispatch.copy(),
        t.rec.copy(),
        t.ledger.copy(),
        t.approvals.copy(),
        t.fx,
    )
    code = {c.name: c.code for c in t.roster}
    region = f.inv.groupby(["region", "month"])["amt"].sum()
    for name in CADENCE:
        cadence(f, code[name], seed)
    hold_regions(f, region, {code[n] for n in CADENCE})
    for sl in SLIPS:  # before the silences, so no slipped invoice lands inside one
        slip(f, sl, seed)
    for s_ in SILENCES:
        silence(f, code[s_.name], s_.start, s_.back, s_.catch_up)
    for h in HOLDS:
        hold(f, h, seed)
    for sp in SPREADS:
        spread(f, sp)
    costs_follow(f, t)  # last: the weeks' revenue and metres are final
    out = ev._renumber(f, t, seam=RENUMBER_FROM)
    bank = bank_moves(ev._bank(t.bank, out.rec, seed, seam=DEMO_SEAM), BANK_MOVES)
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
    )


# ---------------------------------------------------------------------------------------------
# Building blocks: each changes invoices with everything that hangs off them (lines, order,
# dispatch, receivable), so the numbers stay whole.


def _sunday_free(d: np.ndarray) -> np.ndarray:
    d = days(d)
    return np.where(b.is_sunday(d), d + b.DAY, d)


def move(f: ev.Frames, ids: np.ndarray, new: np.ndarray) -> None:
    """Re-date invoices (by invoice_id) to `new`, their lines, order, dispatch and receivable
    moved by the same number of days."""
    inv_i = f.inv.set_index("invoice_id")
    old = days(inv_i.loc[ids, "date"].to_numpy())
    delta = pd.Series((days(new) - old).astype("timedelta64[D]"), index=ids)
    nos = pd.Series(inv_i.loc[ids, "invoice_no"].to_numpy(), index=ids)
    by_no = pd.Series(delta.to_numpy(), index=nos.to_numpy())

    def shift(df: pd.DataFrame, key: str, cols: list[str], m: pd.Series) -> None:
        sel = df[key].isin(m.index).to_numpy()
        if not sel.any():
            return
        dd = df.loc[sel, key].map(m).to_numpy().astype("timedelta64[D]")
        for c in cols:
            v = df.loc[sel, c].to_numpy()
            if np.issubdtype(v.dtype, np.datetime64):
                unit = np.datetime_data(v.dtype)[0]
                inf = v >= b.INF.astype(v.dtype)
                df.loc[sel, c] = np.where(inf, v, v + dd.astype(f"timedelta64[{unit}]"))

    shift(f.inv, "invoice_id", ["date", "cn_date", "cn_posted"], delta)
    sel = f.inv["invoice_id"].isin(ids)
    f.inv.loc[sel, "month"] = days(f.inv.loc[sel, "date"]).astype("datetime64[M]").astype(str)
    f.inv.loc[sel, "cn_month"] = days(f.inv.loc[sel, "cn_date"]).astype("datetime64[M]").astype(str)
    shift(f.lines, "invoice_id", ["date"], delta)
    shift(f.rec, "invoice_id", ["date", "due", "paid_true", "cn_date", "cn_posted"], delta)
    sel = f.rec["invoice_id"].isin(ids)
    f.rec.loc[sel, "month"] = days(f.rec.loc[sel, "date"]).astype("datetime64[M]").astype(str)
    shift(f.orders, "invoice_no", ["order_date", "close"], by_no)
    shift(f.dispatch, "invoice_no", ["dispatched_at", "ready_at", "day"], by_no)


def drop(f: ev.Frames, ids: np.ndarray) -> None:
    """Invoices that never happen, with their lines, order, dispatch and receivable."""
    nos = set(f.inv.loc[f.inv["invoice_id"].isin(ids), "invoice_no"])
    gone = f.orders[f.orders["invoice_no"].isin(nos)]
    f.inv = f.inv[~f.inv["invoice_id"].isin(ids)]
    f.lines = f.lines[~f.lines["invoice_id"].isin(ids)]
    f.rec = f.rec[~f.rec["invoice_id"].isin(ids)]
    f.orders = f.orders[~f.orders["invoice_no"].isin(nos)]
    f.dispatch = f.dispatch[
        ~(f.dispatch["invoice_no"].isin(nos) | f.dispatch["order_no"].isin(set(gone["order_no"])))
    ]


def scale(f: ev.Frames, ids: np.ndarray, k: float | np.ndarray) -> None:
    """Invoices worth `k` times as much (more or fewer metres at the same rates)."""
    k = pd.Series(np.broadcast_to(np.asarray(k, float), len(ids)), index=ids)
    by_no = pd.Series(
        k.to_numpy(), index=f.inv.set_index("invoice_id").loc[ids, "invoice_no"].to_numpy()
    )
    money = ["amt", "net", "gst", "gross", "amount_fc"]
    for df, cols in ((f.inv, money), (f.rec, [*money, "partial_paid"])):
        sel = df["invoice_id"].isin(ids).to_numpy()
        m = df.loc[sel, "invoice_id"].map(k).to_numpy()
        for c in cols:
            df.loc[sel, c] = np.round(df.loc[sel, c].to_numpy(dtype=float) * m, 2)
    sel = f.lines["invoice_id"].isin(ids).to_numpy()
    m = f.lines.loc[sel, "invoice_id"].map(k).to_numpy()
    f.lines.loc[sel, "net"] = np.round(f.lines.loc[sel, "net"].to_numpy() * m, 2)
    f.lines.loc[sel, "qty_m"] = np.round(f.lines.loc[sel, "qty_m"].to_numpy() * m, 1)
    sel = f.orders["invoice_no"].isin(by_no.index).to_numpy()
    m = f.orders.loc[sel, "invoice_no"].map(by_no).to_numpy()
    f.orders.loc[sel, "amount"] = np.round(f.orders.loc[sel, "amount"].to_numpy() * m, 2)
    sel = f.dispatch["invoice_no"].isin(by_no.index).to_numpy()
    m = f.dispatch.loc[sel, "invoice_no"].map(by_no).to_numpy()
    for c in ("qty_m", "dispatch_mtrs"):
        f.dispatch.loc[sel, c] = np.round(f.dispatch.loc[sel, c].to_numpy() * m, 1)


def _own(f: ev.Frames, cust: str, lo: Any = None, hi: Any = None) -> pd.DataFrame:
    d = days(f.inv["date"])
    sel = (f.inv["cust"] == cust).to_numpy() & (f.inv["pin"] == "").to_numpy()
    if lo is not None:
        sel &= d >= d64(lo)
    if hi is not None:
        sel &= d < d64(hi)
    return f.inv[sel].sort_values("date", kind="stable")


def cadence(f: ev.Frames, cust: str, seed: int) -> None:
    """A customer who invoices on a steady rhythm (every ~2 weeks) over the whole erp_db
    history: its invoices re-dated evenly between its first and last, a day either way, so a
    silence of a month is unusual for it (Missing records: one entity, unusually quiet)."""
    own = _own(f, cust, lo=ERP_FROM)
    if len(own) < 8:
        return
    r = co.stream(seed, "demo", "cadence", cust)
    first, last = days(own["date"].iloc[0]), days(own["date"].iloc[-1])
    span = (last - first).astype(int)
    pos = np.round(np.linspace(0, span, len(own)) + r.integers(-1, 2, len(own))).astype(int)
    new = _sunday_free(first + np.clip(pos, 0, span) * b.DAY)
    move(f, own["invoice_id"].to_numpy(), new)


def hold_regions(f: ev.Frames, before: pd.Series, moved: set[str]) -> None:
    """Every region's month as it was before the rhythms: the other customers' invoices in it
    scaled to fill it (so the monthly stories, P1 first, read as they did)."""
    d = days(f.inv["date"])
    sel = (d >= d64(ERP_FROM)) & (f.inv["pin"] == "").to_numpy() & ~f.inv["cust"].isin(moved)
    now = f.inv.groupby(["region", "month"])["amt"].sum()
    other = f.inv[sel].groupby(["region", "month"])["amt"].sum()
    k = ((before - (now - other)) / other).reindex(other.index).fillna(1.0).clip(0.6, 1.6)
    keys = pd.MultiIndex.from_frame(f.inv.loc[sel, ["region", "month"]])
    scale(f, f.inv.loc[sel, "invoice_id"].to_numpy(), k.reindex(keys).to_numpy())


def silence(f: ev.Frames, cust: str, start: D, back: D | None, catch_up: bool = True) -> None:
    """No invoice from `start` until `back` (exclusive). With `catch_up`, its orders were on
    hold: they are invoiced from `back`, two days apart. Without it (or with no `back`) they
    never happen, and the rhythm after `back` stays clean (a catch-up's two-day gaps make the
    customer's next silence harder to tell from its rhythm)."""
    own = _own(f, cust, lo=start, hi=back)
    ids = own["invoice_id"].to_numpy()
    if back is None or not catch_up:
        drop(f, ids)
        return
    new = d64(back) + 2 * np.arange(len(ids)) * b.DAY
    move(f, ids, _sunday_free(new))


def hold(f: ev.Frames, h: Hold, seed: int) -> None:
    """Invoices due in a window, otherwise paid, left unpaid until `paid` (in tranches when
    `paid` has several dates): receivables over 60 days step up, then come down."""
    due = days(f.rec["due"])
    paid = days(f.rec["paid_true"])
    sel = (due >= d64(h.due[0])) & (due <= d64(h.due[1])) & (paid < b.INF)
    sel &= paid >= due  # paid on or after the due date
    sel &= f.rec["region"].isin(h.regions).to_numpy() if h.regions else True
    idx = np.flatnonzero(sel)
    r = co.stream(seed, "demo", "hold", h.key)
    pick = idx[r.random(len(idx)) < h.share]
    when = np.array([d64(x) for x in h.paid])
    f.rec.iloc[pick, f.rec.columns.get_loc("paid_true")] = pd.to_datetime(
        _sunday_free(when[np.arange(len(pick)) % len(when)])
    )


def spread(f: ev.Frames, sp: Spread) -> None:
    """A plant's dispatch times spread `k` times wider around their usual middle from `start`
    to `end`, the average held (Rising variance)."""
    at = f.dispatch["dispatched_at"].to_numpy()
    lo = d64(sp.start).astype("datetime64[s]")
    hi = d64(sp.end).astype("datetime64[s]")
    sel = (f.dispatch["plant"] == sp.plant).to_numpy() & (at >= lo) & (at < hi)
    x = f.dispatch.loc[sel, "dispatch_days"].to_numpy(dtype=float)
    mid = float(np.median(x)) if len(x) else 0.0
    new = np.clip(mid + (x - mid) * sp.k, 0.4, None)
    new = new * (x.mean() / new.mean()) if len(x) else new  # the average holds
    f.dispatch.loc[sel, "dispatch_days"] = new
    f.dispatch.loc[sel, "ready_at"] = f.dispatch.loc[sel, "dispatched_at"] - pd.to_timedelta(
        np.round(new * 86400), unit="s"
    )


def bank_moves(bank: pd.DataFrame, moves: tuple[BankMove, ...]) -> pd.DataFrame:
    """One-off entries added to the bank ledger; the rows from the first one on are renumbered
    in date order and their balances follow."""
    if not moves:
        return bank
    lo = d64(min(m.day for m in moves))
    ed = days(bank["entry_date"])
    keep, rest = bank[ed < lo], bank[ed >= lo]
    add = pd.DataFrame(
        {
            "entry_date": pd.to_datetime([m.day for m in moves]),
            "bank": [m.bank for m in moves],
            "description": [m.description for m in moves],
            "debit": [round(max(-m.amount, 0.0), 2) for m in moves],
            "credit": [round(max(m.amount, 0.0), 2) for m in moves],
        }
    )
    rest = pd.concat([rest.drop(columns=["balance", "entry_id"]), add], ignore_index=True)
    rest = rest.sort_values(["entry_date", "bank", "credit", "debit"], kind="stable")
    flow = rest["credit"] - rest["debit"]
    last = keep.groupby("bank")["balance"].last()
    rest["balance"] = np.round(
        rest["bank"].map(last).to_numpy() + flow.groupby(rest["bank"]).cumsum().to_numpy(), 2
    )
    rest["entry_id"] = np.arange(len(keep) + 1, len(keep) + len(rest) + 1)
    return pd.concat([keep, rest[bank.columns]], ignore_index=True)


def slip(f: ev.Frames, sl: Slip, seed: int) -> None:
    """A share of one week's invoices slips into the next week (a late dispatch run): the week
    comes in under its forecast band, the next one over (Forecast breach)."""
    d = days(f.inv["date"])
    lo = d64(sl.week)
    sel = (d >= lo) & (d < lo + 7 * b.DAY) & (f.inv["pin"] == "").to_numpy()
    idx = f.inv.loc[sel, "invoice_id"].to_numpy()
    r = co.stream(seed, "demo", "slip", sl.week.isoformat())
    pick = idx[r.random(len(idx)) < sl.share]
    old = days(f.inv.set_index("invoice_id").loc[pick, "date"].to_numpy())
    move(f, pick, _sunday_free(old + 7 * b.DAY))


# Costs follow the weeks: the base generator sets each week's cost of goods from that week's
# revenue and metres (build.costs), and the plants above move both after it. Without this a
# slipped week kept its full cost on half its sales (−₹52.7 a metre on the live account for the
# week of 9 Nov, found 21 Nov 2026).
FREIGHT_COUPLING_AFTER_BREAK = 0.3  # build.costs: freight after P6's break follows metres at 0.3


def _week_plant(
    inv: pd.DataFrame, lines: pd.DataFrame, dsp: pd.DataFrame, kappa: float
) -> pd.DataFrame:
    """Net revenue (credit notes and rebates allocated to plants, as build.costs does) and metres
    dispatched by Saturday week end and plant, from ERP_FROM."""
    start = d64(ERP_FROM)
    lw = lines[days(lines["date"]) >= start]
    names = ["week", "plant"]
    rev = lw.groupby([b.week_end(days(lw["date"])), lw["plant"].to_numpy()])["net"].sum()
    alloc = b._allocated(inv, lw, kappa, start).stack()
    met = pd.Series(b.metres_of(dsp), index=dsp.index)
    met = met.groupby([b.week_end(days(dsp["dispatched_at"])), dsp["plant"].to_numpy()]).sum()
    for x in (rev, alloc, met):
        x.index = x.index.set_names(names)
        x.index = x.index.set_levels(x.index.levels[0].astype("datetime64[ns]"), level=0)
    return pd.DataFrame({"rev": rev.sub(alloc, fill_value=0.0), "m": met}).fillna(0.0)


def costs_follow(f: ev.Frames, t: gen.Timeline) -> None:
    """Cost of goods (4100/4200/4300) and freight (5210) of every week and plant whose revenue or
    metres the plants changed, set again from the new ones: cost of goods so the week keeps the
    margin per metre the timeline had for it (net revenue − margin × metres, each account keeping
    its share of the week), freight the way build.costs ties it to metres (in step before P6's
    break, at 0.3 after it, the company's week split over the plants by metres). Weeks the plants
    didn't touch keep their rows exactly."""
    was = _week_plant(t.inv, t.lines, t.dispatch, t.kappa)
    now = _week_plant(f.inv, f.lines, f.dispatch, t.kappa)
    both = was.add_suffix("0").join(now.add_suffix("1"), how="outer").fillna(0.0)
    lg = f.ledger
    ed = days(lg["entry_date"])
    weekly = ed == b.week_end(ed)
    key = pd.MultiIndex.from_arrays(
        [ed.astype("datetime64[ns]"), lg["plant"].to_numpy()], names=["week", "plant"]
    )
    amount = lg["amount"].to_numpy(dtype=float).copy()

    # cost of goods, by week and plant
    cg = weekly & lg["account_code"].isin([4100, 4200, 4300]).to_numpy()
    cogs0 = pd.Series(amount[cg], index=key[cg]).groupby(level=[0, 1]).sum()
    w = both.reindex(cogs0.index)
    moved = ((w["rev1"] - w["rev0"]).abs() > 0.005) | ((w["m1"] - w["m0"]).abs() > 0.005)
    moved &= w["m0"] > 0
    margin = (w["rev0"] - cogs0) / w["m0"]
    cogs1 = w["rev1"] - margin * w["m1"]
    k = (cogs1 / cogs0).where(moved & (cogs0 > 0))
    rows = np.flatnonzero(cg)
    kr = k.reindex(key[rows]).to_numpy()
    hit = ~np.isnan(kr)
    amount[rows[hit]] = np.round(amount[rows[hit]] * kr[hit], 2)

    # freight: the company's week first, then over the plants by their metres
    fr = weekly & (lg["account_code"] == 5210).to_numpy()
    brk = d64(b.EXPECTED["P6"]["break_date"])
    m = both.groupby(level="week")[["m0", "m1"]].sum()
    f0 = pd.Series(amount[fr], index=ed[fr].astype("datetime64[ns]")).groupby(level=0).sum()
    m = m.reindex(f0.index)
    e = np.where(f0.index.to_numpy() < brk, 1.0, FREIGHT_COUPLING_AFTER_BREAK)
    week_moved = (both["m1"] - both["m0"]).abs().groupby(level="week").max().reindex(f0.index)
    f1 = (f0 * (1 + e * (m["m1"] / m["m0"] - 1))).where((week_moved > 0.005) & (m["m0"] > 0))
    rows = np.flatnonzero(fr)
    wk = ed[rows].astype("datetime64[ns]")
    total = f1.reindex(wk).to_numpy()
    share = both["m1"].reindex(key[rows]).to_numpy() / m["m1"].reindex(wk).to_numpy()
    hit = ~np.isnan(total) & np.isfinite(share)
    amount[rows[hit]] = np.round(total[hit] * share[hit], 2)
    f.ledger = lg.assign(amount=amount)
