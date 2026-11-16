# SPDX-License-Identifier: Apache-2.0
"""Sweep on the steady-state fixture, run of Sun 1 Nov 2026 (docs/11 planted patterns, M6a
acceptance): every planted pattern in its expected tab and state, nothing else; Watching 4; P4's
GSTIN check ruled out; P10's Home question; no personal column in any probe; replay identical.

The history before 1 Nov comes from a run of kind `fixture` on 31 Oct (the generator's earlier
nightly snapshots, `reconstructed=false`), so P2 is Measured. Resolved 2 needs the nights before
(P16 open on 31 Oct): that is the e2e test of ten real nights (tests/e2e/test_nights.py).
Needs `make dev && make seed`.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from othor.connectors.models import RunQuery
from othor.connectors.sql.query_log import identifiers
from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from othor.engine import orchestrator
from othor.engine.models import Finding, Run, RunStep
from othor.engine.replay import replay
from othor.engine.store import RunFiles, read_json
from othor.ontology.compiler.catalog import load_catalog
from othor.ontology.models import Question
from tests.fixtures.sample_company.history import STEADY_RUN, prior_snapshots
from tests.fixtures.sample_company.load import write_sheets
from tests.fixtures.sample_company.picture import check, picture
from tests.integration.run_world import confirm_sample, engine_ctx, require_sources

D = dt.date
# ₹57.19 Cr vs the ₹60 Cr goal (the board's ₹58.4 Cr is illustrative). Held to ±₹1: the ETS
# optimiser's floating point differs in the last bits between macOS and Linux (₹…559.00 on a Mac,
# ₹…559.04 on CI). Each platform repeats its own value byte for byte: replay checks that.
P13_PROJECTION = Decimal("571948559.00")
P13_TOLERANCE = Decimal("1")

# (detector, metric, breakdown value or entity) → (state, confidence) for P1–P7 and P10
EXPECTED = {
    "P1": ("net_hiding_gross", "net_revenue", None, "assessed", "high"),
    "P2": ("changepoint", "receivables_over_60", None, "measured", None),
    "P3": ("concentration", "order_book", None, "assessed", "medium"),
    "P4": ("missing_records", "invoiced_value", "Ravira Exports", "not_determinable", None),
    "P5": ("missing_records", "invoiced_value", None, "measured", None),
    "P6": ("broken_relationship", "freight", None, "assessed", None),
    "P7": ("rising_variance", "dispatch_time", "Coimbatore", "assessed", None),
    "P10": ("cross_source", "dispatch_invoice", None, "needs_confirmation", None),
}


@pytest.fixture
def night(session_factory, two_companies, sample, tmp_path, sqlite_only, sample_sources_seeded):
    require_sources()
    cid, _ = two_companies
    ctx = engine_ctx(session_factory, tmp_path)
    write_sheets(sample, ctx.settings.sample_dir / "sheets")
    confirm_sample(ctx, cid, sample, with_mis=True, watches=True, surat_answered=True)
    with ctx.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        f = orchestrator.start_fixture(
            ctx,
            s,
            c,
            run_date=STEADY_RUN - dt.timedelta(days=1),
            points={"erp_db": prior_snapshots(sample, STEADY_RUN)},
            image_version="0.1.0",
        )
        s.commit()
        fid = f.id
    with tenant_scope(cid):
        st = orchestrator.execute(ctx, fid)
    with ctx.sf() as s, tenant_scope(cid):
        steps = {
            x.name: (x.status, x.detail)
            for x in s.scalars(sa.select(RunStep).where(RunStep.run_id == fid))
        }
    assert st == "done", {k: v for k, v in steps.items() if v[0] != "done"}
    with ctx.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        r = orchestrator.start(
            s, c, kind="nightly", run_date=STEADY_RUN, now=ctx.clock(), image_version="0.1.0"
        )
        s.commit()
        rid = r.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid) == "done"
    return ctx, cid, fid, rid


def _match(f, detector, metric, who):
    return f["detector"] == detector and f["metric"] == metric and f["breakdown_value"] == who


def test_the_steady_state_night_of_1_nov(night, sample):
    ctx, cid, fid, rid = night
    files = RunFiles(ctx.settings.runs_dir(cid), rid)
    found = read_json(files.outputs / "findings.json")
    open_ = [f for f in found if f["status"] == "open"]
    seen = {}
    for p, (det, metric, who, st, conf) in EXPECTED.items():
        hits = [f for f in open_ if _match(f, det, metric, who)]
        assert len(hits) == 1, (
            p,
            [
                (
                    f["detector"],
                    f["metric"],
                    f["breakdown_value"],
                    f["entities"][:3],
                    f["state"],
                    f["confidence"],
                    f["money"],
                    sorted(f["slots"]),
                )
                for f in found
            ],
        )
        (f,) = hits
        assert f["state"] == st, (p, f["state"])
        if conf:
            assert f["confidence"] == conf, (p, f["confidence"])
        seen[p] = f
    # nothing else: no finding on any clean series (C1–C10 are among the series tested)
    assert len(open_) == len(EXPECTED), [(f["detector"], f["metric"]) for f in open_]
    # M11c: Ravira Exports' card leads with its bars that stop; the silent group shows rows
    from othor.charts.spec import ChartSpec

    ravira = ChartSpec.from_json(seen["P4"]["chart"])
    assert ravira.kind == "gap_block" and ravira.annotations[0].label == "no invoices"
    assert ravira.annotations[0].from_ > seen["P4"]["slots"]["last"]
    assert seen["P5"].get("chart") is None

    doc = read_json(files.outputs_json)["sweep"]
    assert doc["counts"]["findings"] == 8
    assert doc["counts"]["suppressed"] == 2  # M7: the Diwali week, the Surat catch-up (5a)
    # M7: P1's forecast half, the week of 12 Oct under its band, doesn't pass on its own (q >
    # 0.01): supporting evidence on P1's Test tab, not "Also seen by" (decided 5 Nov 2026)
    (wk,) = [
        r
        for r in read_json(files.outputs / "detection.json")
        if r["detector"] == "forecast_breach"
        and r["metric"] == "net_revenue"
        and r["window"][0] == "2026-10-12"
    ]
    assert wk["direction"] == "down" and wk["money"] and not wk["passed"]
    assert "forecast_breach" not in seen["P1"]["slots"].get("also_by", [])
    # P13: the FY27 projection, golden (decided 5 Nov 2026: the computed value, held exactly)
    fc = read_json(files.outputs_json)["forecast"]
    (fy,) = [g for g in fc["goals"] if g["key"] == "fy27_net_revenue"]
    assert abs(Decimal(fy["projection"]) - P13_PROJECTION) <= P13_TOLERANCE, fy
    assert Decimal(fy["target"]) - Decimal(fy["projection"]) > Decimal("500000")
    assert doc["counts"]["watching"] == 4
    # the 1 Nov picture, golden (Resolved needs the nights before: tests/e2e/test_nights.py)
    got = picture(
        found,
        read_json(files.outputs / "detection.json"),
        read_json(files.outputs / "watches.json"),
        resolved=False,
    )
    check(got, "steady")

    # P1: South's drop, the three accounts
    p1 = seen["P1"]
    assert abs(Decimal(p1["money"]) - Decimal("14000000")) / Decimal("14000000") < Decimal("0.02")
    # P4: billing moved to another entity is ruled out by the GSTIN check
    (same_id,) = [
        r for r in seen["P4"]["reasons"] if (r["check"] or {}).get("kind") == "same_identity"
    ]
    assert same_id["ruled_out"] and same_id["check"]["found"] == []
    # P5: 14 customers, ₹3.60 Cr
    assert seen["P5"]["slots"]["count"] == 14
    assert abs(Decimal(seen["P5"]["money"]) - Decimal("36000000")) <= Decimal("360000")
    # P10: 37 orders, and its Home question for the owners of the area
    assert seen["P10"]["slots"]["count"] == 37
    with ctx.sf() as s, tenant_scope(cid):
        q = s.scalars(
            sa.select(Question).where(
                Question.fingerprint == f"finding:{seen['P10']['fingerprint']}"
            )
        ).one()
        assert "37" in q.text
        assert sorted(q.origin["owners"]) == ["Leela Tandon", "Priya Menon"]
        # P8 and P11 were found and stopped at the materiality floor
        rows = s.scalars(sa.select(Finding).where(Finding.run_id == rid)).all()
        assert len([r for r in rows if r.status == "open"]) == 8
    results = read_json(files.outputs / "detection.json")
    # Tessora EU invoices every two weeks or so (gaps of 10–18 days), far slower than Ravira
    # Exports: its 3-week gap is within its own rhythm, not a finding
    (tessora,) = [
        r for r in results if r["detector"] == "missing_records" and r["entities"] == ["Tessora EU"]
    ]
    assert not tessora["passed"] and tessora["reason"] == "within its own rhythm"
    assert Decimal(tessora["slots"]["median_days"]) >= 10
    # Skarfab is under its answered pause: not tested, said in Coverage
    (skarfab,) = [
        r for r in results if r["detector"] == "missing_records" and r["entities"] == ["Skarfab"]
    ]
    assert not skarfab["passed"] and skarfab["reason"].startswith("explained by your answer")
    (mr,) = [c for c in doc["coverage"] if c["detector"] == "missing_records"]
    assert any(n.startswith("Skarfab · paused") for n in mr["notes"]), mr["notes"]
    for det in ("process_artefact", "bimodal_average"):
        hit = [r for r in results if r["detector"] == det and r["p_value"]]
        assert hit and min(Decimal(r["p_value"]) for r in hit) < Decimal("0.01"), det
        assert {r["reason"] for r in hit if Decimal(r["p_value"]) < Decimal("0.01")} == {
            "below materiality"
        }, det

    # no personal column in any query the run sent (probes included)
    with ctx.sf() as s, tenant_scope(cid):
        personal = {
            t.name.lower(): t.personal for t in load_catalog(s).tables.values() if t.personal
        }
        sent = list(s.scalars(sa.select(RunQuery).where(RunQuery.run_id == rid)))
        assert any("cutoff" in (q.params or {}) for q in sent)  # the probes are among them
        for q in sent:
            ids = identifiers(q.sql)
            assert not any(t in ids and cols & ids for t, cols in personal.items()), q.sql
        kinds = [r.kind for r in s.scalars(sa.select(Run).order_by(Run.number))]
    assert kinds == ["fixture", "nightly"]
    with tenant_scope(cid):
        assert replay(ctx, fid).ok
        assert replay(ctx, rid).ok


def test_a_fixture_run_is_refused_outside_the_test_environment(
    session_factory, two_companies, tmp_path
):
    from othor.core.config import Settings
    from othor.engine.context import EngineContext

    cid, _ = two_companies
    test = engine_ctx(session_factory, tmp_path)
    ctx = EngineContext(
        Settings(env="dev", data_dir=tmp_path, db_url="sqlite://"), test.sf, test.key
    )
    with ctx.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        with pytest.raises(orchestrator.FixtureRefused):
            orchestrator.start_fixture(
                ctx, s, c, run_date=STEADY_RUN, points={}, image_version="0.1.0"
            )
        with pytest.raises(orchestrator.FixtureRefused):
            orchestrator.start(
                s, c, kind="fixture", run_date=STEADY_RUN, now=ctx.clock(), image_version="0.1.0"
            )
