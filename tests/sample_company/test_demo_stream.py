# SPDX-License-Identifier: Apache-2.0
"""The live demo company's stream (tests/fixtures/sample_company/demo_stream.py,
docs/plans/live-demo-account.md).

The guard first: the shared stream (`generate.generate`, which indigothread and indigo2 load)
doesn't move. Its hashes for a few dates around and after the demo's start were taken before the
demo stream existed (20 Nov 2026). Then the demo stream's own promise: each night's dump extends
the one before (the app reads incrementally; a rewritten history would show as restatements),
and its numbers stay whole.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.fixtures.sample_company import demo_stream as ds
from tests.fixtures.sample_company.generate import SampleCompany, generate

D = dt.date
SHARED = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures/sample_company/hashes_shared_stream.json"
    ).read_text()
)


# The demo dumps already handed to the server (setup's load of 20 Nov, the 01:30 load of 21 Nov):
# they can never change. Add each night here once its dump is handed over; a plant that would move
# one moves instead.
LOADED = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures/sample_company/hashes_demo_loaded.json"
    ).read_text()
)


@pytest.mark.parametrize("day", sorted(LOADED))
def test_the_loaded_demo_nights_do_not_move(day: str) -> None:
    assert ds.generate_demo(as_of=D.fromisoformat(day)).data_hash() == LOADED[day], day


@pytest.mark.parametrize("day", sorted(SHARED))
def test_the_shared_stream_does_not_move(day: str) -> None:
    assert generate(as_of=D.fromisoformat(day)).data_hash() == SHARED[day], day


def test_the_guard_covers_the_demo_start() -> None:
    assert {"2026-11-21", "2026-12-15"} <= set(SHARED)


# History tables: (source, table, columns that may change after a row is visible). A row
# visible as of D is in every later dump with the same values, apart from those columns
# (an invoice number on a dispatch once its invoice exists, a receivable paid).
HISTORY = (
    ("erp", "invoices", ()),
    ("erp", "invoice_lines", ("line_id",)),
    ("erp", "cost_ledger", ()),
    ("erp", "bank_ledger", ()),
    ("erp", "fx_rates", ()),
    ("erp", "receivables", ("receivable_id", "paid_amount", "paid_date", "status", "updated_at")),
    ("dw", "fact_invoice", ()),
    ("dw", "fact_invoice_line", ()),
    ("dw", "fact_dispatch", ("invoice_no",)),
    ("dw", "fact_order", ("status", "invoice_no", "updated_at")),
)


def _table(c: SampleCompany, src: str, name: str) -> pd.DataFrame:
    return c.erp[name] if src == "erp" else c.dw["sales_dw"][name]


def _rows(df: pd.DataFrame, drop: tuple[str, ...]) -> set[tuple[str, ...]]:
    df = df.drop(columns=[c for c in drop if c in df.columns])
    return set(df.astype(str).itertuples(index=False, name=None))


PAIRS = (
    (D(2026, 11, 20), D(2026, 11, 21)),
    (D(2026, 11, 21), D(2026, 11, 28)),
    (D(2026, 12, 15), D(2026, 12, 16)),
    (D(2027, 1, 31), D(2027, 2, 1)),
    (D(2027, 3, 30), D(2027, 3, 31)),
)


@pytest.mark.parametrize(("a", "b"), PAIRS)
def test_each_dump_extends_the_one_before(a: D, b: D) -> None:
    ca, cb = ds.generate_demo(as_of=a), ds.generate_demo(as_of=b)
    for src, name, free in HISTORY:
        before, after = _table(ca, src, name), _table(cb, src, name)
        lost = _rows(before, free) - _rows(after, free)
        assert not lost, (a, b, name, sorted(lost)[:3])
        assert len(after) >= len(before), (a, b, name)


@pytest.fixture(scope="module")
def end() -> SampleCompany:
    return ds.generate_demo(as_of=D(2027, 4, 1))


def test_numbers_stay_whole(end: SampleCompany) -> None:
    inv = end.erp["invoices"]
    assert inv["invoice_id"].is_unique and inv["invoice_no"].is_unique
    assert (np.diff(inv["invoice_id"].to_numpy()) > 0).all()
    assert (
        np.diff(pd.to_datetime(inv["invoice_date"]).to_numpy()) >= np.timedelta64(0, "ns")
    ).all()
    for fy in ("2425", "2526", "2627"):
        nos = inv[inv["invoice_no"].str.startswith(f"BI/{fy}/")]["invoice_no"].str[-6:]
        assert list(nos.astype(int)) == list(range(1, len(nos) + 1)), fy  # one series, no gaps
    lines = end.erp["invoice_lines"].groupby("invoice_id")["net_amount"].sum()
    assert np.allclose(lines.reindex(inv["invoice_id"]).to_numpy(), inv["net_amount"], atol=0.05)
    assert not inv["net_amount"].isna().any()
    bank = end.erp["bank_ledger"]
    for _name, g in bank.groupby("bank"):
        flow = (g["credit"] - g["debit"]).cumsum().to_numpy()
        bal = g["balance"].to_numpy()
        assert np.allclose(np.diff(bal), np.diff(flow), atol=0.05)


def _dates(c: SampleCompany, name: str) -> list[D]:
    cust = c.erp["customers"]
    cid = int(cust.loc[cust["name"] == name, "customer_id"].iloc[0])
    inv = c.erp["invoices"]
    return sorted(inv.loc[inv["customer_id"] == cid, "invoice_date"])


def test_every_silence_is_in_the_data(end: SampleCompany) -> None:
    """No invoice inside a silence; the customer is back on its date (with a catch-up) or at its
    next regular invoice (without one)."""
    for s in ds.SILENCES:
        got = _dates(end, s.name)
        assert not [d for d in got if s.start <= d < (s.back or D(2100, 1, 1))], s
        if s.back and s.back <= D(2027, 3, 31):
            if s.catch_up:
                assert s.back in got, s
            elif s.back <= D(2027, 3, 1):
                assert [d for d in got if s.back <= d < s.back + dt.timedelta(days=30)], s


def test_the_rhythm_customers_invoice_steadily(end: SampleCompany) -> None:
    """Outside their silences, the rhythm customers' gaps stay close to their own median."""
    for name in ds.CADENCE:
        got = [d for d in _dates(end, name) if d < D(2026, 10, 1)]
        gaps = np.diff([d.toordinal() for d in got])
        assert np.quantile(gaps, 0.95) <= np.median(gaps) + 4, name


# Gross margin per metre by week (Monday to Sunday) and plant, straight from the sources with the
# Ontology's definition (tests/fixtures/sample_company/ontology.py `gross_margin_per_metre`):
# (line revenue − credit notes − rebates − cost of goods) ÷ metres dispatched, credit notes
# allocated to plants by the lines of the invoice they credit, rebates by the customer's line
# revenue by plant in the month. Re-dated, scaled and dropped invoices must take their cost of
# goods with them (a slipped week once read −₹52.7 a metre on the live account, 21 Nov 2026).
GM_WEEKS = (D(2026, 8, 24), D(2026, 12, 20))


def _monday(s: pd.Series) -> pd.Series:
    d = pd.to_datetime(s)
    return (d - pd.to_timedelta(d.dt.day_of_week, unit="D")).dt.date


def margin_by_week(c: SampleCompany, weeks: tuple[D, D] = GM_WEEKS) -> pd.DataFrame:
    """Net revenue, cost of goods, freight and metres by Monday week and plant; plus `gm` and
    `fpm` (gross margin and freight per metre). Plant "All" is the company."""
    from tests.fixtures.sample_company import company as co

    erp, dw = c.erp, c.dw["sales_dw"]
    inv = erp["invoices"][["invoice_id", "invoice_date", "customer_id"]]
    ln = erp["invoice_lines"].merge(inv, on="invoice_id")
    ln = ln.merge(erp["items"][["item_code", "plant"]], on="item_code", how="left")
    rev = ln.groupby([_monday(ln["invoice_date"]), ln["plant"]])["net_amount"].sum()
    share = ln.groupby(["invoice_id", "plant"])["net_amount"].sum()
    share = (share / share.groupby(level=0).transform("sum")).rename("s").reset_index()
    cn = erp["credit_notes"].merge(share, on="invoice_id")
    cn_w = (cn["net_amount"] * cn["s"]).groupby([_monday(cn["cn_date"]), cn["plant"]]).sum()
    led = erp["cost_ledger"]
    rb = led[led["account_code"] == 4410]
    month = pd.to_datetime(ln["invoice_date"]).dt.to_period("M")
    mix = ln.groupby([ln["customer_id"], month, ln["plant"]])["net_amount"].sum()
    mix = (mix / mix.groupby(level=[0, 1]).transform("sum")).rename("m").reset_index()
    mix.columns = ["customer_id", "month", "plant", "m"]
    rb = rb.assign(month=pd.to_datetime(rb["entry_date"]).dt.to_period("M"))
    rb = rb.merge(mix, on=["customer_id", "month"])
    rb_w = (rb["amount"] * rb["m"]).groupby([_monday(rb["entry_date"]), rb["plant_y"]]).sum()
    cg = led[led["account_code"].isin([4100, 4200, 4300])]
    cogs = cg.groupby([_monday(cg["entry_date"]), cg["plant"]])["amount"].sum()
    fr = led[led["account_code"] == 5210]
    frt = fr.groupby([_monday(fr["entry_date"]), fr["plant"]])["amount"].sum()
    fd = dw["fact_dispatch"]
    mt = np.where(fd["uom"] == "YD", fd["dispatch_mtrs"] * co.YARD, fd["dispatch_mtrs"])
    met = pd.Series(mt, index=fd.index).groupby([_monday(fd["dispatch_date"]), fd["plant"]]).sum()
    out = pd.DataFrame({"rev": rev, "cn": cn_w, "rb": rb_w, "cogs": cogs, "frt": frt, "m": met})
    out = out.fillna(0.0)
    out.index.names = ["week", "plant"]
    tot = out.groupby(level="week").sum()
    tot.index = pd.MultiIndex.from_product([tot.index, ["All"]], names=["week", "plant"])
    out = pd.concat([out, tot]).sort_index()
    out["net"] = out["rev"] - out["cn"] - out["rb"]
    out["gm"] = (out["net"] - out["cogs"]) / out["m"]
    out["fpm"] = out["frt"] / out["m"]
    wk = out.index.get_level_values("week")
    return out[(wk >= weeks[0]) & (wk <= weeks[1])]


@pytest.fixture(scope="module")
def margins(end: SampleCompany) -> pd.DataFrame:
    return margin_by_week(end)


def test_margin_per_metre_holds_through_the_plants(margins: pd.DataFrame) -> None:
    """Slips, silences and rhythms move revenue and metres, never the margin: every week's gross
    margin per metre stays in the shared stream's own range (₹20–70 for the company, ₹15–75 a
    plant; the base process is ₹42 with a slow drift, the cost step of 4 Dec takes it lower)."""
    gm = margins["gm"]
    assert (gm > 0).all(), gm[gm <= 0]
    company = gm.xs("All", level="plant")
    assert company.between(20, 70).all(), company[~company.between(20, 70)]
    plants = gm.drop("All", level="plant")
    assert plants.between(15, 75).all(), plants[~plants.between(15, 75)]


def test_the_slip_weeks_still_dip_then_rise(end: SampleCompany) -> None:
    """A slip is a metres (and revenue) story: under in its week, over the week after."""
    full = margin_by_week(end, (D(2026, 8, 1), D(2027, 1, 31)))
    metres = full["m"].xs("All", level="plant")
    for sl in ds.SLIPS:
        week = sl.week + dt.timedelta(days=1)  # the Monday week holding the slipped Sunday week
        if week > D(2026, 12, 31):
            continue
        before = metres[(metres.index < week) & (metres.index >= week - dt.timedelta(weeks=4))]
        after = metres[week + dt.timedelta(weeks=1)]
        assert metres[week] < 0.7 * before.mean(), (sl, metres[week], before.mean())
        assert after > 1.5 * metres[week], (sl, after, metres[week])


# The nights the live account reads (load dates 22 Nov - 20 Dec, data up to the day before).
STREAM_NIGHTS = [D(2026, 11, 22) + dt.timedelta(days=i) for i in range(29)]


@pytest.mark.parametrize("night", STREAM_NIGHTS, ids=str)
def test_margin_per_metre_holds_on_every_night(night: D) -> None:
    """What each night's dump shows, not only the finished stream: every finished week since 24 Aug
    stays at ₹20-70 a metre (company) and ₹15-75 (a plant), and the week in progress is never
    below zero (its cost of goods lands on the Saturday, so on weekdays it reads high, not low).
    Before costs_follow the slipped weeks read -₹52.7 (9 Nov) and -₹123.7 (14 Dec)."""
    last = night - dt.timedelta(days=1)
    m = margin_by_week(ds.generate_demo(as_of=night), (GM_WEEKS[0], last))
    done = np.array([w + dt.timedelta(days=6) <= last for w in m.index.get_level_values("week")])
    gm = m.loc[done, "gm"]
    company = gm.xs("All", level="plant")
    assert company.between(20, 70).all(), (night, company[~company.between(20, 70)])
    plants = gm.drop("All", level="plant")
    assert plants.between(15, 75).all(), (night, plants[~plants.between(15, 75)])
    open_ = m.loc[~done & (m["m"] > 0).to_numpy(), "gm"]
    assert (open_ > 0).all(), (night, open_[open_ <= 0])
