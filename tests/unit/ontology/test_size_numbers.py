# SPDX-License-Identifier: Apache-2.0
"""Numbers suggested for the company's size and goals (#76, docs/plans/next-1126.md §3 steps 3–8):
what can be built (from the company's numbers, from a scanned table, or needing a source), the
ranking (a goal that is off track first, at most three and one that needs a source), the
proposals (one computed figure each; Not now stays away until the evidence changes), and Accept
drafting a metric record that validates."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import sqlalchemy as sa

from othor.ai.guards import numerals
from othor.core.tenancy import tenant_scope
from othor.engine import proposals
from othor.engine.models import Proposal
from othor.engine.store import Point
from othor.ontology import catalogue, goal_link, size, size_numbers
from othor.ontology.compiler.catalog import Catalog, ColumnInfo, TableInfo
from othor.ontology.types import MetricBody
from tests.unit.ontology.test_size import RUN, months, revenue, spec

CAT = catalogue.load()


def _points() -> list[Point]:
    pts = revenue(Decimal("4e7"))
    for p in months():
        pts.append(Point("cogs", "month", "", None, p, Decimal("2.4e7")))
        pts.append(Point("active_customers", "month", "", None, p, Decimal(40)))
        pts.append(Point("collections", "month", "", None, p, Decimal("3.6e7")))
        pts.append(Point("invoiced_value", "month", "", None, p, Decimal("4e7")))
        for i in range(12):
            v = Decimal("1e7") if i < 2 else Decimal("2e6")
            pts.append(Point("net_revenue", "month", "customer", f"C{i:02d}", p, v))
    return pts


def _spec(goals: list[dict] | None = None) -> dict:
    s = spec()
    s["metrics"]["collections"] = {
        "name": "Collections",
        "status": "active",
        "unit": "inr",
        "area": "Collections",
    }
    s["metrics"]["invoiced_value"] = {"name": "Invoiced value", "status": "active", "unit": "inr"}
    s["metrics"]["receivables_over_60"] = {"name": "Receivables over 60 days", "status": "active"}
    s["metrics"]["net_revenue"]["area"] = "Sales"
    s["forecast"] = {"goals": goals or []}
    return s


def _catalog(*tables: tuple[str, dict[str, str], int]) -> Catalog:
    c = Catalog()
    for name, cols, rows in tables:
        c.tables[("erp_db", "erp_db", name)] = TableInfo(
            "id",
            "erp_db",
            "postgres",
            "erp_db",
            name,
            name,
            "dimension",
            False,
            {k: ColumnInfo(k, v, False) for k, v in cols.items()},
            row_count=rows,
        )
    return c


def _size(s: dict | None = None) -> dict:
    got = size.of(s or _spec(), _points(), RUN)
    assert got is not None
    return got


def test_what_can_be_built_now_from_a_table_or_needs_a_source() -> None:
    s = _spec()
    cat = _catalog(
        ("stock_ledger", {"id": "int", "qty": "decimal", "as_of": "date"}, 900),
        ("customers", {"customer_id": "int", "name": "text"}, 50),  # no date: can't count new
    )
    got = {c.key: c for c in size_numbers.candidates(s, _size(s), cat)}
    assert "receivables_over_60" not in got  # they have it already
    assert {k: c.status for k, c in got.items()} == {
        "top10_share": "now",
        "gross_margin_pct": "now",
        "revenue_per_active_customer": "now",
        "collected_vs_invoiced": "now",
        "stock_days": "table",
        "new_customers": "source",
    }
    assert got["stock_days"].table == "erp_db · stock_ledger"
    # a table with no rows, or ignored, isn't one to build from
    empty = _catalog(("stock_ledger", {"qty": "decimal", "as_of": "date"}, 0))
    assert {c.key: c.status for c in size_numbers.candidates(s, _size(s), empty)}[
        "stock_days"
    ] == "source"


def test_a_part_missing_means_it_needs_a_source_never_a_draft() -> None:
    s = _spec()
    del s["metrics"]["cogs"]
    got = {c.key: c for c in size_numbers.candidates(s, _size(s), Catalog())}
    assert got["gross_margin_pct"].status == "source"
    assert got["gross_margin_pct"].missing == ["cost_of_goods"]
    ev = {"status": "source", "build": got["gross_margin_pct"].entry.build}
    assert size_numbers.record(ev, {}, set()) is None


def _ranked(goals: list[goal_link.GoalState], s: dict | None = None) -> list[str]:
    s = s or _spec()
    sz = _size(s)
    cands = size_numbers.candidates(s, sz, Catalog())
    size_numbers.link_goals(cands, sz["parts"], goals)
    return [c.key for c in size_numbers.pick(cands)]


def test_the_band_order_without_goals_three_and_one_that_needs_a_source() -> None:
    assert _ranked([]) == [
        "top10_share",
        "gross_margin_pct",
        "revenue_per_active_customer",
        "stock_days",  # needs a source: one, after the three
    ]


def test_a_goal_that_is_off_track_brings_the_number_serving_it_first() -> None:
    goals = [{"key": "collect", "name": "Collect ₹4 Cr a month", "metric": "collections"}]
    s = _spec(goals)
    states = goal_link.states(s, [{"key": "collect", "on_track": False}])
    assert states[0].state == "off_track" and states[0].behind
    assert _ranked(states, s)[:2] == ["collected_vs_invoiced", "top10_share"]
    # behind beats any goal: revenue's goal on track, collections' off track
    both = [*goals, {"key": "grow", "name": "Grow", "metric": "net_revenue"}]
    s2 = _spec(both)
    st = goal_link.states(
        s2, [{"key": "collect", "on_track": False}, {"key": "grow", "on_track": True}]
    )
    assert _ranked(st, s2)[:3] == ["collected_vs_invoiced", "top10_share", "gross_margin_pct"]
    st = goal_link.states(
        s2, [{"key": "collect", "on_track": True}, {"key": "grow", "on_track": False}]
    )
    assert _ranked(st, s2)[:3] == ["top10_share", "gross_margin_pct", "revenue_per_active_customer"]
    # the goal test's at risk counts as behind too
    risky = goal_link.states(s, [], at_risk=["collect"])
    assert risky[0].state == "at_risk" and _ranked(risky, s)[0] == "collected_vs_invoiced"


def test_the_goal_link_by_number_then_by_area() -> None:
    g = [
        goal_link.GoalState("a", "A", "net_revenue", "on_track", "Sales"),
        goal_link.GoalState("b", "B", "dso", "off_track", "Collections"),
    ]
    assert goal_link.served(g, ["net_revenue", "cogs"], "Finance").key == "a"
    assert goal_link.served(g, ["collections"], "Collections").key == "b"
    assert goal_link.served(g, ["cash"], "Operations") is None


def test_each_number_drafts_a_record_that_validates() -> None:
    s = _spec()
    sz = _size(s)
    for c in size_numbers.candidates(s, sz, Catalog()):
        if c.status != "now":
            continue
        ev = size_numbers.evidence(c, 0, sz, s, CAT, RUN)
        key, body = size_numbers.record(ev, s["metrics"], set())
        MetricBody.model_validate(body)  # the formula is one the compiler knows
        assert key == c.key
    top = next(c for c in size_numbers.candidates(s, sz, Catalog()) if c.key == "top10_share")
    ev = size_numbers.evidence(top, 0, sz, s, CAT, RUN)
    assert size_numbers.record(ev, {}, {"top10_share"})[0] == "top10_share_2"
    assert size_numbers.record(ev, {}, set())[1]["formula"] == {
        "fn": "top_share",
        "args": [
            {"metric": "net_revenue", "missing": "not_determinable"},
            {"lit": "customer"},
            {"lit": 10},
        ],
    }


def test_the_line_is_the_catalogues_words_and_one_computed_figure() -> None:
    s = _spec()
    sz = _size(s)
    c = next(x for x in size_numbers.candidates(s, sz, Catalog()) if x.key == "top10_share")
    ev = size_numbers.evidence(c, 0, sz, s, CAT, RUN)
    assert ev["figure_parts"] == [
        "Your 10 largest customers carried ",
        "90%",
        " of net revenue in the last 12 months, across 12 customers",
    ]
    assert ev["line"].startswith("Your 10 largest customers carried 90% of net revenue")
    # every number on the line is the figure's, the count, or the catalogue's own words (10, 12)
    assert numerals(ev["line"]) <= {"10", "90", "12"}
    assert numerals(ev["reason"]) == set()


def _propose(s, sf, cid, spec_, goals=(), catalog=None) -> list[Proposal]:
    with sf() as ss, tenant_scope(cid):
        run = SimpleNamespace(id=None, run_date=RUN)
        made = size_numbers.propose(ss, run, spec_, _size(spec_), catalog or Catalog(), list(goals))
        ss.commit()
        return made


def _open(sf, cid) -> list[Proposal]:
    with sf() as s, tenant_scope(cid):
        rows = size_numbers.open_rows(s)
        s.expunge_all()
        return rows


def test_proposals_not_now_and_the_evidence_changing(session_factory, two_companies) -> None:
    cid, _ = two_companies
    s = _spec([{"key": "collect", "name": "Collect more", "metric": "collections"}])
    made = _propose(None, session_factory, cid, s)
    assert [p.evidence["key"] for p in made] == [
        "top10_share",
        "gross_margin_pct",
        "revenue_per_active_customer",
        "stock_days",
    ]
    assert all(p.kind == "size_number" and p.text for p in made)
    assert made[0].text == "Add “Top 10 customers’ share of net revenue”"
    # the same night again: nothing new, the same open ones
    assert _propose(None, session_factory, cid, s) == []
    assert len(_open(session_factory, cid)) == 4
    # Not now on the first: it stays away while its evidence is the same …
    with session_factory() as ss, tenant_scope(cid):
        p = ss.scalars(sa.select(Proposal).where(Proposal.id == made[0].id)).one()
        proposals.decide(
            ss, p, accept=False, actor="o", now=dt.datetime(2026, 11, 2, tzinfo=dt.UTC)
        )
        ss.commit()
    _propose(None, session_factory, cid, s)
    keys = [p.evidence["key"] for p in _open(session_factory, cid)]
    assert "top10_share" not in keys
    assert keys[:3] == ["gross_margin_pct", "revenue_per_active_customer", "collected_vs_invoiced"]
    # … and comes back when its evidence changes (a goal it serves falls behind)
    s2 = _spec([{"key": "rev", "name": "Grow net revenue", "metric": "net_revenue"}])
    behind = goal_link.states(s2, [{"key": "rev", "on_track": False}])
    _propose(None, session_factory, cid, s2, behind)
    rows = _open(session_factory, cid)
    assert rows[0].evidence["key"] == "top10_share"
    assert rows[0].evidence["goal"] == {
        "key": "rev",
        "name": "Grow net revenue",
        "state": "off_track",
    }
    assert "It serves your goal Grow net revenue (off track)" in rows[0].evidence["line"]
    # one not picked tonight is set aside, never left open
    with session_factory() as ss, tenant_scope(cid):
        n = ss.scalar(
            sa.select(sa.func.count()).where(
                Proposal.kind == "size_number", Proposal.status == "replaced"
            )
        )
    assert n >= 1


def test_accept_writes_the_number_as_a_new_version(session_factory, two_companies) -> None:
    from othor.ontology import service

    cid, _ = two_companies
    made = _propose(None, session_factory, cid, _spec())
    with session_factory() as ss, tenant_scope(cid):
        p = ss.scalars(sa.select(Proposal).where(Proposal.id == made[1].id)).one()
        assert p.evidence["key"] == "gross_margin_pct"
        proposals.decide(ss, p, accept=True, actor="o", now=dt.datetime(2026, 11, 2, tzinfo=dt.UTC))
        ss.commit()
        rec = next(r for r in service.current(ss, ["metric"]) if r.key == "gross_margin_pct")
        assert rec.body["formula"]["op"] == "/"
        assert rec.body["name"] == "Gross margin %" and rec.body["unit"] == "percent"
        # one that needs a source has nothing to accept
        src = ss.scalars(sa.select(Proposal).where(Proposal.id == made[3].id)).one()
        proposals.decide(
            ss, src, accept=True, actor="o", now=dt.datetime(2026, 11, 2, tzinfo=dt.UTC)
        )
        assert src.status == "open"


def test_no_band_sets_every_open_one_aside(session_factory, two_companies) -> None:
    cid, _ = two_companies
    _propose(None, session_factory, cid, _spec())
    with session_factory() as ss, tenant_scope(cid):
        size_numbers.propose(
            ss, SimpleNamespace(id=None, run_date=RUN), _spec(), None, Catalog(), []
        )
        ss.commit()
    assert _open(session_factory, cid) == []
