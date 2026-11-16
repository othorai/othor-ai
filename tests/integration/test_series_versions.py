# SPDX-License-Identifier: Apache-2.0
"""Series versions (answers of 5 Nov 2026, docs/OPEN-QUESTIONS): a snapshot metric whose
definition changes gets a new series version: the old points are kept apart (readable for
History), never mixed with the new ones, and the new version is back-filled as of past dates so
detection goes on without a gap and without a finding at the change. A pure scale change keeps
one series, converted exactly. A snapshot metric that becomes active is back-filled on its first
night. Needs `make dev && make seed`.
"""

from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal

import pandas as pd
import pytest
import sqlalchemy as sa

from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.engine.models import RunStep
from othor.engine.replay import replay
from othor.engine.store import read_json, read_points
from othor.ontology import service
from othor.ontology.models import OntologyRecord
from tests.fixtures.sample_company import measures
from tests.integration.run_world import confirm_sample, engine_ctx, require_sources
from tests.integration.setup_world import OWNER
from tests.integration.test_run_sample import FIRST, PAISA, _files, _run

D = dt.date
NEXT = FIRST + dt.timedelta(days=1)


@pytest.fixture
def world(session_factory, two_companies, sample, tmp_path, sqlite_only, sample_sources_seeded):
    require_sources()
    cid, _ = two_companies
    ctx = engine_ctx(session_factory, tmp_path)
    confirm_sample(ctx, cid, sample)
    return ctx, cid


def _change(ctx, cid, key, edit, reason):
    with ctx.sf() as s, tenant_scope(cid):
        rec = s.scalars(
            sa.select(OntologyRecord).where(
                OntologyRecord.type == "metric",
                OntologyRecord.key == key,
                OntologyRecord.status == "active",
            )
        ).one()
        body = copy.deepcopy(rec.body)
        edit(body)
        service.apply(s, [service.put("metric", key, body)], actor=OWNER, reason=reason)
        s.commit()


def _over(days):
    def edit(body):
        body["filters"][-1]["args"][1] = {"lit": days}

    return edit


def _points(ctx, cid, rid, metric, path="series"):
    f = _files(ctx, cid, rid)
    src = f.series if path == "series" else f.archive_points("erp_db")
    return {
        p.period: p
        for p in read_points(src)
        if p.metric == metric and p.grain == "day" and p.breakdown == ""
    }


def _over_days(sample, day: D, days: int) -> Decimal:
    o = measures._open_receivables(sample, pd.Timestamp(day))
    age = (pd.Timestamp(day) - pd.to_datetime(o["due_date"])).dt.days
    return Decimal(str(round(float(o.loc[age > days, "open_amount"].sum()), 2)))


def test_a_definition_change_is_a_new_series_version_back_filled(world, sample):
    ctx, cid = world
    r1 = _run(ctx, cid, run_date=FIRST)
    before = _points(ctx, cid, r1, "receivables_over_60")
    old_hash = read_json(_files(ctx, cid, r1).source_meta("erp_db"))["definitions"][
        "receivables_over_60"
    ]["hash"]
    _change(ctx, cid, "receivables_over_60", _over(90), "Over 90 days, not 60")
    r2 = _run(ctx, cid, run_date=NEXT)

    # the old version stays readable, apart, under its own definition
    archived = {
        p.period: p
        for p in read_points(_files(ctx, cid, r2).archive_points("erp_db"))
        if p.metric == f"receivables_over_60@{old_hash}" and p.breakdown == ""
    }
    assert archived and {k: v.value for k, v in archived.items()} == {
        k: v.value for k, v in before.items()
    }
    meta = read_json(_files(ctx, cid, r2).source_meta("erp_db"))
    (version,) = [v for v in meta["versions"] if v["metric"] == "receivables_over_60"]
    assert version["definition"] == old_hash and version["points"] >= len(before)
    assert meta["definitions"]["receivables_over_60"]["hash"] != old_hash
    with ctx.sf() as s, tenant_scope(cid):
        (ev,) = s.scalars(sa.select(Event).where(Event.action == "series.versioned")).all()
        assert ev.after["metric"] == "receivables_over_60" and ev.after["definition"] == old_hash

    # the new version is back-filled, and each back-filled point is the new definition as of
    # its date (the direct as-of check)
    with ctx.sf() as s, tenant_scope(cid):
        bf = s.scalars(
            sa.select(RunStep).where(RunStep.run_id == r2, RunStep.name == "backfill:erp_db")
        ).one()
    assert set(bf.detail["metrics"]) == {"receivables_over_60"}
    now = _points(ctx, cid, r2, "receivables_over_60")
    recon = sorted(d for d, p in now.items() if p.reconstructed)
    assert len(recon) >= 52
    for day in (*recon[-3:], recon[0]):
        assert abs(now[day].value - _over_days(sample, day, 90)) <= PAISA, day
    assert (
        abs(
            now[NEXT - dt.timedelta(days=1)].value
            - _over_days(sample, NEXT - dt.timedelta(days=1), 90)
        )
        <= PAISA
    )
    # never mixed: no point of the new series is an old-definition value
    assert all(now[d].value != before[d].value for d in recon[-4:] if d in before)

    # no finding from the change itself: P2's planted step (August) is still found, but
    # nothing on these series breaks at the change
    found = read_json(_files(ctx, cid, r2).outputs / "findings.json")
    mine = [f for f in found if f["metric"].startswith("receivables_")]
    assert mine, "P2 is still found on the new version"
    change = NEXT - dt.timedelta(days=1)
    assert not [
        f
        for f in mine
        if D.fromisoformat(f["window"][1]) >= change - dt.timedelta(days=14)
        and D.fromisoformat(f["window"][0]) >= change - dt.timedelta(days=14)
    ], [(f["detector"], f["window"]) for f in mine]
    with tenant_scope(cid):
        assert replay(ctx, r2).ok


def test_a_pure_scale_change_keeps_one_series_converted_exactly(world):
    ctx, cid = world
    r1 = _run(ctx, cid, run_date=FIRST)
    before = _points(ctx, cid, r1, "receivables_open")

    def thousands(body):
        body["scale"] = "0.001"

    _change(ctx, cid, "receivables_open", thousands, "In thousands")
    r2 = _run(ctx, cid, run_date=NEXT)
    after = _points(ctx, cid, r2, "receivables_open")
    for day, p in before.items():
        assert after[day].value == p.value * Decimal("0.001"), day
    meta = read_json(_files(ctx, cid, r2).source_meta("erp_db"))
    assert not [v for v in meta["versions"] if v["metric"] == "receivables_open"]
    assert meta["conversions"] == [
        {"metric": "receivables_open", "on": FIRST.isoformat(), "from": "1", "to": "0.001"}
    ]
    with ctx.sf() as s, tenant_scope(cid):
        (ev,) = s.scalars(sa.select(Event).where(Event.action == "series.converted")).all()
        assert ev.after["to"] == "0.001"
    with ctx.sf() as s, tenant_scope(cid):
        bf = s.scalars(
            sa.select(RunStep).where(RunStep.run_id == r2, RunStep.name == "backfill:erp_db")
        ).one()
    assert bf.detail["metrics"] == {} and bf.detail["queries"] == 0
    with tenant_scope(cid):
        assert replay(ctx, r2).ok


def test_a_snapshot_metric_that_becomes_active_is_back_filled_that_night(world):
    ctx, cid = world

    def off(body):
        body["status"] = "declined"

    _change(ctx, cid, "order_book", off, "Not yet")
    r1 = _run(ctx, cid, run_date=FIRST)
    assert not _points(ctx, cid, r1, "order_book")

    def on(body):
        body["status"] = "active"

    _change(ctx, cid, "order_book", on, "Now")
    r2 = _run(ctx, cid, run_date=NEXT)
    with ctx.sf() as s, tenant_scope(cid):
        bf = s.scalars(
            sa.select(RunStep).where(RunStep.run_id == r2, RunStep.name == "backfill:sales_dw")
        ).one()
    assert set(bf.detail["metrics"]) == {"order_book"}
    got = read_points(_files(ctx, cid, r2).series)
    assert sum(1 for p in got if p.metric == "order_book" and p.reconstructed) > 0


def test_a_compiler_change_alone_never_versions_a_series(world, monkeypatch):
    """Definition hash = the metric's meaning (its Ontology records); render hash = what the
    compiler produced. A change in render output only (a compiler fix, a wider decimal) keeps
    the same series and its history (decided 5 Nov 2026)."""
    from othor.ontology.compiler import build

    ctx, cid = world
    r1 = _run(ctx, cid, run_date=FIRST)
    before = _points(ctx, cid, r1, "receivables_over_60")
    meta1 = read_json(_files(ctx, cid, r1).source_meta("erp_db"))
    real = build.to_sql
    from othor.ontology.compiler import api

    monkeypatch.setattr(build, "to_sql", lambda e, d: real(e, d) + "\n")  # a new render
    monkeypatch.setattr(api, "COMPILER_VERSION", build.COMPILER_VERSION + "+test")  # a new compiler
    r2 = _run(ctx, cid, run_date=NEXT)
    meta2 = read_json(_files(ctx, cid, r2).source_meta("erp_db"))
    assert meta2["query_hashes"] != meta1["query_hashes"]  # the SQL changed
    assert meta2["definitions"] == meta1["definitions"]  # the meaning didn't
    assert meta2["versions"] == [] and meta2["conversions"] == []
    after = _points(ctx, cid, r2, "receivables_over_60")
    for day, p in before.items():
        assert after[day].value == p.value and after[day].reconstructed == p.reconstructed
    with ctx.sf() as s, tenant_scope(cid):
        bf = s.scalars(
            sa.select(RunStep).where(RunStep.run_id == r2, RunStep.name == "backfill:erp_db")
        ).one()
    assert bf.detail["metrics"] == {} and bf.detail["note"] == "nothing due"
