# SPDX-License-Identifier: Apache-2.0
"""The run on Indigo Thread against the compose sample sources (docs/10 M5 acceptance):
values match the fixture oracles, the back-fill reconstructs P2's step weekly, two runs on the
same inputs are byte-identical, replay reproduces every run, a crash resumes to the same bytes,
a failed source goes stale, Read now is a source-scoped manual run. Needs `make dev && make seed`.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pandas as pd
import pytest
import sqlalchemy as sa

from othor.connectors import credentials
from othor.connectors.models import RunQuery, Source
from othor.connectors.sql.query_log import identifiers
from othor.core.models import Company, Event
from othor.core.tenancy import tenant_scope
from othor.engine import orchestrator, periods
from othor.engine.ledger import verify
from othor.engine.models import Run, RunStep, SeriesMeta
from othor.engine.replay import replay
from othor.engine.store import RunFiles, read_points
from othor.ontology.compiler.catalog import load_catalog
from tests.fixtures.sample_company import measures
from tests.integration.run_world import confirm_sample, engine_ctx, require_sources

D = dt.date
FIRST = D(2026, 11, 2)  # the first-days fixture: first run Mon 2 Nov, as of Sun 1 Nov
PAISA = Decimal("0.01")


def _start(ctx, cid, kind="dry", run_date=FIRST, scope=None):
    with ctx.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        r = orchestrator.start(
            s, c, kind=kind, run_date=run_date, now=ctx.clock(), image_version="0.1.0", scope=scope
        )
        s.commit()
        return r.id


def _run(ctx, cid, **kw):
    rid = _start(ctx, cid, **kw)
    with tenant_scope(cid):
        orchestrator.execute(ctx, rid)
    return rid


def _files(ctx, cid, rid) -> RunFiles:
    return RunFiles(ctx.settings.runs_dir(cid), rid)


def _series(ctx, cid, rid, metric, grain, breakdown=""):
    return {
        (p.breakdown_value, p.period): p
        for p in read_points(_files(ctx, cid, rid).series)
        if p.metric == metric and p.grain == grain and p.breakdown == breakdown
    }


@pytest.fixture
def world(session_factory, two_companies, sample, tmp_path, sqlite_only, sample_sources_seeded):
    require_sources()
    cid, _ = two_companies
    ctx = engine_ctx(session_factory, tmp_path)
    ids = confirm_sample(ctx, cid, sample)
    return ctx, cid, ids


@pytest.fixture
def first(world):
    ctx, cid, ids = world
    return ctx, cid, ids, _run(ctx, cid)


def test_first_run_values_match_the_oracles(first, sample):
    ctx, cid, _, rid = first
    with ctx.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        steps = s.scalars(sa.select(RunStep).where(RunStep.run_id == rid)).all()
        assert run.status == "done" and run.number == 1 and run.prev_run_hash is None
        assert {st.status for st in steps} == {"done"}
        assert [st.name for st in sorted(steps, key=lambda x: x.ordinal)] == [
            "read:erp_db",
            "materialise:erp_db",
            "backfill:erp_db",
            "read:sales_dw",
            "materialise:sales_dw",
            "backfill:sales_dw",
            "probes:erp_db",
            "probes:sales_dw",
            "derive",
            "forecast",
            "detect",
            "questions",
            "brief",
            "seal",
        ]
        queries = s.scalars(sa.select(RunQuery).where(RunQuery.run_id == rid)).all()
        personal = {
            t.name.lower(): t.personal for t in load_catalog(s).tables.values() if t.personal
        }
    # every value a direct computation agrees with (docs/11: values match the oracle)
    net = _series(ctx, cid, rid, "net_revenue", "month")
    oracle = measures.monthly_net_revenue(sample)
    for month in ("2026-08", "2026-09", "2026-10"):
        got = net[(None, D.fromisoformat(month + "-01"))].value
        assert abs(got - Decimal(str(oracle[month]))) <= PAISA, month
    by_region = _series(ctx, cid, rid, "net_revenue", "month", "region")
    for region, v in measures.net_revenue_by_region_month(sample)["2026-10"].items():
        assert abs(by_region[(region, D(2026, 10, 1))].value - Decimal(str(v))) <= PAISA, region
    share = _series(ctx, cid, rid, "receivables_share_over_60", "day")
    want = measures.share_over_60(sample, [pd.Timestamp("2026-11-01")]).iloc[0]
    assert abs(share[(None, D(2026, 11, 1))].value - Decimal(str(want))) < Decimal("0.000002")
    assert not share[(None, D(2026, 11, 1))].reconstructed
    # October is complete; nothing on or after the run date is read
    assert net[(None, D(2026, 10, 1))].complete
    assert max(d for _, d in net) <= D(2026, 11, 1)
    # run_query holds every statement, none naming a personal column (guard test 1)
    assert len(queries) > 100 and {q.purpose for q in queries} == {"run"}
    for q in queries:
        ids = identifiers(q.sql)
        assert not any(t in ids and cols & ids for t, cols in personal.items()), q.sql


def test_backfill_reconstructs_p2_weekly_from_one_grouped_query(first, sample):
    ctx, cid, _, rid = first
    share = _series(ctx, cid, rid, "receivables_share_over_60", "day")
    recon = sorted(d for (_, d), p in share.items() if p.reconstructed)
    assert recon[-1] == D(2026, 10, 25) and all(d.weekday() == 6 for d in recon)
    assert len(recon) <= 104 and recon[0] >= D(2024, 4, 1)
    for d in recon[::13]:  # each reconstructed Sunday equals the generator's as-of truth
        want = measures.share_over_60(sample, [pd.Timestamp(d)]).iloc[0]
        assert abs(share[(None, d)].value - Decimal(str(want))) < Decimal("0.000002"), d
    # P2: the share steps from about 9% to 14% in mid-August, visible only through back-fill
    before = [share[(None, d)].value for d in recon if D(2026, 6, 1) <= d < D(2026, 8, 1)]
    after = [share[(None, d)].value for d in recon if D(2026, 8, 23) <= d <= D(2026, 10, 25)]
    assert sum(before) / len(before) < Decimal("0.11") < Decimal("0.125") < sum(after) / len(after)
    # the weekly series is continuous: back-filled Sundays, then the first daily point
    weekly = _series(ctx, cid, rid, "receivables_share_over_60", "week")
    assert weekly[(None, D(2026, 10, 26))].value == share[(None, D(2026, 11, 1))].value
    assert weekly[(None, D(2026, 10, 19))].reconstructed
    with ctx.sf() as s, tenant_scope(cid):
        meta = s.scalars(
            sa.select(SeriesMeta).where(
                SeriesMeta.metric == "receivables_over_60",
                SeriesMeta.grain == "day",
                SeriesMeta.breakdown == "",
            )
        ).one()
        assert meta.reconstructed_to == recon[0]
        assert "invoice_date" in (meta.reconstructed_from or "")
        bf = s.scalars(
            sa.select(RunStep).where(RunStep.run_id == rid, RunStep.name == "backfill:erp_db")
        ).one()
        # one grouped query per metric and breakdown for each 13-Sunday chunk (M15.1), plus one
        # history_start per metric: never one per as-of date
        metrics = bf.detail["metrics"]
        spec_metrics = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        assert set(metrics) >= {"receivables_over_60", "receivables_open", "cash_balance"}
        chunks = len(periods.backfill_chunks(recon[0], recon[-1]))
        assert chunks > 1 and bf.detail["queries"] < 30 * chunks
        # sales_dw's order book (a snapshot metric) is back-filled in its own step
        dw = s.scalars(
            sa.select(RunStep).where(RunStep.run_id == rid, RunStep.name == "backfill:sales_dw")
        ).one()
        assert set(dw.detail["metrics"]) == {"order_book"}
        assert s.scalar(
            sa.select(sa.func.count())
            .select_from(Event)
            .where(Event.action == "snapshot.backfilled")
        ) == len(metrics) + len(dw.detail["metrics"])
        del spec_metrics


def test_same_inputs_same_bytes_replay_and_ledger(first):
    ctx, cid, _, r1 = first
    r2 = _run(ctx, cid, kind="nightly")
    r3 = _run(ctx, cid, kind="nightly")
    f1, f2, f3 = (_files(ctx, cid, r) for r in (r1, r2, r3))
    assert f2.manifest.read_bytes() == f3.manifest.read_bytes()
    assert f2.outputs_json.read_bytes() == f3.outputs_json.read_bytes()
    # incremental (carried + look-back) equals the first full read, kind aside
    a, b = json.loads(f1.outputs_json.read_bytes()), json.loads(f2.outputs_json.read_bytes())
    # tonight's change tags differ (New on the first run, Open · day 1 after), nothing else
    sa_, sb_ = a.pop("sweep"), b.pop("sweep")
    # M7: the same forecasts; only the first run carries the labelled backtest, so the
    # forecast document's hash differs and nothing else does
    fa, fb = a.pop("forecast"), b.pop("forecast")
    assert {**fa, "hash": None} == {**fb, "hash": None}
    # M8: the brief carries the same change tags (New, then Open · day 1): same issues, other words
    ba, bb = a.pop("briefs"), b.pop("briefs")
    assert {**ba, "hash": None} == {**bb, "hash": None}
    assert {**a, "kind": None} == {**b, "kind": None}
    assert (
        sa_["results_hash"] == sb_["results_hash"]
        and sa_["counts"]["findings"] == sb_["counts"]["findings"]
    )
    assert sa_["counts"]["new"] == sa_["counts"]["findings"] and sb_["counts"]["new"] == 0
    with ctx.sf() as s, tenant_scope(cid):
        runs = {r.id: r for r in s.scalars(sa.select(Run)).all()}
        assert runs[r2].input_hash == runs[r3].input_hash
        assert runs[r2].output_hash == runs[r3].output_hash
        assert runs[r2].run_hash != runs[r3].run_hash
        assert runs[r3].prev_run_hash == runs[r2].run_hash
        assert runs[r2].prev_run_hash == runs[r1].run_hash
        assert verify(s, ctx.settings, cid).ok
    with tenant_scope(cid):
        for r in (r1, r2, r3):
            out = replay(ctx, r)
            assert out.ok, out.diff
    # tampering shows
    doc = json.loads(f2.outputs_json.read_bytes())
    doc["series"]["points"] += 1
    f2.outputs_json.write_text(json.dumps(doc))
    with ctx.sf() as s, tenant_scope(cid):
        assert any("#0002: outputs changed" in p for p in verify(s, ctx.settings, cid).problems)
    with tenant_scope(cid):
        assert not replay(ctx, r2).ok


def test_a_crash_resumes_to_the_same_bytes(first):
    ctx, cid, _, _ = first
    clean = _run(ctx, cid, kind="nightly")
    rid = _start(ctx, cid, kind="nightly")

    def boom(step):
        if step == "materialise:sales_dw":
            raise RuntimeError("worker killed")

    ctx.fault = boom
    with tenant_scope(cid), pytest.raises(RuntimeError):
        orchestrator.execute(ctx, rid)
    with ctx.sf() as s, tenant_scope(cid):
        # killed, not failed: nothing got to mark it; the next claim resumes it
        assert s.scalars(sa.select(Run.status).where(Run.id == rid)).one() == "running"
        done = s.scalars(
            sa.select(RunStep.name).where(RunStep.run_id == rid, RunStep.status == "done")
        ).all()
        # (a nightly back-fill step runs too, and reads nothing when no metric is due)
        assert set(done) == {
            "read:erp_db",
            "materialise:erp_db",
            "backfill:erp_db",
            "read:sales_dw",
        }
    ctx.fault = None
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid) == "done"
    assert (
        _files(ctx, cid, rid).outputs_json.read_bytes()
        == _files(ctx, cid, clean).outputs_json.read_bytes()
    )


def test_a_source_that_fails_goes_stale_and_the_run_goes_on(world):
    ctx, cid, ids = world
    r1 = _run(ctx, cid)
    with ctx.sf() as s, tenant_scope(cid):
        credentials.store(s, ctx.key, cid, ids["sales_dw"], {"password": "wrong"})
        s.commit()
    r2 = _run(ctx, cid, kind="nightly", run_date=FIRST + dt.timedelta(days=1))
    with ctx.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == r2)).one()
        assert run.status == "partial" and run.summary["stale_sources"] == ["sales_dw"]
        st = {x.name: x.status for x in s.scalars(sa.select(RunStep).where(RunStep.run_id == r2))}
        assert st["read:sales_dw"] == "stale" and st["materialise:erp_db"] == "done"
        metres = s.scalars(
            sa.select(SeriesMeta).where(
                SeriesMeta.metric == "metres_dispatched", SeriesMeta.breakdown == ""
            )
        ).first()
        assert metres.stale and metres.last_good_at == D(2026, 11, 1)
        assert s.scalar(
            sa.select(sa.func.count()).select_from(Event).where(Event.action == "source.stale")
        )
    doc = json.loads(_files(ctx, cid, r2).outputs_json.read_bytes())
    assert doc["coverage"]["sources"]["sales_dw"] == {"status": "stale", "last_good": "2026-11-01"}
    assert "metres_dispatched" in doc["coverage"]["stale_metrics"]
    # the stale source's points are the last good ones
    a = _series(ctx, cid, r1, "metres_dispatched", "month")
    b = _series(ctx, cid, r2, "metres_dispatched", "month")
    assert {k: p.value for k, p in a.items()} == {k: p.value for k, p in b.items()}
    with tenant_scope(cid):
        assert replay(ctx, r2).ok


def test_read_now_is_a_source_scoped_manual_run(first):
    ctx, cid, ids, r1 = first
    rid = _run(ctx, cid, kind="manual", scope={"source_id": str(ids["erp_db"])})
    with ctx.sf() as s, tenant_scope(cid):
        names = [
            x.name
            for x in s.scalars(
                sa.select(RunStep).where(RunStep.run_id == rid).order_by(RunStep.ordinal)
            )
        ]
        assert names == ["read:erp_db", "materialise:erp_db", "carry:sales_dw", "derive", "seal"]
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        assert run.status == "done" and run.prev_run_hash is not None
        src = s.scalars(sa.select(Source).where(Source.id == ids["erp_db"])).one()
        assert src.health["last_read"]["run_id"] == str(rid)
        assert src.health["last_read"]["rows"] > 0
        sent = s.scalars(sa.select(RunQuery.source_id).where(RunQuery.run_id == rid)).all()
        assert set(sent) == {ids["erp_db"]}
    doc = json.loads(_files(ctx, cid, rid).outputs_json.read_bytes())
    assert doc["kind"] == "manual" and doc["scope"] == {"source_id": str(ids["erp_db"])}
    assert doc["coverage"]["sources"]["sales_dw"]["status"] == "carried"
    # the snapshot already captured as of 1 Nov is kept, not re-read
    a = _series(ctx, cid, r1, "receivables_over_60", "day")
    b = _series(ctx, cid, rid, "receivables_over_60", "day")
    assert a[(None, D(2026, 11, 1))].value == b[(None, D(2026, 11, 1))].value
    with tenant_scope(cid):
        assert replay(ctx, rid).ok
