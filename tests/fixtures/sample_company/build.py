# SPDX-License-Identifier: Apache-2.0
"""Build the Indigo Thread timeline: invoices, orders, dispatch, receivables, ledgers.

Order of work (docs/plans/M0.md §5):
  1. baseline demand and invoices per customer;
  2. known patterns (P14) and planted patterns that live in invoices (P4, P5, P2 batch);
  3. revenue calibration so the board totals hold (P1, P13 path, credit notes 1.6%);
  4. derived flows: orders (P3, P10, P11), dispatch (P6, P7, P14 Surat), receivables (P2, P4,
     DSO), costs (gross margin, P6 freight), cash, approvals (P8).

Everything is generated over the full horizon (START..END) and cut at `as_of` by the
renderers, so the data for a day never depends on `as_of`. Money is rounded to paise.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tests.fixtures.sample_company import company as co

EXPECTED = json.loads((Path(__file__).parent / "expected.json").read_text())
DAY = np.timedelta64(1, "D")
FY_TOTAL = {2021: 38.0e7, 2022: 43.5e7, 2023: 48.0e7, 2024: 52.0e7, 2025: 55.5e7, 2026: 57.6e7}
SEASON = np.array([0.97, 0.99, 1.02, 0.99, 0.93, 1.02, 1.00, 0.98, 1.02, 0.97, 0.98, 1.13])
SEASON = SEASON / SEASON.mean()
LAST12 = (np.datetime64("2025-11-01"), np.datetime64("2026-11-01"))
SURAT_SHUT = ((8, 10), (8, 24))
DIWALI_2026_BOOST = 0.8  # West's 2026 festive week, after calibration (decided 5 Nov 2026)


def d64(d: dt.date | str) -> np.datetime64:
    return np.datetime64(d, "D")


def month_key(days: np.ndarray) -> np.ndarray:
    return np.asarray(days).astype("datetime64[D]").astype("datetime64[M]").astype(str)


def is_sunday(days: np.ndarray) -> np.ndarray:
    # 1970-01-01 was a Thursday; (days + 3) % 7 == 6 is Sunday.
    return ((np.asarray(days).astype("datetime64[D]").astype("int64") + 3) % 7) == 6


def fiscal_year(day: np.datetime64) -> int:
    d = day.astype(object)
    return d.year if d.month >= 4 else d.year - 1


# ---------------------------------------------------------------------------------------------
# 1. Demand and invoices


def demand(seed: int) -> pd.DataFrame:
    idx = pd.date_range(co.START, co.END, freq="D")
    r = co.stream(seed, "demand")
    t = (idx - idx[0]).days.to_numpy() / 365.25
    trend = np.exp(0.0757 * t)
    week = (idx - idx[0]).days.to_numpy() // 7
    ar = np.zeros(week.max() + 1)
    for k in range(1, len(ar)):
        ar[k] = 0.6 * ar[k - 1] + r.normal(0, 0.07)
    mult = np.exp(ar[week])
    month_end = idx + pd.offsets.MonthEnd(0)
    qe = idx.month.isin([3, 6, 9, 12]) & ((month_end - idx).days < 5)
    mult = mult * np.where(qe, 2.2, 1.0)
    diwali = np.zeros(len(idx), bool)
    for start in EXPECTED["P14"]["diwali_week_start"].values():
        s = pd.Timestamp(start)
        diwali |= (idx >= s) & (idx < s + pd.Timedelta(days=7))
    mult = mult * np.where(diwali, 2.2, 1.0)
    sunday = idx.day_of_week == 6
    mult = np.where(sunday, 0.0, mult)
    return pd.DataFrame({"trend": trend, "mult": mult, "qe": qe, "diwali": diwali}, index=idx)


def _cadence_dates(c: co.Customer, r: np.random.Generator) -> list[np.datetime64]:
    lo, hi = c.gap
    first = d64(max(c.first, co.START))
    last = d64(c.last) if c.last else d64(co.END)
    out = [last]
    stretch = [2.0, 1.8, 1.6] if "quiet" in c.tags else []
    cur = last
    k = 0
    while True:
        g = int(r.integers(lo, hi + 1))
        if k < len(stretch):
            g = round(g * stretch[k])
        k += 1
        nxt = cur - g * DAY
        if is_sunday(np.array([nxt]))[0]:
            nxt -= DAY
        if nxt < first:
            break
        out.append(nxt)
        cur = nxt
    if c.resume:
        cur = d64(c.resume)
        while cur <= d64(co.END):
            if not is_sunday(np.array([cur]))[0]:
                out.append(cur)
            cur += int(r.integers(lo, hi + 1)) * DAY
    return sorted(out)


def invoices(
    seed: int,
    roster: list[co.Customer],
    dem: pd.DataFrame,
    p2_amount: float,
    rate_scale: float = 1.0,
) -> pd.DataFrame:
    """Invoices per customer. `rate_scale` scales Poisson counts (not amounts) so the natural
    revenue lands near the plan and calibration factors stay close to 1."""
    days = dem.index.to_numpy().astype("datetime64[D]")
    ref = dem["trend"].iloc[(days >= d64("2026-04-01")).argmax()]
    rows: list[pd.DataFrame] = []
    for c in roster:
        r = co.stream(seed, "inv", c.code)
        if c.kind == "poisson":
            lo = d64(max(c.first, co.START))
            hi = d64(c.last) if c.last else d64(co.END)
            mask = (days >= lo) & (days <= hi)
            lam = (
                c.per_month
                * rate_scale
                / 26.0
                * dem["trend"].to_numpy()
                / ref
                * dem["mult"].to_numpy()
            )
            if "mehta" in c.tags:  # West's October growth (P1)
                surge = (days >= d64("2026-10-01")) & (days <= d64("2026-10-31"))
                lam = lam * np.where(surge, 5.0, 1.0)
            counts = r.poisson(np.where(mask, lam, 0.0))
            d = np.repeat(days, counts)
            sigma = 0.32 if c.segment == "small" else 0.28
            amt = c.value * r.lognormal(-(sigma**2) / 2, sigma, len(d))
        else:
            d = np.array(_cadence_dates(c, r), dtype="datetime64[D]")
            amt = c.value * r.lognormal(-(0.25**2) / 2, 0.25, len(d))
            qe = dem["qe"].reindex(pd.DatetimeIndex(d)).to_numpy()
            amt = amt * np.where(qe, 1.5, 1.0)
            if "ktn" in c.tags:  # pulled orders forward in September before going quiet
                sep26 = (d >= d64("2026-09-01")) & (d <= d64("2026-09-30"))
                amt = amt * np.where(sep26, 1.3, 1.0)
        rows.append(
            pd.DataFrame({"cust": c.code, "date": d, "amt": amt, "poisson": c.kind == "poisson"})
        )
    inv = pd.concat(rows, ignore_index=True)
    # P2 batch: six large invoices on Fri 15 May 2026, due Sun 14 Jun, never paid.
    batch = pd.DataFrame({"cust": co.P2_BATCH, "date": d64("2026-05-15"), "amt": p2_amount})
    batch["pin"] = "p2"
    batch["poisson"] = False
    inv["pin"] = ""
    inv = pd.concat([inv, batch], ignore_index=True)
    inv = inv.sort_values(["date", "cust", "amt"], kind="stable").reset_index(drop=True)
    return inv


def attach(inv: pd.DataFrame, roster: list[co.Customer], seed: int) -> pd.DataFrame:
    by = {c.code: c for c in roster}
    inv["region"] = inv["cust"].map(lambda k: by[k].region)
    inv["currency"] = inv["cust"].map(lambda k: by[k].currency)
    inv["segment"] = inv["cust"].map(lambda k: by[k].segment)
    inv["month"] = month_key(inv["date"].to_numpy().astype("datetime64[D]"))
    inv["erp"] = inv["date"].to_numpy().astype("datetime64[D]") >= d64(co.ERP_START)
    tags = inv["cust"].map(lambda k: by[k].tags)
    inv.loc[tags.map(lambda t: "quiet" in t).to_numpy(), "pin"] = "quiet"
    inv["ktn"] = tags.map(lambda t: "ktn" in t).to_numpy()
    inv["top4"] = tags.map(lambda t: "top4" in t).to_numpy()
    # Credit notes (10% of invoices) and rebates (key and mid INR accounts, 0.6%).
    r = co.stream(seed, "credit-notes")
    n = len(inv)
    has_cn = (r.random(n) < 0.10) & inv["erp"].to_numpy()
    inv["cn_frac"] = np.where(has_cn, r.uniform(0.05, 0.27, n), 0.0)
    lag = r.integers(4, 35, n)
    cn_date = inv["date"].to_numpy().astype("datetime64[D]") + lag * DAY
    cn_date = np.where(is_sunday(cn_date), cn_date + DAY, cn_date)
    inv["cn_date"] = cn_date
    inv["cn_month"] = month_key(cn_date)
    inv["cn_reason"] = r.choice(
        ["Return", "Rate difference", "Quality claim"], n, p=[0.5, 0.3, 0.2]
    )
    rebate = inv["segment"].isin(["key", "mid"]) & (inv["currency"] == "INR") & inv["erp"]
    inv["rebate"] = np.where(rebate, 0.006, 0.0)
    # P16: one South mid account's credit notes dated 23 Oct (week of 19 Oct), for its two
    # largest invoices of the weeks before, posted in erp_db only on 31 Oct (the MIS books them
    # on time); together ≥ ₹5 L. Set before calibration, so P1 still holds.
    p16 = EXPECTED["P16"]
    wk0 = d64(p16["week"])
    dated = wk0 + 4 * DAY  # Fri 23 Oct
    day = inv["date"].to_numpy().astype("datetime64[D]")
    south_mid = tags.map(lambda t: "south_mid" in t).to_numpy()
    recent = (day >= wk0 - 35 * DAY) & (day <= wk0 - 4 * DAY) & inv["erp"].to_numpy()
    cand = np.flatnonzero(recent & south_mid & (inv["pin"].to_numpy() == ""))
    inv["cn_posted"] = cn_date
    if len(cand):
        cust = inv["cust"].to_numpy()[cand]
        amt = inv["amt"].to_numpy()[cand]
        top2 = {k: np.sort(amt[cust == k])[-2:].sum() for k in sorted(set(cust))}
        best = max(sorted(top2), key=lambda k: top2[k])
        mine = cand[cust == best]
        picks = mine[np.argsort(inv["amt"].to_numpy()[mine], kind="stable")[-2:]]
        frac = inv["cn_frac"].to_numpy().copy()
        frac[picks] = p16["cn_frac"]
        inv["cn_frac"] = frac
        dates = inv["cn_date"].to_numpy().copy()
        dates[picks] = dated
        inv["cn_date"] = dates
        inv["cn_month"] = month_key(dates)
        posted = dates.copy()
        posted[picks] = d64(p16["posted"])
        inv["cn_posted"] = posted
    return inv


# ---------------------------------------------------------------------------------------------
# 2-3. Revenue calibration


def festive_boost(inv: pd.DataFrame, roster: list[co.Customer]) -> pd.DataFrame:
    """The 2026 festive week, stronger than earlier years (M7, decided 5 Nov 2026), added after
    calibration so nothing else in the year moves: West's invoices in the Diwali week rise by
    DIWALI_2026_BOOST and West's other October invoices give the same amount back, spread in
    proportion, so every region's October total (and P1: South −₹1.40 Cr, West +₹1.21 Cr) stays
    exact. West only: North, East and Export carry the clean series C1–C3."""
    start = d64(EXPECTED["P14"]["diwali_week_start"]["2026"])
    day = inv["date"].to_numpy().astype("datetime64[D]")
    west = (inv["region"] == "West").to_numpy()
    october = (day >= d64("2026-10-01")) & (day < d64("2026-11-01"))
    week = (day >= start) & (day < start + 7 * DAY)
    up, rest = west & october & week, west & october & ~week
    amt = inv["amt"].to_numpy().astype(float).copy()
    # each invoice's contribution to October's net revenue: its amount less its rebate, less its
    # credit note when that is dated in October (credit notes and rebates follow the amount)
    cn_day = inv["cn_date"].to_numpy().astype("datetime64[D]")
    cn_oct = (cn_day >= d64("2026-10-01")) & (cn_day < d64("2026-11-01"))
    unit = (1 - inv["rebate"].to_numpy()) - inv["cn_frac"].to_numpy() * cn_oct
    net_up, net_rest = (amt[up] * unit[up]).sum(), (amt[rest] * unit[rest]).sum()
    if net_up <= 0 or net_rest <= net_up * DIWALI_2026_BOOST:
        return inv
    cut = net_up * DIWALI_2026_BOOST / net_rest
    amt[up] *= 1 + DIWALI_2026_BOOST
    amt[rest] *= 1 - cut
    inv = inv.copy()
    inv["amt"] = amt
    return inv


def rate_scale(seed: int, roster: list[co.Customer], dem: pd.DataFrame) -> float:
    """Poisson count scale that makes natural revenue match the plan over the horizon."""
    inv = invoices(seed, roster, dem, 0.0)
    month = month_key(inv["date"].to_numpy())
    plan = sum(plan_total(m) for m in sorted(set(month)))
    pois = inv.loc[inv["poisson"], "amt"].sum()
    return float((plan - inv.loc[~inv["poisson"], "amt"].sum()) / pois)


def plan_total(month: str) -> float:
    y, m = int(month[:4]), int(month[5:7])
    fy = y if m >= 4 else y - 1
    return FY_TOTAL[min(fy, 2026)] / 12 * SEASON[(m - 4) % 12]


def calibrate(inv: pd.DataFrame, roster: list[co.Customer]) -> dict[str, Any]:
    """Scale invoice amounts so net revenue hits the plan and the board figures.

    Pass A scales every month to the plan, holding the P5 accounts (last-12-month totals) and
    the P2 batch fixed. Pass B then sets Sep/Oct 2026 by region the way board 4d tells it:
    Ravira, Tessora and Skarfab carry 71% of South's drop, South's mid accounts the rest,
    Merai Apparels carries West's growth, and North/East/Export stay flat. Everything else in
    Sep/Oct keeps its pass-A level, so the clean series (C1–C10) see no planted step.
    """
    p1 = EXPECTED["P1"]
    by_name = {c.name: c.code for c in roster}
    by_code = {c.code: c for c in roster}
    quiet_code_target = {by_name[n]: t for n, _r, _l, t in co.QUIET}
    sep, oct_ = "2026-09", "2026-10"
    regional = (sep, oct_)
    ktn_t = {sep: 14_000_000.0, oct_: 4_000_000.0}  # carries 71% of South's drop

    amt = inv["amt"].to_numpy().copy()
    month = inv["month"].to_numpy()
    cn_month = inv["cn_month"].to_numpy()
    region = inv["region"].to_numpy()
    cust = inv["cust"].to_numpy()
    pin = inv["pin"].to_numpy()
    erp = inv["erp"].to_numpy()
    frac = inv["cn_frac"].to_numpy()
    reb = inv["rebate"].to_numpy()
    tags = np.array([by_code[c].tags for c in cust], dtype=object)
    ktn = np.array(["ktn" in x for x in tags])
    south_mid = np.array(["south_mid" in x for x in tags])
    mehta = np.array(["mehta" in x for x in tags])
    date = inv["date"].to_numpy().astype("datetime64[D]")
    cn_date = inv["cn_date"].to_numpy().astype("datetime64[D]")
    in12 = (date >= LAST12[0]) & (date < LAST12[1])
    cn12 = (cn_date >= LAST12[0]) & (cn_date < LAST12[1])
    in_reg = np.isin(month, regional)
    every = np.ones(len(amt), bool)
    inv_months = sorted(set(month))
    kappa = 1.0

    def net_by(keys: list[np.ndarray], sel: np.ndarray) -> pd.Series:
        """Net revenue (invoices − CN − rebates) by month (+region) for rows `sel`."""
        gross = pd.Series(amt * sel).groupby(keys).sum()
        rb = pd.Series(amt * reb * sel).groupby(keys).sum()
        cn_keys = [cn_month if k is month else k for k in keys]
        cn = pd.Series(amt * frac * kappa * sel * erp).groupby(cn_keys).sum()
        return gross.sub(rb, fill_value=0).sub(cn, fill_value=0)

    def scale(sel: np.ndarray, target: float, cur: float, part: float) -> float:
        if not sel.any():
            raise RuntimeError(f"nothing to scale toward {target}")
        amt[sel] *= max(1 + (target - cur) / max(part, 1.0), 0.05)
        return abs(target - cur) / abs(target)

    absorber = ktn | south_mid | mehta
    summer = np.isin(month, ["2026-06", "2026-07", "2026-08"])

    def run(region_t: dict[tuple[str, str], float] | None) -> None:
        nonlocal kappa
        fixed = pin != ""
        # Pass A: Sep/Oct free accounts keep their summer share of the plan; the absorbers
        # (KTN pull-forward, Merai's growth) don't squeeze them. Pass B: regions, below.
        month_rule = ~fixed & ~in_reg if region_t else ~fixed & ~(in_reg & absorber)
        for _ in range(60):
            kappa = 0.016 * (amt * in12).sum() / (amt * frac * cn12).sum()
            for code, target in quiet_code_target.items():
                sel = cust == code
                amt[sel] *= target / (amt * sel * in12).sum()
            worst = 0.0
            tot = net_by([month], every)
            part = net_by([month], month_rule)
            free_share = (
                net_by([month], summer & ~absorber & ~fixed).sum() / net_by([month], summer).sum()
            )
            for m in inv_months:
                if region_t and m in regional:
                    continue
                sel = (month == m) & month_rule
                if m in regional:  # pass A only: free accounts keep their summer share
                    cur = part.get(m, 0.0)
                    worst = max(worst, scale(sel, plan_total(m) * free_share, cur, cur))
                    continue
                worst = max(worst, scale(sel, plan_total(m), tot[m], part.get(m, 0.0)))
            if region_t:
                for m in regional:
                    sel = (month == m) & ktn
                    worst = max(
                        worst,
                        scale(sel, ktn_t[m], net_by([month], sel)[m], net_by([month], sel)[m]),
                    )
                    cell = net_by([month, region], every)
                    for reg, absorb in (("South", south_mid), ("West", mehta)):
                        sel = (month == m) & absorb
                        cur = cell[(m, reg)]
                        worst = max(
                            worst, scale(sel, region_t[(m, reg)], cur, net_by([month], sel)[m])
                        )
                    for reg in ("North", "East", "Export"):
                        sel = (month == m) & (region == reg) & ~fixed
                        cur = net_by([month, region], every)[(m, reg)]
                        worst = max(
                            worst, scale(sel, region_t[(m, reg)], cur, net_by([month], sel)[m])
                        )
            if worst < 1e-8:
                return
        detail = ""
        if region_t:
            cell = net_by([month, region], every)
            grp = np.where(
                ktn, "ktn", np.where(south_mid, "south_mid", np.where(mehta, "mehta", "free"))
            )
            parts = pd.Series(amt * in_reg).groupby([month, region, grp]).sum()
            detail = f"\ntargets {region_t}\ncells {cell.loc[list(regional)].round(-3).to_dict()}\n{parts[parts > 0].round(-3)}"
        raise RuntimeError(f"calibration did not converge ({worst:.2e}){detail}")

    run(None)
    nat = net_by([month, region], every)

    def part(m: str, sel: np.ndarray) -> float:
        return float(net_by([month], sel & (month == m)).get(m, 0.0))

    free_s = {m: part(m, (region == "South") & ~ktn & ~south_mid) for m in regional}
    free_w = {m: part(m, (region == "West") & ~mehta) for m in regional}
    region_t: dict[tuple[str, str], float] = {}
    for reg in ("North", "East", "Export"):
        region_t[(sep, reg)] = region_t[(oct_, reg)] = round(
            (nat[(sep, reg)] + nat[(oct_, reg)]) / 2, -3
        )
    nex = sum(region_t[(sep, r)] for r in ("North", "East", "Export"))
    # Room for the two absorbers in September once everything else is fixed.
    room = p1["sep_net_revenue"] - nex - free_s[sep] - free_w[sep] - ktn_t[sep]
    other_drop = -p1["region_change_oct_vs_sep"]["South"] - (ktn_t[sep] - ktn_t[oct_])
    need_mid = other_drop - (free_s[sep] - free_s[oct_]) + 1_000_000  # Oct mids stay ≥ ₹10 L
    mehta_sep = min(part(sep, mehta), room - need_mid)
    if mehta_sep < 300_000:
        raise RuntimeError(f"P1 not plantable for this seed (room {room:.0f}, need {need_mid:.0f})")
    mids_sep = room - mehta_sep
    south_sep = round(free_s[sep] + ktn_t[sep] + mids_sep, -3)
    west_sep = p1["sep_net_revenue"] - nex - south_sep
    ch = p1["region_change_oct_vs_sep"]
    region_t[(sep, "South")], region_t[(sep, "West")] = south_sep, west_sep
    region_t[(oct_, "South")] = south_sep + ch["South"]
    region_t[(oct_, "West")] = west_sep + ch["West"]
    assert abs(sum(region_t[(oct_, r)] for r in co.REGIONS) - p1["oct_net_revenue"]) < 1
    run(region_t)
    assert (amt > 0).all(), "calibration made a negative invoice"
    inv["amt"] = np.round(amt, 2)
    return {"kappa": kappa, "region_targets": {f"{m}|{r}": v for (m, r), v in region_t.items()}}


# ---------------------------------------------------------------------------------------------
# 4a. Lines, currency, GST


def fx_rates(seed: int) -> pd.Series:
    idx = pd.date_range(co.START, co.END, freq="D")
    r = co.stream(seed, "fx")
    steps = r.normal((99.0 - 86.5) / len(idx), 0.17, len(idx))
    return pd.Series(np.round(86.5 + np.cumsum(steps), 4), index=idx)


def currency(inv: pd.DataFrame, fx: pd.Series) -> pd.DataFrame:
    """EUR invoices carry the invoice-date rate; GST 5% on domestic invoices, 0% on exports."""
    rate = fx.reindex(pd.DatetimeIndex(inv["date"])).to_numpy()
    eur = (inv["currency"] == "EUR").to_numpy()
    fc = np.where(eur, np.round(inv["amt"].to_numpy() / rate, 2), np.nan)
    inv["fx_rate"] = np.where(eur, rate, 1.0)
    inv["amount_fc"] = fc
    inv["net"] = np.where(eur, np.round(fc * rate, 2), inv["amt"].to_numpy())
    inv["gst"] = np.where(eur, 0.0, np.round(inv["net"].to_numpy() * 0.05, 2))
    inv["gross"] = np.round(inv["net"] + inv["gst"], 2)
    # Invoice numbers per fiscal year, in date order.
    fy = np.array([fiscal_year(d) for d in inv["date"].to_numpy().astype("datetime64[D]")])
    seq = pd.Series(1, index=inv.index).groupby(fy).cumsum().to_numpy()
    inv["invoice_no"] = [
        f"BI/{y % 100:02d}{(y + 1) % 100:02d}/{s:06d}" for y, s in zip(fy, seq, strict=True)
    ]
    inv["invoice_id"] = np.arange(1, len(inv) + 1)
    return inv


def invoice_lines(
    inv: pd.DataFrame, roster: list[co.Customer], items: list[co.Item], seed: int
) -> pd.DataFrame:
    by = {c.code: c for c in roster}
    surat = [i for i in items if i.plant == "Surat"]
    cbe = [i for i in items if i.plant == "Coimbatore"]
    lines: list[tuple[Any, ...]] = []
    for row in inv.itertuples():
        r = co.stream(seed, "lines", row.invoice_no)
        c = by[row.cust]
        n = int(r.choice([1, 2, 3], p=[0.45, 0.35, 0.2]))
        shares = r.dirichlet([2.0] * n)
        d = pd.Timestamp(row.date)
        shut = SURAT_SHUT[0] <= (d.month, d.day) <= SURAT_SHUT[1]
        amounts = np.round(shares * row.net, 2)
        amounts[-1] = round(row.net - amounts[:-1].sum(), 2)
        for k in range(n):
            plant_items = surat if (r.random() < c.surat_share and not shut) else cbe
            it = plant_items[int(r.integers(0, len(plant_items)))]
            unit = round(it.rate * (1 + r.uniform(-0.03, 0.03)), 2)
            lines.append(
                (
                    row.invoice_id,
                    row.invoice_no,
                    k + 1,
                    it.code,
                    it.plant,
                    round(float(amounts[k]) / unit, 1),
                    unit,
                    float(amounts[k]),
                    row.date,
                )
            )
    return pd.DataFrame(
        lines,
        columns=[
            "invoice_id",
            "invoice_no",
            "line_no",
            "item_code",
            "plant",
            "qty_m",
            "rate",
            "net",
            "date",
        ],
    )


# ---------------------------------------------------------------------------------------------
# 4b. Orders and the order book (P3, P10, P11)


@dataclass
class Book:
    orders: pd.DataFrame  # order_no, cust, order_date, close (inf = open), amount, status, kind
    k_lead: float


INF = np.datetime64("2100-01-01")


def order_book_share(orders: pd.DataFrame, dates: list[np.datetime64]) -> np.ndarray:
    od = orders["order_date"].to_numpy().astype("datetime64[D]")
    close = orders["close"].to_numpy().astype("datetime64[D]")
    amt = orders["amount"].to_numpy()
    cust = orders["cust"].to_numpy()
    out = []
    for d in dates:
        sel = (od <= d) & (close > d)
        s = pd.Series(amt[sel]).groupby(cust[sel]).sum()
        out.append(s.nlargest(4).sum() / s.sum())
    return np.array(out)


def _bisect(f: Any, lo: float, hi: float, target: float, n: int = 40) -> float:
    flo = f(lo)
    for _ in range(n):
        mid = (lo + hi) / 2
        if (f(mid) - target) * (flo - target) > 0:
            lo, flo = mid, f(mid)
        else:
            hi = mid
    return (lo + hi) / 2


def _closest(f: Any, lo: float, hi: float, target: float) -> float:
    """Bisection for a step function (whole days of lateness): the side of the step nearer the
    target, not the midpoint just short of it."""
    x = _bisect(f, lo, hi, target)
    return min((x - 0.01, x + 0.01), key=lambda s: (abs(f(s) - target), s))


def orders(seed: int, inv: pd.DataFrame, roster: list[co.Customer]) -> Book:
    e = EXPECTED["P3"]
    r = co.stream(seed, "orders")
    by = {c.code: c for c in roster}
    n = len(inv)
    seg = inv["segment"].to_numpy()
    cur = inv["currency"].to_numpy()
    top4 = inv["top4"].to_numpy()
    base = np.where(
        seg == "small",
        r.uniform(4, 14, n),
        np.where(cur == "EUR", r.uniform(20, 40, n), r.uniform(10, 24, n)),
    )
    base = np.where(top4, r.uniform(25, 50, n), base)
    inv_date = inv["date"].to_numpy().astype("datetime64[D]")

    def build(k: float) -> pd.DataFrame:
        lead = np.round(np.where(top4, base * k, base)).astype("int64")
        od = inv_date - lead * DAY
        od = np.where(is_sunday(od), od - DAY, od)
        return pd.DataFrame(
            {
                "cust": inv["cust"].to_numpy(),
                "order_date": od,
                "close": inv_date,
                "amount": inv["net"].to_numpy(),
                "invoice_no": inv["invoice_no"].to_numpy(),
                "kind": "invoiced",
            }
        )

    # P10: 37 orders dispatched in Aug–Sep with no invoice, ₹1.9 Cr together.
    mids = [
        c.code
        for c in roster
        if c.segment == "mid"
        and c.currency == "INR"
        and "quiet" not in c.tags
        and c.code not in co.P2_BATCH
    ]
    p10 = EXPECTED["P10"]
    odates = d64("2026-08-01") + np.sort(r.integers(0, 40, p10["orders"])) * DAY
    raw = r.lognormal(0, 0.35, p10["orders"])
    amts = np.round(raw / raw.sum() * p10["value"], 2)
    amts[-1] = round(p10["value"] - amts[:-1].sum(), 2)
    orphans = pd.DataFrame(
        {
            "cust": [mids[i % len(mids)] for i in range(p10["orders"])],
            "order_date": odates,
            "close": INF,
            "amount": amts,
            "invoice_no": None,
            "kind": "orphan",
        }
    )

    # Board orders (Open orders tab rows 4, 5, 6, 88).
    code = {c.name: c.code for c in roster}
    fixed = pd.DataFrame(
        [
            (code["Ravira Exports"], d64("2026-10-02"), 2_160_000.0, "Confirmed"),
            (code["Ravira Exports"], d64("2026-10-03"), 1_080_000.0, "Confirmed"),
            (code["Tessora EU"], d64("2026-10-05"), 3_120_000.0, "In production"),
            (code["Skarfab"], d64("2026-10-29"), 1_640_000.0, "On hold"),
        ],
        columns=["cust", "order_date", "amount", "status"],
    )
    fixed["close"], fixed["invoice_no"], fixed["kind"] = INF, None, "board"

    # A: KTN orders on hold, placed 1 Jul – 25 Oct. B: confirmed top-4 orders 26 Oct – 1 Nov.
    ktn_codes = [code["Ravira Exports"], code["Tessora EU"], code["Skarfab"]]
    a_dates = d64("2026-07-01") + np.round(np.linspace(0, 115, 24)).astype("int64") * DAY
    a_dates = np.where(is_sunday(a_dates), a_dates - DAY, a_dates)
    a_raw = r.lognormal(0, 0.3, 24)
    hold_a = pd.DataFrame(
        {
            "cust": [ktn_codes[i % 3] for i in range(24)],
            "order_date": a_dates,
            "close": INF,
            "invoice_no": None,
            "kind": "hold",
        }
    )
    b_dates = np.array([d64(x) for x in ("2026-10-27", "2026-10-28", "2026-10-30", "2026-10-31")])
    b_raw = r.lognormal(0, 0.2, 4)
    conf_b = pd.DataFrame(
        {
            "cust": code["Merai Apparels"],
            "order_date": b_dates,
            "close": INF,
            "invoice_no": None,
            "kind": "confirmed",
        }
    )

    june = [d64("2026-06-01") + i * DAY for i in range(30)]
    wk, now = d64(e["last_week_date"]), d64("2026-11-01")

    def assemble(k: float, a: float, b: float) -> pd.DataFrame:
        ha = hold_a.assign(amount=np.round(a_raw / a_raw.sum() * a, 2))
        cb = conf_b.assign(amount=np.round(b_raw / b_raw.sum() * b, 2))
        return pd.concat(
            [build(k), orphans, fixed.drop(columns="status"), ha, cb], ignore_index=True
        )

    k = _bisect(
        lambda k: order_book_share(assemble(k, 0, 0), june).mean(), 0.05, 6.0, e["top4_share_june"]
    )
    a = _bisect(
        lambda a: order_book_share(assemble(k, a, 0), [wk])[0], 0.0, 5e8, e["top4_share_last_week"]
    )
    b = _bisect(
        lambda b: order_book_share(assemble(k, a, b), [now])[0], 0.0, 5e8, e["top4_share_now"]
    )
    book = assemble(k, a, b)
    status = {"board": None, "hold": "On hold", "confirmed": "Confirmed", "orphan": "Dispatched"}
    book["fixed_status"] = book["kind"].map(status)
    book.loc[book["kind"] == "board", "fixed_status"] = fixed["status"].to_numpy()
    book = book.sort_values(["order_date", "cust", "amount"], kind="stable").reset_index(drop=True)
    yy = book["order_date"].astype("datetime64[s]").dt.year % 100
    book["order_no"] = [f"SO-{y:02d}{i + 1:06d}" for i, y in enumerate(yy)]
    book["region"] = book["cust"].map(lambda c: by[c].region)
    return Book(book, k)


# ---------------------------------------------------------------------------------------------
# 4c. Dispatch (P6 metres, P7 spread, P14 Surat shutdown)

Q_BEFORE = ([0.0, 0.1, 0.5, 0.9, 1.0], [2.2, 3.1, 4.1, 5.1, 6.2])
Q_AFTER = ([0.0, 0.1, 0.5, 0.9, 1.0], [1.0, 1.6, 2.94, 7.9, 10.0])
Q_SURAT = ([0.0, 0.1, 0.5, 0.9, 1.0], [1.8, 2.6, 3.5, 4.4, 5.4])


def _quantile(u: np.ndarray, q: tuple[list[float], list[float]]) -> np.ndarray:
    return np.interp(u, q[0], q[1])


def dispatch(
    seed: int, inv: pd.DataFrame, lines: pd.DataFrame, book: Book, items: list[co.Item]
) -> pd.DataFrame:
    r = co.stream(seed, "dispatch")
    order_of = book.orders.dropna(subset=["invoice_no"]).set_index("invoice_no")["order_no"]
    d = lines.copy()
    d["order_no"] = d["invoice_no"].map(order_of)
    n = len(d)
    # Some pipeline orders (invoiced 1–14 Nov) leave the plant a few days before invoicing.
    early = (d["date"].to_numpy().astype("datetime64[D]") >= d64("2026-11-01")) & (
        d["date"].to_numpy().astype("datetime64[D]") <= d64("2026-11-14")
    )
    early &= np.isin(
        d["invoice_no"].to_numpy(),
        d["invoice_no"].drop_duplicates().sample(frac=0.35, random_state=7).to_numpy(),
    )
    shift = np.where(early, r.integers(2, 10, n), 0)
    day = d["date"].to_numpy().astype("datetime64[D]") - shift * DAY
    # P10 orphans: their own lines, dispatched 2–6 days after the order (all 30+ days before
    # 31 Oct), never invoiced.
    orph = book.orders[book.orders["kind"] == "orphan"]
    plant_items = {p: [i for i in items if i.plant == p] for p in co.PLANTS}
    extra = []
    for o in orph.itertuples():
        it = plant_items["Surat"][int(r.integers(0, len(plant_items["Surat"])))]
        dd = o.order_date + int(r.integers(2, 7)) * DAY
        if d64("2026-08-10") <= dd <= d64("2026-08-24"):  # Surat is shut (P14): after it
            dd = d64("2026-08-25") + int(r.integers(0, 3)) * DAY
        extra.append((None, o.order_no, it.code, it.plant, round(o.amount / it.rate, 1), dd))
    ex = pd.DataFrame(
        extra, columns=["invoice_no", "order_no", "item_code", "plant", "qty_m", "day"]
    )
    d = pd.DataFrame(
        {
            "invoice_no": d["invoice_no"],
            "order_no": d["order_no"],
            "item_code": d["item_code"],
            "plant": d["plant"],
            "qty_m": d["qty_m"],
            "day": day,
        }
    )
    d = pd.concat([d, ex], ignore_index=True)
    n = len(d)
    secs = r.integers(9 * 3600, 18 * 3600, n)
    at = d["day"].to_numpy().astype("datetime64[s]") + secs.astype("timedelta64[s]")
    cb = (d["plant"] == "Coimbatore").to_numpy()
    after = d["day"].to_numpy().astype("datetime64[D]") >= d64(EXPECTED["P7"]["start"])
    # Stratified draws: within each plant-week (split at the P7 start) the dispatch times
    # cover the quantile curve evenly, so any run of weeks has the planted quantiles.
    group = pd.Series(
        list(zip(d["plant"], week_end(d["day"].to_numpy()).astype(str), after, strict=True))
    )
    rank = group.groupby(group).cumcount().to_numpy()
    size = group.map(group.value_counts()).to_numpy()
    u = r.random(n)
    for g, idx in group.groupby(group).indices.items():
        if g[0] == "Coimbatore":
            u[idx] = (r.permutation(len(idx)) + 0.5) / len(idx)
    del rank, size, g
    days = np.where(
        cb, np.where(after, _quantile(u, Q_AFTER), _quantile(u, Q_BEFORE)), _quantile(u, Q_SURAT)
    )
    d["dispatched_at"] = at
    d["ready_at"] = at - np.round(days * 86400).astype("timedelta64[s]")
    d["dispatch_days"] = days
    d["uom"] = np.where(cb, "YD", "MTR")
    d["dispatch_mtrs"] = np.where(cb, np.round(d["qty_m"] / co.YARD, 1), d["qty_m"])
    d = d.sort_values(["dispatched_at", "order_no"], kind="stable").reset_index(drop=True)
    d["dispatch_key"] = np.arange(1, len(d) + 1)
    return d


# ---------------------------------------------------------------------------------------------
# 4d. Receivables (P2, P4 overdue, DSO)


def receivables(seed: int, inv: pd.DataFrame, roster: list[co.Customer]) -> pd.DataFrame:
    by = {c.code: c for c in roster}
    erp = inv[inv["erp"]].copy()
    r = co.stream(seed, "receivables")
    n = len(erp)
    terms = erp["cust"].map(lambda c: by[c].terms).to_numpy()
    erp["due"] = erp["date"].to_numpy().astype("datetime64[D]") + terms * DAY
    base = r.normal(6.0, 6.0, n)
    tail_u = r.random(n)
    tail_len = r.uniform(75, 125, n)
    rev_oct = EXPECTED["P1"]["oct_net_revenue"]
    oct31 = d64("2026-10-31")
    ravira = by_name(roster, "Ravira Exports")
    is_k = (erp["cust"] == ravira).to_numpy()
    is_p2 = (erp["pin"] == "p2").to_numpy()
    is_key = (erp["segment"] == "key").to_numpy()
    tail_ok = ~is_key & (erp["currency"] == "INR").to_numpy()
    q2_mult = 1.0
    due = erp["due"].to_numpy().astype("datetime64[D]")
    date = erp["date"].to_numpy().astype("datetime64[D]")
    gross = erp["gross"].to_numpy()

    # Ravira stopped paying for invoices from late August (credit hold, ₹0.4 Cr overdue).
    run = d64("2026-11-01")
    k_idx = np.where(is_k & (due < run))[0]
    target_overdue = EXPECTED["P4"]["overdue"]
    cum = np.cumsum(gross[k_idx][::-1])
    take = int(np.searchsorted(cum, target_overdue)) + 1
    k_unpaid = k_idx[::-1][:take]
    k_unpaid_all = np.concatenate([k_unpaid, np.where(is_k & (due >= run))[0]])
    partial = cum[take - 1] - target_overdue  # paid on the oldest unpaid invoice

    def paid_dates(shift: float, q: float) -> np.ndarray:
        late = np.round(base + shift).astype("int64")
        q_eff = np.where(due >= d64("2026-06-14"), q * q2_mult, q)
        tail = (tail_u < q_eff) & tail_ok  # key and export accounts pay on time
        late = np.where(tail, late + np.round(tail_len).astype("int64"), late)
        pd_ = due + late * DAY
        pd_ = np.maximum(pd_, date + DAY)
        pd_ = np.where(is_sunday(pd_), pd_ + DAY, pd_)
        pd_[is_p2] = INF
        k_paid = is_k.copy()
        k_paid[k_unpaid_all] = False
        pd_[k_paid] = np.minimum(pd_[k_paid], d64("2026-10-30"))  # only the hold is overdue
        pd_[k_unpaid_all] = INF
        return pd_

    def share60(pd_: np.ndarray, days: list[np.datetime64]) -> np.ndarray:
        out = []
        for d in days:
            open_ = (date <= d) & (pd_ > d)
            over = open_ & ((d - due).astype("int64") > 60)
            out.append(gross[over].sum() / gross[open_].sum())
        return np.array(out)

    def dso(pd_: np.ndarray) -> float:
        open_ = (date <= oct31) & (pd_ > oct31)
        return (gross[open_].sum() - partial) / rev_oct * 31

    pre = [d64("2026-03-06") + 7 * i * DAY for i in range(22)]  # Fridays Mar–Jul
    post = [d64("2026-08-21") + 7 * i * DAY for i in range(10)]  # Fridays Aug–Oct
    early, late_ = post[:4], post[6:]
    shift, q = 6.0, 0.08
    for _ in range(6):
        q = _bisect(
            lambda x: share60(paid_dates(shift, x), pre).mean(),
            0.0,
            0.5,
            EXPECTED["P2"]["share_over_60_before"],
        )

        def flat(mult: float) -> float:
            nonlocal q2_mult
            q2_mult = mult
            s = share60(paid_dates(shift, q), early + late_)
            return float(s[len(early) :].mean() - s[: len(early)].mean())

        q2_mult = _bisect(flat, 0.2, 6.0, 0.0)  # room below 1: fewer late payers after 14 Jun
        shift = _closest(
            # the root sits near −14 days: the bounds leave room either side (M6)
            lambda s: dso(paid_dates(s, q)),
            -30.0,
            40.0,
            EXPECTED["board_extras"]["dso_days_oct"],
        )
    pd_ = paid_dates(shift, q)
    erp["paid_true"] = pd_
    erp["partial_paid"] = 0.0
    erp.iloc[k_unpaid[-1], erp.columns.get_loc("partial_paid")] = round(partial, 2)
    erp.attrs.update(shift=shift, q=q, share_after=float(share60(pd_, post).mean()), dso=dso(pd_))
    return erp


def by_name(roster: list[co.Customer], name: str) -> str:
    return next(c.code for c in roster if c.name == name)


# ---------------------------------------------------------------------------------------------
# 4e. Costs: COGS (gross margin per metre), freight 5210 (P6), rebates 4410, admin


def week_end(days: np.ndarray) -> np.ndarray:
    """Saturday ending the Sunday–Saturday week (pandas 'W-SAT')."""
    days = np.asarray(days).astype("datetime64[D]")
    dow = (days.astype("int64") + 4) % 7  # Sunday = 0 … Saturday = 6
    return days + (6 - dow) * DAY


def metres_of(dsp: pd.DataFrame) -> np.ndarray:
    return np.where(dsp["uom"] == "YD", dsp["dispatch_mtrs"] * co.YARD, dsp["dispatch_mtrs"])


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    from scipy import stats  # type: ignore[import-untyped]

    return float(stats.spearmanr(a, b).statistic)


def _allocated(
    inv: pd.DataFrame, lw: pd.DataFrame, kappa: float, start: np.datetime64
) -> pd.DataFrame:
    """Credit notes (by the lines of the invoice they credit) and rebates (by the customer's line
    revenue by plant in the month), by week ending and plant: what net revenue loses there."""
    ln = lw[["invoice_id", "plant", "net", "date"]]
    s = ln.groupby(["invoice_id", "plant"])["net"].sum()
    s = (s / s.groupby(level=0).transform("sum")).rename("s").reset_index()
    cn = inv[(inv["cn_frac"] > 0) & (inv["cn_date"].to_numpy().astype("datetime64[D]") >= start)]
    cn = cn.assign(v=np.round(kappa * cn["cn_frac"] * cn["net"], 2))[["invoice_id", "cn_date", "v"]]
    cn = cn.merge(s, on="invoice_id")
    cn_w = (
        cn.assign(
            week=week_end(cn["cn_date"].to_numpy().astype("datetime64[D]")), v=cn["v"] * cn["s"]
        )
        .groupby(["week", "plant"])["v"]
        .sum()
    )
    rb = inv[inv["rebate"] > 0]
    end = (rb["date"].to_numpy().astype("datetime64[M]") + np.timedelta64(1, "M")).astype(
        "datetime64[D]"
    ) - DAY
    rb = rb.assign(
        end=end, month=rb["date"].to_numpy().astype("datetime64[M]"), v=rb["net"] * rb["rebate"]
    )
    rb = rb.groupby(["end", "month", "cust"])["v"].sum().reset_index()
    lc = ln.merge(inv[["invoice_id", "cust"]], on="invoice_id")
    lc = lc.assign(month=lc["date"].to_numpy().astype("datetime64[D]").astype("datetime64[M]"))
    mix = lc.groupby(["cust", "month", "plant"])["net"].sum()
    mix = (mix / mix.groupby(level=[0, 1]).transform("sum")).rename("m").reset_index()
    rb = rb.merge(mix, on=["cust", "month"])
    rb_w = rb.assign(
        week=week_end(rb["end"].to_numpy().astype("datetime64[D]")), v=rb["v"] * rb["m"]
    )
    rb_w = rb_w[rb_w["end"] >= start].groupby(["week", "plant"])["v"].sum()
    tot = cn_w.add(rb_w, fill_value=0.0)
    return tot.unstack(fill_value=0.0)


def costs(
    seed: int, inv: pd.DataFrame, lines: pd.DataFrame, dsp: pd.DataFrame, kappa: float
) -> pd.DataFrame:
    r = co.stream(seed, "costs")
    e6 = EXPECTED["P6"]
    erp_start = d64(co.ERP_START)
    dday = dsp["dispatched_at"].to_numpy().astype("datetime64[D]")
    m = pd.DataFrame(
        {"week": week_end(dday), "plant": dsp["plant"].to_numpy(), "metres": metres_of(dsp)}
    )
    m = m[m["week"] >= erp_start]
    mw = m.groupby(["week", "plant"])["metres"].sum().unstack(fill_value=0.0)
    lw = lines[lines["date"].to_numpy().astype("datetime64[D]") >= erp_start]
    rw = (
        pd.DataFrame(
            {
                "week": week_end(lw["date"].to_numpy().astype("datetime64[D]")),
                "plant": lw["plant"].to_numpy(),
                "net": lw["net"].to_numpy(),
            }
        )
        .groupby(["week", "plant"])["net"]
        .sum()
    )
    rw = rw.unstack(fill_value=0.0).reindex(mw.index, fill_value=0.0)
    # Net revenue by week and plant: credit notes and rebates allocated to plants the way the
    # Ontology's allocation rules do (decided 4 Nov), so margin per metre holds at every grain.
    rw = rw - _allocated(inv, lw, kappa, erp_start).reindex(
        index=mw.index, columns=rw.columns, fill_value=0.0
    )
    weeks = mw.index.to_numpy()
    margin = 42.0 + np.cumsum(r.normal(0, 0.15, len(weeks))) * 0.5 + r.normal(0, 0.5, len(weeks))
    margin = margin - (margin.mean() - 42.0)
    rows: list[tuple[Any, ...]] = []
    for i, w in enumerate(weeks):
        for plant in co.PLANTS:
            cogs = float(rw.loc[w, plant] - margin[i] * mw.loc[w, plant])
            for acct, name, sh in (
                (4100, "Raw material consumed", 0.62),
                (4200, "Direct labour", 0.23),
                (4300, "Power and fuel", 0.15),
            ):
                rows.append((w, acct, name, plant, None, round(cogs * sh, 2), f"Week to {w}"))
    # Freight 5210: tracked metres for 20+ months, then decoupled on 8 Sep 2026 (P6).
    tot = mw.sum(axis=1).to_numpy()
    brk = d64(e6["break_date"])
    before = weeks < brk
    win = before & (weeks >= brk - np.timedelta64(int(e6["months_tracked"] * 30.44), "D"))
    after = (weeks >= brk) & (weeks <= d64("2026-10-31"))
    cv = tot[win].std() / tot[win].mean()
    mbar = tot[before][-12:].mean()
    freight = np.zeros(len(weeks))
    for attempt in range(500):
        rr = co.stream(seed, "freight", attempt)
        f = 4.2 * tot * (1 + rr.normal(0, 0.55 * cv, len(weeks)))
        late = weeks >= brk
        f_after = 4.2 * mbar * (1 + 0.3 * (tot / mbar - 1) + rr.normal(0, 1.4 * cv, len(weeks)))
        f = np.where(late, f_after, f)
        rb, ra = _spearman(f[win], tot[win]), _spearman(f[after], tot[after])
        if 0.86 <= rb <= 0.92 and 0.22 <= ra <= 0.34:
            freight = f
            break
    else:  # pragma: no cover - the seed search always finds one
        raise RuntimeError("could not plant P6")
    fpm = freight / tot
    want = (1 + e6["freight_per_m_change"]) * fpm[before][-12:].mean()
    late = weeks >= brk
    freight = np.where(late, freight * want / fpm[after].mean(), freight)
    for i, w in enumerate(weeks):
        for plant in co.PLANTS:
            share = mw.loc[w, plant] / tot[i]
            rows.append(
                (
                    w,
                    5210,
                    "Freight outward",
                    plant,
                    None,
                    round(float(freight[i] * share), 2),
                    f"Freight week to {w}",
                )
            )
    # Rebates 4410 (deducted from net revenue), posted at month end per customer.
    rb_rows = inv[inv["rebate"] > 0].copy()
    rb_rows["end"] = (
        rb_rows["date"].to_numpy().astype("datetime64[M]") + np.timedelta64(1, "M")
    ).astype("datetime64[D]") - DAY
    reb = rb_rows.assign(v=rb_rows["net"] * rb_rows["rebate"]).groupby(["end", "cust"])["v"].sum()
    for (end, cust), v in reb.items():
        rows.append(
            (end, 4410, "Rebates and discounts", None, cust, round(float(v), 2), "Volume rebate")
        )
    # Admin 5300: monthly, not part of COGS.
    for mth in pd.period_range(co.ERP_START, co.END, freq="M"):
        end = d64(mth.end_time.date())
        rows.append(
            (
                end,
                5300,
                "Administrative expenses",
                None,
                None,
                round(float(r.uniform(9e5, 1.2e6)), 2),
                "Monthly admin",
            )
        )
    ledger = pd.DataFrame(
        rows,
        columns=[
            "entry_date",
            "account_code",
            "account_name",
            "plant",
            "cust",
            "amount",
            "narration",
        ],
    )
    # Gross margin per metre in October = ₹41.6 exactly (board 4d tile).
    gm_target = EXPECTED["board_extras"]["gross_margin_per_m_oct"]
    o0, o1 = d64("2026-10-01"), d64("2026-11-01")
    oct_metres = metres_of(dsp)[(dday >= o0) & (dday < o1)].sum()
    in_oct = (inv["date"].to_numpy().astype("datetime64[D]") >= o0) & (
        inv["date"].to_numpy().astype("datetime64[D]") < o1
    )
    cn_oct = (inv["cn_date"].to_numpy().astype("datetime64[D]") >= o0) & (
        inv["cn_date"].to_numpy().astype("datetime64[D]") < o1
    )
    rebates_oct = ledger.loc[
        (ledger["account_code"] == 4410)
        & (ledger["entry_date"].to_numpy().astype("datetime64[D]") >= o0)
        & (ledger["entry_date"].to_numpy().astype("datetime64[D]") < o1),
        "amount",
    ].sum()
    net_oct = (
        inv["net"].to_numpy()[in_oct].sum()
        - np.round(kappa * inv["cn_frac"].to_numpy() * inv["net"].to_numpy(), 2)[cn_oct].sum()
        - rebates_oct
    )
    cogs_sel = (
        ledger["account_code"].isin([4100, 4200, 4300])
        & (ledger["entry_date"].to_numpy().astype("datetime64[D]") >= o0)
        & (ledger["entry_date"].to_numpy().astype("datetime64[D]") < o1)
    )
    want_cogs = net_oct - gm_target * oct_metres
    have = ledger.loc[cogs_sel, "amount"].sum()
    ledger.loc[cogs_sel, "amount"] = np.round(ledger.loc[cogs_sel, "amount"] * want_cogs / have, 2)
    resid = round(want_cogs - ledger.loc[cogs_sel, "amount"].sum(), 2)
    ledger.loc[ledger.index[cogs_sel][0], "amount"] += resid
    ledger = ledger.sort_values(
        ["entry_date", "account_code", "plant"], kind="stable", na_position="last"
    ).reset_index(drop=True)
    ledger["entry_id"] = np.arange(1, len(ledger) + 1)
    return ledger


# ---------------------------------------------------------------------------------------------
# 4f. Cash (bank ledger), approvals (P8)


def bank(seed: int, rec: pd.DataFrame) -> pd.DataFrame:
    r = co.stream(seed, "bank")
    paid = rec[rec["paid_true"] < INF]
    credits = pd.DataFrame(
        {
            "entry_date": paid["paid_true"].to_numpy().astype("datetime64[D]"),
            "bank": "HDFC current",
            "description": "Receipt " + paid["invoice_no"],
            "debit": 0.0,
            "credit": paid["gross"].to_numpy(),
        }
    )
    part = rec[rec["partial_paid"] > 0]
    credits = pd.concat(
        [
            credits,
            pd.DataFrame(
                {
                    "entry_date": d64("2026-09-30"),
                    "bank": "HDFC current",
                    "description": "Part receipt " + part["invoice_no"],
                    "debit": 0.0,
                    "credit": part["partial_paid"].to_numpy(),
                }
            ),
        ],
        ignore_index=True,
    )
    wk = credits.groupby(week_end(credits["entry_date"].to_numpy().astype("datetime64[D]")))[
        "credit"
    ].sum()
    debits = pd.DataFrame(
        {
            "entry_date": wk.index.to_numpy(),
            "bank": "HDFC current",
            "description": "Supplier and payroll payments",
            "debit": np.round(wk.to_numpy() * 0.985 * r.lognormal(0, 0.05, len(wk)), 2),
            "credit": 0.0,
        }
    )
    months = pd.period_range(co.ERP_START, co.END, freq="M")
    sbi = pd.DataFrame(
        {
            "entry_date": [d64(p.end_time.date()) for p in months],
            "bank": "SBI cash credit",
            "description": "Interest on cash credit",
            "debit": np.round(r.uniform(90_000, 130_000, len(months)), 2),
            "credit": 0.0,
        }
    )
    led = pd.concat([credits, debits, sbi], ignore_index=True)
    led = led[led["entry_date"] >= d64(co.ERP_START)]
    led = led.sort_values(["entry_date", "bank", "credit", "debit"], kind="stable").reset_index(
        drop=True
    )
    led["flow"] = led["credit"] - led["debit"]
    # Opening balances so the closing cash on Sat 31 Oct 2026 is ₹2.1 Cr (board 4d).
    oct31 = d64("2026-10-31")
    target = EXPECTED["board_extras"]["cash_2026_10_31"]
    upto = led["entry_date"].to_numpy().astype("datetime64[D]") <= oct31
    sbi_open = 4_000_000.0
    sbi_flow = led.loc[upto & (led["bank"] == "SBI cash credit"), "flow"].sum()
    hdfc_flow = led.loc[upto & (led["bank"] == "HDFC current"), "flow"].sum()
    hdfc_open = round(target - sbi_open - sbi_flow - hdfc_flow, 2)
    opening = pd.DataFrame(
        {
            "entry_date": d64(co.ERP_START) - DAY,
            "bank": ["HDFC current", "SBI cash credit"],
            "description": "Opening balance",
            "debit": 0.0,
            "credit": [hdfc_open, sbi_open],
            "flow": [hdfc_open, sbi_open],
        }
    )
    led = pd.concat([opening, led], ignore_index=True)
    led["balance"] = np.round(led.groupby("bank")["flow"].cumsum(), 2)
    led["entry_id"] = np.arange(1, len(led) + 1)
    return led.drop(columns="flow")


def approvals(seed: int, roster: list[co.Customer]) -> pd.DataFrame:
    r = co.stream(seed, "approvals")
    e = EXPECTED["P8"]
    days = pd.date_range(co.ERP_START, co.END, freq="D").to_numpy().astype("datetime64[D]")
    counts = r.poisson(9.5 / 30.4, len(days))
    d = np.repeat(days, counts)
    x = r.lognormal(np.log(2.2e5), 0.55, len(d))
    pile = (x >= e["limit"]) & (x < 6.5e5) & (r.random(len(d)) < 0.7)
    x = np.where(pile, r.uniform(4.6e5, 4.99e5, len(d)), x)
    x = np.round(x, -2)
    mids = [c.code for c in roster if c.segment in ("mid", "key")]
    return pd.DataFrame(
        {
            "approval_id": np.arange(1, len(d) + 1),
            "request_date": d,
            "cust": [mids[i] for i in r.integers(0, len(mids), len(d))],
            "discount_amount": x,
            "approval_limit": float(e["limit"]),
            "approved_by_role": np.where(x < e["limit"], "Head of Sales", "Director"),
            "status": np.where(r.random(len(d)) < 0.88, "Approved", "Rejected"),
            "decided_at": d + r.integers(0, 4, len(d)) * DAY,
        }
    )


# ---------------------------------------------------------------------------------------------
# 4g. Deals (Order tracker · Deals): won value leads invoicing by 4 weeks (P12)


def detrended_weekly(inv: pd.DataFrame) -> pd.Series:
    w = (
        pd.Series(inv["net"].to_numpy(), index=pd.DatetimeIndex(inv["date"]))
        .resample("W-SAT")
        .sum()
    )
    return w / w.rolling(13, center=True, min_periods=7).median()


def deals(seed: int, inv: pd.DataFrame, roster: list[co.Customer]) -> pd.DataFrame:
    r = co.stream(seed, "deals")
    lead = EXPECTED["P12"]["lead_weeks"]
    x = detrended_weekly(inv)
    weeks = x.index[
        (x.index >= pd.Timestamp("2023-04-08")) & (x.index <= pd.Timestamp("2026-10-31"))
    ]
    future = x.shift(-lead).reindex(weeks).to_numpy()
    won_value = 42.0 * np.clip(future, 0.2, None) ** 1.5 * r.lognormal(0, 0.06, len(weeks))  # ₹ L
    n_won_total, n_total = 800, 1980
    alloc = won_value / won_value.sum() * (n_won_total - len(weeks))
    n_won = 1 + np.floor(alloc).astype(int)
    rem = n_won_total - n_won.sum()
    n_won[np.argsort(-(alloc - np.floor(alloc)), kind="stable")[:rem]] += 1
    lost_weeks = r.choice(len(weeks), n_total - n_won_total)
    names = [c.name for c in roster if c.status == "active"]
    rows = []
    for i, wk in enumerate(weeks):
        vals = won_value[i] * r.dirichlet([3.0] * n_won[i])
        for v in vals:
            closed = wk - pd.Timedelta(days=int(r.integers(0, 6)))
            rows.append((closed, "Won", round(float(v), 1)))
    for i in lost_weeks:
        closed = weeks[i] - pd.Timedelta(days=int(r.integers(0, 6)))
        rows.append((closed, "Lost", round(float(r.lognormal(np.log(9.0), 0.6)), 1)))
    df = pd.DataFrame(rows, columns=["closed", "outcome", "value_lakh"])
    df = df.sort_values(["closed", "outcome", "value_lakh"], kind="stable").reset_index(drop=True)
    df["customer"] = [names[k] for k in r.integers(0, len(names), len(df))]
    df["created"] = df["closed"] - pd.to_timedelta(r.integers(14, 120, len(df)), unit="D")
    blank = r.choice(len(df), 312, replace=False)
    df.loc[blank, "created"] = pd.NaT
    df["deal_id"] = [f"D-{i + 1:05d}" for i in range(len(df))]
    df["owner"] = r.choice(["PM", "RK", "SV", "AJ"], len(df))
    return df
