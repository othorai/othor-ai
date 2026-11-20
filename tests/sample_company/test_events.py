# SPDX-License-Identifier: Apache-2.0
"""The sample company's events from 23 Nov 2026 and its horizon to 31 Mar 2027
(tests/fixtures/sample_company/events.py, docs/plans/sample-demo-events.md).

The guard first: the data as of every date up to 22 Nov is byte for byte what the generator gave
before the events (hashes taken from it on 20 Nov 2026). The goldens, the e2e nights, the 1 Nov
fixture and production's past nights all read those dates. If one moves, move the event, never
the hashes. Then each event is checked in the data the way a direct SQL check would see it.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.fixtures.sample_company import events as ev
from tests.fixtures.sample_company.generate import SampleCompany, generate

D = dt.date
BEFORE = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures/sample_company/hashes_before_events.json"
    ).read_text()
)


@pytest.mark.parametrize("day", sorted(BEFORE))
def test_nothing_before_the_seam_moves(day: str) -> None:
    assert generate(as_of=D.fromisoformat(day)).data_hash() == BEFORE[day], day


def test_the_hashes_reach_the_seam() -> None:
    days = sorted(D.fromisoformat(x) for x in BEFORE)
    assert days[-1] == D(2026, 11, 22) == ev.SEAM.astype(object) - dt.timedelta(days=1)
    assert D(2026, 7, 25) in days  # the 100-night window's first night


@pytest.fixture(scope="module")
def end() -> SampleCompany:
    return generate(as_of=D(2027, 4, 1))


def _cust(c: SampleCompany, name: str) -> int:
    t = c.erp["customers"]
    return int(t.loc[t["name"] == name, "customer_id"].iloc[0])


def _dates(c: SampleCompany, name: str) -> list[D]:
    inv = c.erp["invoices"]
    return sorted(inv.loc[inv["customer_id"] == _cust(c, name), "invoice_date"])


def test_the_horizon_runs_to_31_march(end: SampleCompany) -> None:
    inv = end.erp["invoices"]
    assert max(inv["invoice_date"]) >= D(2027, 3, 25)
    month = pd.to_datetime(inv["invoice_date"]).dt.strftime("%Y-%m")
    net = inv.groupby(month)["net_amount"].sum()
    for m in ("2027-01", "2027-02", "2027-03"):  # each month near its plan (₹4.5–5.6 Cr)
        assert 4.4e7 < net[m] < 5.8e7, (m, net[m])
    fx = end.erp["fx_rates"]
    assert max(fx["rate_date"]) == D(2027, 3, 31)


def test_numbers_stay_whole(end: SampleCompany) -> None:
    inv = end.erp["invoices"]
    assert inv["invoice_id"].is_unique and inv["invoice_no"].is_unique
    assert (np.diff(inv["invoice_id"].to_numpy()) > 0).all()
    assert (
        np.diff(pd.to_datetime(inv["invoice_date"]).to_numpy()) >= np.timedelta64(0, "ns")
    ).all()
    fy27 = inv[inv["invoice_no"].str.startswith("BI/2627/")]["invoice_no"].str[-6:].astype(int)
    assert list(fy27) == list(range(1, len(fy27) + 1))  # one series, no gaps
    lines = end.erp["invoice_lines"].groupby("invoice_id")["net_amount"].sum()
    assert np.allclose(lines.reindex(inv["invoice_id"]).to_numpy(), inv["net_amount"], atol=0.02)
    rec = end.erp["receivables"]
    assert len(rec) == len(inv) and set(rec["invoice_id"]) == set(inv["invoice_id"])
    fo = end.dw["sales_dw"]["fact_order"]
    assert fo["order_no"].is_unique
    fd = end.dw["sales_dw"]["fact_dispatch"]
    assert fd["dispatch_key"].is_unique and fd["order_no"].dropna().isin(fo["order_no"]).all()
    bank = end.erp["bank_ledger"]
    for _name, g in bank.groupby("bank"):
        flow = (g["credit"] - g["debit"]).to_numpy()
        assert np.allclose(np.diff(g["balance"].to_numpy()), flow[1:], atol=0.05)


def test_e1_ravira_invoices_again_and_clears_its_overdue_on_4_january() -> None:
    before, after = generate(as_of=D(2026, 11, 24)), generate(as_of=D(2026, 11, 25))
    assert max(_dates(before, "Ravira Exports")) == D(2026, 10, 9)
    assert max(_dates(after, "Ravira Exports")) == ev.E1_RAVIRA_BACK
    for day, status in ((D(2026, 12, 20), {"O"}), (ev.J1_PAID + dt.timedelta(days=1), {"P"})):
        rec = generate(as_of=day).erp["receivables"]
        mine = rec[rec["customer_id"] == _cust(after, "Ravira Exports")]
        old = mine[
            (mine["invoice_date"] >= D(2026, 9, 11)) & (mine["invoice_date"] < D(2026, 11, 1))
        ]
        assert set(old["status"]) == status, day


def test_e2_merai_places_no_orders_until_1_february(end: SampleCompany) -> None:
    fo = end.dw["sales_dw"]["fact_order"]
    key = end.dw["sales_dw"]["dim_customer"]
    merai = int(key.loc[key["customer_name"] == "Merai Apparels", "customer_key"].iloc[0])
    days = sorted(fo.loc[fo["customer_key"] == merai, "order_date"])
    gap = [d for d in days if ev.E2_MERAI_STOPS <= d < ev.J2_MERAI_BACK]
    assert not gap
    assert any(d >= ev.J2_MERAI_BACK for d in days)


def test_e3_six_silent_customers_marked_inactive() -> None:
    on, after = generate(as_of=D(2026, 12, 1)), generate(as_of=D(2026, 12, 2))
    for c in (on, after):
        assert len(c.erp["customers"]) == len(on.erp["customers"])
    st = after.erp["customers"].set_index("name")["status"]
    assert (st[list(ev.E3_INACTIVE)] == "inactive").all()
    assert (on.erp["customers"].set_index("name")["status"][list(ev.E3_INACTIVE)] == "active").all()
    dim = after.dw["sales_dw"]["dim_customer"].set_index("customer_name")["is_active"]
    assert not dim[list(ev.E3_INACTIVE)].any()


def test_e5_raw_material_steps_up(end: SampleCompany) -> None:
    lg = end.erp["cost_ledger"]
    rm = lg[lg["account_code"] == 4100]
    sales = end.erp["invoice_lines"].merge(
        end.erp["invoices"][["invoice_id", "invoice_date"]], on="invoice_id"
    )

    def share(lo: D, hi: D) -> float:
        cost = rm[(rm["entry_date"] >= lo) & (rm["entry_date"] < hi)]["amount"].sum()
        s = sales[(sales["invoice_date"] >= lo) & (sales["invoice_date"] < hi)]["net_amount"].sum()
        return float(cost / s)

    assert share(D(2026, 12, 5), D(2027, 1, 30)) > 1.12 * share(D(2026, 9, 26), D(2026, 11, 21))


def test_e6_late_payers_pass_60_days_and_pay_on_4_january() -> None:
    def over60(day: D) -> float:
        ar = generate(as_of=day).dw["public"]["ar_open_items"]
        return float(ar.loc[ar["days_overdue"] > 60, "amount_open"].sum())

    seam, dec, jan = over60(D(2026, 11, 23)), over60(D(2026, 12, 21)), over60(D(2027, 1, 6))
    assert dec > seam + 2.5e6, (seam, dec)
    assert jan < dec - 2e6, (dec, jan)
    rec = generate(as_of=D(2027, 1, 5)).erp["receivables"]
    paid = rec[(rec["paid_date"] == ev.J1_PAID) & (rec["due_date"] <= ev.E6_DUE[1])]
    assert len(paid) >= 10


def test_e7_coimbatore_settles_back() -> None:
    fd = generate(as_of=D(2027, 1, 1)).dw["sales_dw"]["fact_dispatch"]
    cbe = fd[fd["plant"] == "Coimbatore"]
    days = (cbe["dispatched_at"] - cbe["ready_at"]).dt.total_seconds() / 86400
    when = pd.to_datetime(cbe["dispatch_date"])
    wide = days[(when >= "2026-10-01") & (when < "2026-11-20")]
    calm = days[(when >= "2026-12-11") & (when < "2027-01-01")]
    assert calm.std() < 0.6 * wide.std()
    assert abs(calm.mean() - wide.mean()) < 1.5


def test_e9_a_new_export_customer() -> None:
    before, after = generate(as_of=D(2026, 12, 18)), generate(as_of=D(2026, 12, 27))
    assert "Nordvik Tekstil" not in set(before.erp["customers"]["name"])
    assert "Nordvik Tekstil" in set(after.dw["sales_dw"]["dim_customer"]["customer_name"])
    key = after.dw["sales_dw"]["dim_customer"]
    k = int(key.loc[key["customer_name"] == "Nordvik Tekstil", "customer_key"].iloc[0])
    fo = after.dw["sales_dw"]["fact_order"]
    mine = fo[fo["customer_key"] == k]
    assert len(mine) == 5 and (mine["status"] != "Invoiced").all()
    assert mine["amount"].sum() > 1.5e7


def test_j4_surat_stops_for_a_week(end: SampleCompany) -> None:
    fd = end.dw["sales_dw"]["fact_dispatch"]
    surat = fd[(fd["plant"] == "Surat")]
    lo, hi = ev.J4_SURAT_STOP
    assert not ((surat["dispatch_date"] >= lo) & (surat["dispatch_date"] <= hi)).any()


def test_j5_tessora_back_and_seed_7_still_builds(end: SampleCompany) -> None:
    assert min(d for d in _dates(end, "Tessora EU") if d > D(2026, 10, 9)) >= ev.J5_TESSORA_BACK
    assert generate(seed=7, as_of=D(2027, 2, 1)).erp["invoices"]["invoice_id"].is_unique
