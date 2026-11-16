# SPDX-License-Identifier: Apache-2.0
"""P15 vs P2 (docs/11, decided 5 Nov 2026): the same receivables-over-60-days step in mid-August.

- First-days fixture (first run Mon 2 Nov): no snapshot exists before the run, so the step is
  found only through the back-fill, and every point carries reconstructed=true (→ Assessed).
- Steady-state fixture (1 Nov): earlier nightly snapshots from the generator, reconstructed=false
  (→ P2 stays Measured).

M5 asserts the two series agree week by week and differ only in the flag. M6a adds the
changepoint finding on 2 Nov for both: same money and slots (the text's inputs), the same
fingerprint, Measured vs Assessed. The provenance line is M6b's wording.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from othor.engine import orchestrator
from othor.engine.store import RunFiles, read_json, read_points
from tests.fixtures.sample_company.history import FIRST_DAYS_RUN, prior_snapshots
from tests.integration.run_world import confirm_sample, engine_ctx, require_sources

STEP = dt.date(2026, 8, 14)


def test_p15_backfill_matches_the_steady_state_snapshots_week_by_week(
    session_factory,
    two_companies,
    sample,
    tmp_path,
    sqlite_only,
    sample_sources_seeded,
):
    require_sources()
    cid, _ = two_companies
    ctx = engine_ctx(session_factory, tmp_path)
    confirm_sample(ctx, cid, sample)
    with ctx.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        r = orchestrator.start(
            s, c, kind="dry", run_date=FIRST_DAYS_RUN, now=ctx.clock(), image_version="0.1.0"
        )
        s.commit()
        rid = r.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid) == "done"
    first_days = {
        (p.metric, p.period): p
        for p in read_points(RunFiles(ctx.settings.runs_dir(cid), rid).series)
        if p.metric in ("receivables_over_60", "receivables_open")
        and p.grain == "day"
        and not p.breakdown
        and p.reconstructed
    }
    sundays = sorted({d for _, d in first_days})
    assert len(sundays) == 104 and STEP - dt.timedelta(weeks=12) > sundays[0]
    # the generator's earlier nightly snapshots on the same Sundays
    steady = {(p.metric, p.period): p for p in prior_snapshots(sample, every=1)}
    for key, p in first_days.items():
        q = steady[key]
        assert p.reconstructed and not q.reconstructed  # the only difference
        assert abs(p.value - q.value) <= Decimal("0.01"), key

    # and both show the step: about 9% before mid-August, about 14% after
    def share(points, d):
        return points[("receivables_over_60", d)].value / points[("receivables_open", d)].value

    before = [d for d in sundays if STEP - dt.timedelta(weeks=8) <= d < STEP]
    after = [d for d in sundays if STEP + dt.timedelta(weeks=1) <= d]
    for pts in (first_days, steady):
        b = sum(share(pts, d) for d in before) / len(before)
        a = sum(share(pts, d) for d in after) / len(after)
        assert b < Decimal("0.11") and a > Decimal("0.125"), (b, a)


def _night(ctx, cid, kind, run_date, points=None):
    with ctx.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        if kind == "fixture":
            r = orchestrator.start_fixture(
                ctx, s, c, run_date=run_date, points=points, image_version="0.1.0"
            )
        else:
            r = orchestrator.start(
                s, c, kind=kind, run_date=run_date, now=ctx.clock(), image_version="0.1.0"
            )
        s.commit()
        rid = r.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid) == "done"
    return rid


def _p2(ctx, cid, rid):
    found = read_json(RunFiles(ctx.settings.runs_dir(cid), rid).outputs / "findings.json")
    (f,) = [
        x
        for x in found
        if x["detector"] == "changepoint" and x["metric"].startswith("receivables_")
    ]
    return f


@pytest.fixture
def both(session_factory, two_companies, sample, tmp_path, sqlite_only, sample_sources_seeded):
    require_sources()
    steady, first = two_companies
    ctx = engine_ctx(session_factory, tmp_path)
    confirm_sample(ctx, steady, sample)
    confirm_sample(ctx, first, sample)
    _night(
        ctx,
        steady,
        "fixture",
        FIRST_DAYS_RUN - dt.timedelta(days=1),
        {"erp_db": prior_snapshots(sample, FIRST_DAYS_RUN)},
    )
    a = _night(ctx, steady, "nightly", FIRST_DAYS_RUN)
    b = _night(ctx, first, "nightly", FIRST_DAYS_RUN)
    return _p2(ctx, steady, a), _p2(ctx, first, b)


def test_p15_same_fact_same_money_only_the_state_differs(both):
    p2, p15 = both
    assert p2["state"] == "measured" and not p2["reconstructed_only"]
    assert p15["state"] == "assessed" and p15["reconstructed_only"]
    for k in ("metric", "series", "direction", "window", "money", "money_basis"):
        assert p2[k] == p15[k], k
    # the text's inputs are the same; the day of the step is evidence only daily snapshots give
    day = p2["slots"].pop("change_date")
    # M7: the steady night has forecasts of record (P2 is also seen by Forecast breach); the
    # first days have none yet (a forecast is stated before its period), so that line differs
    assert "forecast_breach" in p2["slots"].pop("also_by", [])
    assert "forecast_breach" not in p15["slots"].pop("also_by", [])
    assert "change_date" not in p15["slots"] and p2["slots"] == p15["slots"]
    assert dt.date.fromisoformat(p2["slots"]["change_week"]) <= dt.date.fromisoformat(day)
    assert day == "2026-08-14"
    assert p2["fingerprint"] == p15["fingerprint"]
