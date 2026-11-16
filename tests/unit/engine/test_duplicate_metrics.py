# SPDX-License-Identifier: Apache-2.0
"""Two numbers that are the same number (owner, 12 Nov 2026: Indigo's Net revenue and Invoice net
amount, so Sweep found "South fell ₹1.35 Cr" twice): a Home proposal to keep one. Never removed
on its own; Keep both is remembered."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

from othor.engine.proposals import same_numbers
from othor.engine.store import Point


def _series(metric: str, values: list[str | None], grain: str = "month") -> list[Point]:
    out, d = [], dt.date(2026, 4, 1)
    for v in values:
        out.append(Point(metric, grain, "", None, d, None if v is None else Decimal(v)))
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


SPEC = {
    "metrics": {
        "net_revenue": {"name": "Net revenue", "status": "active", "headline": True},
        "invoice_net_amount": {"name": "Invoice net amount", "status": "active"},
        "orders": {"name": "Orders", "status": "active"},
        "zero_a": {"name": "Zero A", "status": "active"},
        "zero_b": {"name": "Zero B", "status": "active"},
    },
    "forecast": {"goals": []},
}
SAME = ["120", "130", "125", "140", "150", "135", "160"]


def test_the_same_values_for_six_periods_pair_up_and_the_headline_stays():
    pts = _series("net_revenue", SAME) + _series("invoice_net_amount", SAME)
    pts += _series("orders", ["120", "130", "125", "140", "150", "136", "160"])
    ((keep, drop, n, grain),) = same_numbers(SPEC, pts)
    assert (keep, drop, grain) == ("net_revenue", "invoice_net_amount", "month") and n == 7


def test_fewer_than_six_periods_zeros_or_a_gap_never_pair():
    short = _series("net_revenue", SAME[:5]) + _series("invoice_net_amount", SAME[:5])
    zeros = _series("zero_a", ["0"] * 8) + _series("zero_b", ["0"] * 8)
    gap = _series("net_revenue", SAME) + _series("invoice_net_amount", [*SAME[:6], None])
    assert same_numbers(SPEC, short) == [] and same_numbers(SPEC, zeros) == []
    assert same_numbers(SPEC, gap) == []


def test_accept_sets_the_duplicate_aside_and_decline_is_remembered(session_factory, two_companies):
    from othor.core.tenancy import tenant_scope
    from othor.engine import proposals
    from othor.engine.models import Proposal
    from othor.ontology import service
    from othor.ontology.types import validate_body

    cid, _ = two_companies
    now = dt.datetime(2026, 11, 12, 9, tzinfo=dt.UTC)
    body = {
        "name": "Invoice net amount",
        "status": "active",
        "unit": "inr",
        "grain": "month",
        "table": {"source": "erp_db", "schema": "erp_db", "table": "invoice_lines"},
        "measure": {"agg": "sum", "expr": {"col": "net_amount"}},
        "date": "invoice_date",
    }
    with session_factory() as s, tenant_scope(cid):
        service.apply(
            s,
            [service.put("metric", "invoice_net_amount", validate_body("metric", body), {"t": 1})],
            actor="t",
            reason="t",
            allow_empty=True,
        )
        service.confirm(s, actor="t")
        run = SimpleNamespace(id=None)  # the proposal's run is optional
        pts = _series("net_revenue", SAME) + _series("invoice_net_amount", SAME)
        (p,) = proposals.duplicate(s, run, SPEC, pts)
        assert p.text == "Invoice net amount gives the same numbers as Net revenue. Keep one?"
        assert proposals.duplicate(s, run, SPEC, pts) == []  # asked once
        proposals.decide(s, p, accept=True, actor="t", now=now)
        rec = next(r for r in service.current(s) if r.key == "invoice_net_amount")
        assert rec.body["status"] == "declined"  # set aside, not deleted: Undo brings it back
        assert s.query(Proposal).count() == 1
