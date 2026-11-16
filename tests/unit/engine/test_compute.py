# SPDX-License-Identifier: Apache-2.0
"""Inputs → outputs (othor.engine.compute): what replay re-executes. No containers."""

import datetime as dt
import random
from decimal import Decimal

import pytest

from othor.core.tenancy import tenant_scope
from othor.engine.compute import OTHER, Inputs, compute
from othor.engine.store import Point
from othor.ontology.compiler import compile_version
from othor.ontology.compiler.api import canonical

D = dt.date
RUN = D(2026, 11, 2)


def _spec(**metrics):
    return {
        "metrics": metrics,
        "order": sorted(metrics),
        "breakdowns": {"customer": {"name": "Customer", "cap": 50}},
        "forecast": {"headline": []},
        "unmapped_checks": [],
        "sources": {"erp_db": {"id": "x", "kind": "mysql", "dialect": "mysql"}},
    }


def _base(kind="flow", agg="sum", **kw):
    return {"type": "base", "status": "active", "kind": kind, "agg": agg, "source": "erp_db", **kw}


def _inputs(spec, points, status="read"):
    return Inputs(
        RUN,
        "nightly",
        None,
        spec,
        "h",
        {"erp_db": {"status": status, "last_good": "2026-11-01"}},
        points,
    )


def _v(x):
    return Decimal(x).quantize(Decimal("0.000001"))


def test_breakdown_cap_keeps_top_50_by_size_and_sums_the_rest():
    spec = _spec(rev=_base(grain="month"))
    pts = [
        Point("rev", "month", "customer", f"C{i:02d}", D(2026, 10, 1), _v(1000 - i))
        for i in range(60)
    ]
    pts.append(Point("rev", "month", "customer", "C59b", D(2026, 10, 1), _v(941)))  # tie with C59
    out = compute(_inputs(spec, pts)).points
    names = {p.breakdown_value for p in out if p.breakdown == "customer"}
    assert len(names) == 51 and OTHER in names and "C49" in names and "C50" not in names
    other = next(p for p in out if p.breakdown_value == OTHER)
    assert other.value == sum((_v(1000 - i) for i in range(50, 60)), _v(941))


def test_a_non_additive_breakdown_has_no_other():
    spec = _spec(aov=_base(agg="avg", grain="month"))
    pts = [Point("aov", "month", "customer", f"C{i:02d}", D(2026, 10, 1), _v(i)) for i in range(60)]
    out = compute(_inputs(spec, pts)).points
    assert OTHER not in {p.breakdown_value for p in out}
    assert len(out) == 50


def test_snapshot_rollups_take_the_last_day_and_join_backfill_to_daily():
    spec = _spec(rec=_base(kind="snapshot", grain="day"))
    sundays = [D(2026, 10, 11), D(2026, 10, 18), D(2026, 10, 25)]
    pts = [Point("rec", "day", "", None, d, _v(100 + i), True) for i, d in enumerate(sundays)]
    pts.append(Point("rec", "day", "", None, D(2026, 11, 1), _v(200)))  # the first daily
    out = {(p.grain, p.period): p for p in compute(_inputs(spec, pts)).points}
    weeks = [k[1] for k in out if k[0] == "week"]
    assert weeks == [D(2026, 10, 5), D(2026, 10, 12), D(2026, 10, 19), D(2026, 10, 26)]
    assert out[("week", D(2026, 10, 19))].value == _v(102)
    assert out[("week", D(2026, 10, 19))].reconstructed is True
    assert out[("week", D(2026, 10, 26))].value == _v(200)
    assert out[("week", D(2026, 10, 26))].reconstructed is False
    assert out[("month", D(2026, 10, 1))].value == _v(102)  # last October point: 25 Oct


def test_completeness_and_staleness():
    spec = _spec(rev=_base(grain="month"))
    pts = [Point("rev", "month", "", None, D(2026, m, 1), _v(m)) for m in (9, 10, 11)]
    out = {p.period: p for p in compute(_inputs(spec, pts, status="stale")).points}
    assert out[D(2026, 10, 1)].complete and not out[D(2026, 11, 1)].complete
    assert all(p.stale for p in out.values())


def test_baselines_mean_sd_history_and_reconstruction():
    spec = _spec(
        rec=_base(
            kind="snapshot", grain="day", backfill={"as_of_columns": ["invoice_date", "paid_date"]}
        )
    )
    pts = [
        Point("rec", "day", "", None, D(2026, 10, 25) - dt.timedelta(weeks=i), _v(10 + i), True)
        for i in range(11)
    ]
    pts.append(Point("rec", "day", "", None, D(2026, 11, 1), _v(20)))
    b = compute(_inputs(spec, pts)).baselines["rec|day"]
    assert b["history_start"] == "2026-08-16" and b["history_days"] == 78
    assert b["reconstructed_to"] == "2026-08-16"
    assert b["reconstructed_from"] == "invoice_date, paid_date"
    vals = [Decimal(10 + i) for i in range(11)] + [Decimal(20)]
    mean = sum(vals) / 12
    assert Decimal(b["mean"]) == mean.quantize(Decimal("0.000001"))
    assert b["last"] == {"period": "2026-11-01", "value": "20.000000"}


def test_outputs_are_identical_whatever_the_input_order():
    spec = _spec(rev=_base(grain="month"))
    pts = [
        Point("rev", "month", "customer", f"C{i:02d}", D(2026, m, 1), _v(i * m))
        for i in range(70)
        for m in (8, 9, 10)
    ]
    a = canonical(compute(_inputs(spec, pts)).doc)
    random.Random(7).shuffle(pts)
    b = canonical(compute(_inputs(spec, pts)).doc)
    assert a == b


# --- derived metrics through the compiled spec ---------------------------------------------


@pytest.fixture
def spec(world):
    with world.sf() as s, tenant_scope(world.cid):
        doc = compile_version(s, world.company(s)).compiled
        s.commit()
    return doc


def test_derived_share_over_60_follows_backfilled_points(spec):
    pts = []
    for i, d in enumerate([D(2026, 10, 18), D(2026, 10, 25)]):
        pts.append(Point("receivables_over_60", "day", "", None, d, _v(14 + i), True))
        pts.append(Point("receivables_open", "day", "", None, d, _v(100), True))
    pts.append(Point("receivables_over_60", "day", "", None, D(2026, 11, 1), _v(16)))
    pts.append(Point("receivables_open", "day", "", None, D(2026, 11, 1), _v(100)))
    inp = Inputs(
        RUN, "nightly", None, spec, "h", {n: {"status": "read"} for n in spec["sources"]}, pts
    )
    out = {
        (p.grain, p.period): p
        for p in compute(inp).points
        if p.metric == "receivables_share_over_60" and not p.breakdown
    }
    assert out[("day", D(2026, 10, 25))].value == _v("0.15")
    assert out[("day", D(2026, 10, 25))].reconstructed is True
    assert out[("day", D(2026, 11, 1))].reconstructed is False
    assert out[("week", D(2026, 10, 19))].value == _v("0.15")


def test_derived_missing_side_is_not_determinable_never_zero(spec):
    pts = [Point("receivables_over_60", "day", "", None, D(2026, 11, 1), _v(16))]
    inp = Inputs(
        RUN, "nightly", None, spec, "h", {n: {"status": "read"} for n in spec["sources"]}, pts
    )
    out = [
        p
        for p in compute(inp).points
        if p.metric == "receivables_share_over_60" and p.grain == "day" and not p.breakdown
    ]
    assert [p.value for p in out] == [None]
