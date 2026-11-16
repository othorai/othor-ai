# SPDX-License-Identifier: Apache-2.0
"""Summary's figures on the sample company's sealed 1 Nov run (v0.16, M16.2): an area per row,
Net revenue leads Sales, its parts add up, 13 weeks of points, all read from the stored run."""

from __future__ import annotations

import datetime as dt

from othor.core.tenancy import tenant_scope
from othor.web import run_cache
from othor.web import summary_view as sv
from othor.web.ontology_view import run_data
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

TODAY = dt.date(2026, 11, 1)


def test_the_sample_company_summary(night):  # noqa: F811
    _env, _h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        out = sv.page(s, svc.settings, TODAY)
        assert out["empty"] == ""
        areas = [r["area"] for r in out["rows"]]
        assert areas[:5] == ["Sales", "Collections", "Operations", "Finance", "Customers"]
        sales = out["rows"][0]
        assert sales["lead"] == "net_revenue" and sales["grain"] == "week"
        assert len(sales["chart"].series[0].points) == 13
        assert sales["value_text"] and sales["change_text"]
        assert out["as_of"] == "As of last night's run · 1 Nov"
        assert out["view"] == sv.View()
        assert any(k.startswith("region=") for k, _ in out["splits"])

        rd = run_data(s, svc.settings)
        ctx = sv.Ctx(rd.spec, run_cache.by_metric(rd.files.series), sv.View())
        ex = sv.expand(ctx, [m for m, _ in sales["metrics"]], "net_revenue")
        parts = [p["metric"] for p in ex["parts"]]
        assert parts == ["invoiced_value", "credit_note_value", "rebates"]
        assert sum(p["sign"] * p["value"] for p in ex["parts"]) == sales["value"]
