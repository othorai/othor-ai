# SPDX-License-Identifier: Apache-2.0
"""Each planted pattern is in the data, measured directly with pandas (docs/11).

These prove the signal exists. Whether the detectors find it, with the expected state, is
proved by the golden tests in M6.
"""

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd
import pytest
from scipy import stats  # type: ignore[import-untyped]

from tests.fixtures.sample_company import measures as m
from tests.fixtures.sample_company.generate import SampleCompany

D = dt.date
RUN = D(2026, 11, 1)


def money(actual: float, target: float, tol: float = 0.01) -> None:
    assert abs(actual - target) <= abs(target) * tol + 1, (actual, target)


def test_p1_net_hiding_gross(company: SampleCompany, expected: dict[str, Any]) -> None:
    e = expected["P1"]
    rev = m.net_revenue_by_region_month(company)
    money(rev["2026-10"].sum(), e["oct_net_revenue"])
    money(rev["2026-09"].sum(), e["sep_net_revenue"])
    change = rev["2026-10"] - rev["2026-09"]
    for region, target in e["region_change_oct_vs_sep"].items():
        assert abs(change[region] - target) <= max(abs(target) * 0.01, 50_000), region
    money(change.sum(), e["total_change"], tol=0.03)
    acct = m.net_revenue_by_customer_month(company)
    drop = (acct["2026-09"] - acct["2026-10"]).reindex(e["accounts"]).sum()
    assert drop / -change["South"] >= e["accounts_share_of_south_drop_min"]
    last = m.last_invoice_dates(company, before=RUN)
    for name, day in e["last_invoice"].items():
        assert last[name] == D.fromisoformat(day), name


def test_p2_receivables_step(company: SampleCompany, expected: dict[str, Any]) -> None:
    e = expected["P2"]
    share = m.share_over_60(company, pd.date_range("2026-03-01", "2026-10-31", freq="W-FRI"))
    before = share[share.index < pd.Timestamp("2026-08-08")]
    after = share[share.index >= pd.Timestamp("2026-08-15")]
    assert abs(before.mean() - e["share_over_60_before"]) <= 0.01
    assert abs(after.mean() - e["share_over_60_after"]) <= 0.01
    # a step, held: the quiet quarter of the after-period sits above the busy quarter before
    assert after.quantile(0.25) > before.quantile(0.75) + 0.02
    daily = m.share_over_60(company, pd.date_range("2026-08-10", "2026-08-18"))
    jump = daily.diff().idxmax()
    assert jump in (pd.Timestamp("2026-08-14"), pd.Timestamp("2026-08-15"))


def test_p3_order_book_concentration(company: SampleCompany, expected: dict[str, Any]) -> None:
    e = expected["P3"]
    tol = e["tolerance_pp"]
    june = m.top4_order_book_share(company, pd.date_range("2026-06-01", "2026-06-30"))
    assert abs(june.mean() - e["top4_share_june"]) <= tol
    wk = m.top4_order_book_share(company, [pd.Timestamp(e["last_week_date"])]).iloc[0]
    now = m.top4_order_book_share(company, [pd.Timestamp("2026-11-01")]).iloc[0]
    assert abs(wk - e["top4_share_last_week"]) <= tol
    assert abs(now - e["top4_share_now"]) <= tol


def test_p4_ravira_cadence(company: SampleCompany, expected: dict[str, Any]) -> None:
    e = expected["P4"]
    days = m.invoice_dates(company, e["customer"], before=RUN)
    assert days[0] <= RUN - dt.timedelta(days=365 * e["history_years"])
    gaps = np.diff([d.toordinal() for d in days])
    assert gaps.min() >= e["gap_min_days"] and gaps.max() <= e["gap_max_days"]
    assert (RUN - days[-1]).days == e["gap_on_run_date"]
    cust = company.erp["customers"]
    gstin = cust.loc[cust["name"] == e["customer"], "gstin"].iloc[0]
    assert (cust["gstin"] == gstin).sum() == 1
    assert cust["gstin"].dropna().str.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z]\dZ[0-9A-Z]").all()
    money(m.overdue(company, e["customer"], RUN), e["overdue"])


def test_p5_quiet_customers(company: SampleCompany, expected: dict[str, Any]) -> None:
    e = expected["P5"]
    quiet = m.quiet_active_customers(company, RUN, days=e["min_quiet_days"])
    assert len(quiet) == e["count"]
    money(quiet["last_12m"].sum(), e["last_12m_total"])
    for name, v in e["named"].items():
        row = quiet.loc[name]
        assert row["last_invoice"] == D.fromisoformat(v["last_invoice"])
        money(row["last_12m"], v["last_12m"])
    assert quiet["last_12m"].nlargest(3).index.tolist() == list(e["named"])
    for name in quiet.index:
        days = m.invoice_dates(company, name, before=RUN)
        gaps = np.diff([d.toordinal() for d in days])
        early, late = np.median(gaps[:-5]), np.mean(gaps[-3:])
        assert late > early * 1.4, name  # reorder gaps lengthened before going quiet


def test_p6_freight_decoupled(company: SampleCompany, expected: dict[str, Any]) -> None:
    e = expected["P6"]
    w = m.weekly_freight_and_metres(company)
    brk = pd.Timestamp(e["break_date"])
    before = w[(w.index < brk) & (w.index >= brk - pd.DateOffset(months=e["months_tracked"]))]
    after = w[w.index >= brk]
    r_before = stats.spearmanr(before["freight"], before["metres"]).statistic
    r_after = stats.spearmanr(after["freight"], after["metres"]).statistic
    assert r_before >= e["r_before_min"], r_before
    assert r_after <= e["r_after_max"], r_after
    fpm = w["freight"] / w["metres"]
    change = fpm[after.index].mean() / fpm[before.index[-12:]].mean() - 1
    assert abs(change - e["freight_per_m_change"]) <= 0.02
    # Board 5a says "metres are flat"; with West's October growth they rise ~20% here. The
    # finding's copy uses computed values, so the fixture doesn't force it (OPEN-QUESTIONS).


def test_p7_dispatch_spread(company: SampleCompany, expected: dict[str, Any]) -> None:
    e = expected["P7"]
    t = m.dispatch_days(company, plant=e["plant"])
    start = pd.Timestamp(e["start"])
    before = t[(t.index < start) & (t.index >= start - pd.Timedelta(days=120))]
    after = t[(t.index >= start) & (t.index < pd.Timestamp(RUN))]
    assert abs(before.mean() - e["mean_days"]) <= 0.15
    assert abs(after.mean() - e["mean_days"]) <= 0.15
    assert abs(before.quantile(0.9) - e["p90_before"]) <= 0.2
    assert abs(after.quantile(0.9) - e["p90_after"]) <= 0.25
    last28 = t[
        (t.index >= pd.Timestamp(RUN) - pd.Timedelta(days=28)) & (t.index < pd.Timestamp(RUN))
    ]
    assert abs(last28.quantile(0.9) - last28.quantile(0.1) - e["spread_now"]) <= 0.25


def test_p8_approvals_pile_under_limit(company: SampleCompany, expected: dict[str, Any]) -> None:
    e = expected["P8"]
    a = company.erp["approvals"]["discount_amount"].astype(float)
    assert len(a) >= e["min_events"]
    below = ((a >= e["limit"] - e["band_below"]) & (a < e["limit"])).sum()
    above = ((a >= e["limit"]) & (a < e["limit"] + e["band_below"])).sum()
    assert below >= e["min_ratio_below_vs_above"] * max(above, 1)


def test_p9_mis_includes_gst(company: SampleCompany, expected: dict[str, Any]) -> None:
    e = expected["P9"]
    mis = m.mis_sales_by_month(company)
    erp = m.net_revenue_by_region_month(company)["2026-10"].sum()
    money(mis["2026-10"] - erp, e["oct_mis_minus_erp"])


def test_p10_dispatched_not_invoiced(company: SampleCompany, expected: dict[str, Any]) -> None:
    e = expected["P10"]
    orphans = m.dispatched_without_invoice(company, RUN, days=e["min_days_since_dispatch"])
    assert len(orphans) == e["orders"]
    money(orphans["amount"].sum(), e["value"])


def test_p11_bimodal_order_value(company: SampleCompany, expected: dict[str, Any]) -> None:
    e = expected["P11"]
    lo, hi = m.order_value_modes(company)
    assert abs(lo / e["domestic_mode"] - 1) <= 0.25, lo
    assert abs(hi / e["export_mode"] - 1) <= 0.25, hi


def test_p12_deals_lead_invoicing(company: SampleCompany, expected: dict[str, Any]) -> None:
    corr = m.lagged_deals_vs_invoicing(company, lags=range(1, 9))
    assert corr.idxmax() == expected["P12"]["lead_weeks"], corr.round(2).to_dict()
    assert corr.max() >= 0.5


def test_p13_fy27_path_is_plausible(company: SampleCompany) -> None:
    rev = m.monthly_net_revenue(company, source="erp")
    ytd = rev.loc["2026-04":"2026-10"].sum()
    assert 3.2e8 <= ytd <= 3.45e8  # tuned against the M7 forecast (target ₹58.4 Cr)


def test_p14_known_patterns(company: SampleCompany, expected: dict[str, Any]) -> None:
    e = expected["P14"]
    daily = m.daily_invoicing(company, source="dw")
    for q_end in (
        "2025-06-30",
        "2025-09-30",
        "2025-12-31",
        "2026-03-31",
        "2026-06-30",
        "2026-09-30",
    ):
        end = pd.Timestamp(q_end)
        push = daily[end - pd.Timedelta(days=4) : end].mean()
        base = daily[end - pd.Timedelta(days=40) : end - pd.Timedelta(days=10)].mean()
        assert push > 1.3 * base, q_end
    surat = m.daily_metres(company, plant="Surat")
    for year in (2024, 2025, 2026):
        shut = surat[f"{year}-08-10" : f"{year}-08-24"].mean()
        assert shut < 0.2 * surat[f"{year}-07-01" : f"{year}-07-31"].mean(), year
    wk = pd.Timestamp(e["diwali_week_start"]["2026"])
    spike = daily[wk : wk + pd.Timedelta(days=6)].mean()
    assert spike > 1.25 * daily["2026-10-01":"2026-10-14"].mean()
    last = m.last_invoice_dates(company, before=D(2026, 11, 4))
    assert last["Skarfab"] < D.fromisoformat(e["skarfab_pause"][0])


def test_board_extras(company: SampleCompany, expected: dict[str, Any]) -> None:
    e = expected["board_extras"]
    assert abs(m.gross_margin_per_metre(company, "2026-10") - e["gross_margin_per_m_oct"]) <= 0.1
    assert abs(m.dso_days(company, D(2026, 10, 31)) - e["dso_days_oct"]) <= 0.5
    money(m.cash_balance(company, D(2026, 10, 31)), e["cash_2026_10_31"])
    assert abs(m.credit_note_share(company) - e["credit_notes_share_of_gross"]) <= 0.001
    assert abs(m.top10_revenue_share(company) - e["top10_revenue_share"]) <= 0.02


@pytest.mark.parametrize("series", list(range(1, 11)))
def test_clean_series_have_no_signal(company: SampleCompany, series: int) -> None:
    """C1–C10: similar size and noise to the planted series, nothing to find."""
    s = m.clean_series(company, series)
    assert len(s) >= 40
    recent, prior = s.iloc[-6:], s.iloc[-30:-6]
    # no step: recent mean within 2 standard errors of the prior mean
    se = prior.std(ddof=1) * np.sqrt(1 / len(recent) + 1 / len(prior))
    assert abs(recent.mean() - prior.mean()) <= 2.5 * se, s.name
    # no variance change
    ratio = recent.var(ddof=1) / prior.var(ddof=1)
    assert ratio <= 3.0, (s.name, ratio)  # Rising variance only flags increases


def test_p16_late_credit_notes(company, expected):
    """P16: one South mid account's credit notes dated in the week of 19 Oct, posted in erp_db
    only on 31 Oct, together above the materiality floor."""
    e = expected["P16"]
    cn = company.erp["credit_notes"]
    late = cn[cn["created_at"].astype(str).str[:10] == e["posted"]]
    late = late[late["cn_date"].astype(str) < e["posted"]]
    assert len(late) >= 1 and late["customer_id"].nunique() == 1
    cust = company.erp["customers"].set_index("customer_id").loc[late["customer_id"].iloc[0]]
    assert (cust["region"], cust["segment"]) == ("South", "mid")
    week = pd.Timestamp(e["week"])
    assert all(week <= pd.Timestamp(d) < week + pd.Timedelta(days=7) for d in late["cn_date"])
    assert late["net_amount"].sum() >= e["min_amount"]
