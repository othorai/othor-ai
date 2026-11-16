# SPDX-License-Identifier: Apache-2.0
"""Guard 3 (docs/11, kept forever): two consecutive runs on identical inputs give identical
canonical outputs and hashes, and `othor replay` of every run reproduces it byte for byte.
Guard 1 for runs: no statement a run sent names a personal column. Needs the sample sources."""

from __future__ import annotations

import json

import sqlalchemy as sa

from othor.connectors.models import RunQuery
from othor.connectors.sql.query_log import identifiers
from othor.core.tenancy import tenant_scope
from othor.engine.ledger import verify
from othor.engine.models import Run
from othor.engine.replay import replay
from othor.ontology.compiler.catalog import load_catalog
from tests.integration.test_run_sample import _files, _run


def test_two_runs_on_the_same_inputs_are_byte_identical_and_replay(world):
    ctx, cid, _ = world
    a = _run(ctx, cid, kind="nightly")
    b = _run(ctx, cid, kind="nightly")
    b2 = _run(ctx, cid, kind="nightly")
    fb, fc = _files(ctx, cid, b), _files(ctx, cid, b2)
    assert fb.outputs_json.read_bytes() == fc.outputs_json.read_bytes()
    with ctx.sf() as s, tenant_scope(cid):
        runs = {r.id: r for r in s.scalars(sa.select(Run))}
        assert (runs[b].input_hash, runs[b].output_hash) == (
            runs[b2].input_hash,
            runs[b2].output_hash,
        )
        assert verify(s, ctx.settings, cid).ok
        personal = {
            t.name.lower(): t.personal for t in load_catalog(s).tables.values() if t.personal
        }
        sent = s.scalars(sa.select(RunQuery.sql).where(RunQuery.purpose == "run")).all()
    assert sent
    for sql in sent:
        ids = identifiers(sql)
        assert not any(t in ids and cols & ids for t, cols in personal.items()), sql
    with tenant_scope(cid):
        for r in (a, b, b2):
            assert replay(ctx, r).ok


def test_a_run_again_the_same_day_replays_byte_for_byte(world):
    """Run again now (v0.11): a `rerun` on the same date as the night's run is a new run in the
    chain, with Sweep and briefs, and replay reproduces it byte for byte."""
    ctx, cid, _ = world
    _run(ctx, cid, kind="dry")
    night = _run(ctx, cid, kind="nightly")
    again = _run(ctx, cid, kind="rerun")
    with ctx.sf() as s, tenant_scope(cid):
        runs = {r.id: r for r in s.scalars(sa.select(Run))}
        assert runs[again].kind == "rerun" and runs[again].status in ("done", "partial")
        assert runs[again].run_date == runs[night].run_date
        assert runs[again].prev_run_hash == runs[night].run_hash
        assert verify(s, ctx.settings, cid).ok
    assert "sweep" in json.loads(_files(ctx, cid, again).outputs_json.read_bytes())
    assert "briefs" in json.loads(_files(ctx, cid, again).outputs_json.read_bytes())
    with tenant_scope(cid):
        for r in (night, again):
            assert replay(ctx, r).ok
