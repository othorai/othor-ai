# SPDX-License-Identifier: Apache-2.0
"""A plug-in's run input (Advanced Intelligence Phase 0 §3): collected from what is stored,
written into the run's inputs before the manifest, hashed there, read by the detectors, and read
back by replay, which never collects again. A run without one keeps its manifest bytes. A
collector that fails leaves the run going without it. Needs the sample sources."""

from __future__ import annotations

import datetime as dt

import pytest

from othor.core.tenancy import tenant_scope
from othor.engine import orchestrator
from othor.engine.detectors import REGISTRY
from othor.engine.detectors.base import Coverage, Result, Spec
from othor.engine.ledger import sha
from othor.engine.replay import replay
from othor.engine.store import read_json
from tests.integration.test_run_sample import _files, _run, world  # noqa: F401

SERIES = {"diesel": [["2026-10-01", "92.10"], ["2026-10-08", "94.40"]]}


def _reads_it(ctx):
    got = ctx.run_inputs.get("demo_outside") or {}
    last = (got.get("diesel") or [["", None]])[-1]
    r = Result(
        detector="demo_outside_seen",
        version="1",
        series=["diesel"],
        metric="",
        breakdown="",
        breakdown_value=None,
        window=(dt.date(2026, 10, 8), dt.date(2026, 10, 8)),
        statistic=None,
        p_value=None,
        effect=None,
        strong=False,
        direction="none",
        money=None,
        money_basis="",
        slots={"last": last[1]},
        eligible=False,
        reason="a record of what it read",
    )
    return [r], Coverage("demo_outside_seen", "ran", 1, 1, "checks")


@pytest.fixture
def outside():
    calls = []

    def collect(ctx, info):
        calls.append(info.id)
        return SERIES

    orchestrator.register_run_input("demo_outside", collect)
    REGISTRY["demo_outside_seen"] = Spec("demo_outside_seen", "1", "Seen", _reads_it)
    yield calls
    orchestrator.RUN_INPUTS.pop("demo_outside", None)
    REGISTRY.pop("demo_outside_seen", None)


def _seen(ctx, cid, rid):
    det = read_json(_files(ctx, cid, rid).outputs / "detection.json")
    return [r for r in det if r["detector"] == "demo_outside_seen"]


def test_a_run_input_is_hashed_read_and_replayed_without_collecting(world, outside):  # noqa: F811
    ctx, cid, _ = world
    rid = _run(ctx, cid, kind="nightly")
    files = _files(ctx, cid, rid)
    stored = files.inputs / "run_input_demo_outside.json"
    assert stored.exists() and outside == [rid]
    doc = read_json(files.manifest)
    assert doc["run_inputs"] == {"demo_outside": sha(stored.read_bytes())}
    (seen,) = _seen(ctx, cid, rid)
    assert seen["slots"]["last"] == "94.40"

    # replay reads the stored file: a collector that would now fail is never called
    def broken(ctx, info):
        raise AssertionError("replay collected")

    orchestrator.RUN_INPUTS.pop("demo_outside")
    orchestrator.register_run_input("demo_outside", broken)
    with tenant_scope(cid):
        assert replay(ctx, rid).ok


def test_without_a_run_input_the_manifest_has_no_key(world):  # noqa: F811
    ctx, cid, _ = world
    rid = _run(ctx, cid, kind="nightly")
    assert "run_inputs" not in read_json(_files(ctx, cid, rid).manifest)


def test_a_failing_collector_never_blocks_the_run(world, outside):  # noqa: F811
    ctx, cid, _ = world

    def broken(ctx, info):
        raise RuntimeError("feed down")

    orchestrator.RUN_INPUTS.pop("demo_outside")
    orchestrator.register_run_input("demo_outside", broken)
    rid = _run(ctx, cid, kind="nightly")
    files = _files(ctx, cid, rid)
    assert not (files.inputs / "run_input_demo_outside.json").exists()
    assert "run_inputs" not in read_json(files.manifest)
    (seen,) = _seen(ctx, cid, rid)
    assert seen["slots"]["last"] is None
    with tenant_scope(cid):
        assert replay(ctx, rid).ok


def test_after_seal_runs_once_the_run_is_sealed(world, monkeypatch):  # noqa: F811
    """A plug-in's after_seal hook (Phase 0 §1) runs after a worker's run, with no model; one
    that fails is logged and never fails the run."""
    from othor.core import plugins
    from othor.core.plugins import Plugin
    from othor.engine.models import Run
    from othor.jobs.queue import enqueue
    from othor.jobs.runner import WorkerContext, run_pending

    ctx, cid, _ = world
    seen = []

    def broken(wctx, company_id, run_id):
        raise RuntimeError("bad plug-in")

    monkeypatch.setattr(
        plugins,
        "_loaded",
        (Plugin("demo", after_seal=(broken, lambda w, c, r: seen.append((c, r)))),),
    )
    with ctx.sf() as s, tenant_scope(cid):
        enqueue(s, "run", {"kind": "manual", "run_date": "2026-11-01"})
        s.commit()
    run_pending(WorkerContext(ctx.settings, ctx.sf, ctx.key, clock=ctx.clock))
    import sqlalchemy as sa

    with ctx.sf() as s, tenant_scope(cid):
        (run,) = s.scalars(sa.select(Run)).all()
        assert run.status in ("done", "partial")
    assert seen == [(cid, run.id)]
