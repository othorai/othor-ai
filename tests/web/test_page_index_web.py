# SPDX-License-Identifier: Apache-2.0
"""The pages' index beside each sealed run (load test, 25 Nov 2026): the worker writes it after
the seal, outside the ledger (the run still replays byte for byte); Sweep, a finding, the Business
Model, a metric and the brief for a run not in the web's memory load it and never rebuild from
the series file. Needs `make dev && make seed`."""

from __future__ import annotations

import re

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from othor.engine import page_index, store
from othor.engine.models import Run
from othor.engine.replay import replay
from othor.engine.store import RunFiles
from othor.web import run_cache
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_run_again import AT_1540, _brief, _press, _work, env_ctx
from tests.web.test_sweep_web import night  # noqa: F401


def test_a_sealed_run_has_its_page_index_and_still_replays(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    bid = _brief(svc, cid)
    env.clock.t = AT_1540
    assert _press(env, h, f"/brief/{bid}").status_code == 303
    _work(env, svc)  # the run job: run, seal, then the page index
    with svc.sf() as s, tenant_scope(cid):
        rerun = s.scalars(sa.select(Run).where(Run.kind == "rerun")).one()
        assert rerun.status == "done"
    files = RunFiles(svc.settings.runs_dir(cid), rerun.id)
    assert page_index.path_for(files.series).exists()
    page_index.load(files.series)  # whole, and built from this run's series file
    with tenant_scope(cid):
        assert replay(env_ctx(svc, env), rerun.id).ok  # byte for byte, the file beside it
    page_index.path_for(files.series).unlink()  # deleting it is harmless
    with tenant_scope(cid):
        assert replay(env_ctx(svc, env), rerun.id).ok
    run_cache.clear()
    assert env.client.get(h + "/sweep").status_code == 200
    assert page_index.path_for(files.series).exists()  # built again by the page, once


def test_pages_for_a_run_not_in_memory_never_rebuild_it(night, monkeypatch):  # noqa: F811
    env, h, cid, rid, svc = night
    series = RunFiles(svc.settings.runs_dir(cid), rid).series
    page_index.build(series)
    bid = _brief(svc, cid)
    sweep = env.client.get(h + "/sweep").text
    fp = re.search(r"/sweep/finding/([0-9a-f]{8,})", sweep)
    assert fp
    pages = [
        "/sweep",
        f"/sweep/finding/{fp.group(1)}",
        "/ontology",
        "/ontology?metric=net_revenue",
        f"/brief/{bid}",
        f"/brief/{bid}/edit?tab=numbers",
        "/summary",
    ]
    run_cache.clear()  # as when other companies have pushed this run out
    store.clear_cache()

    def rebuilt(*_a: object) -> None:
        raise AssertionError("a page rebuilt the run's points from the series file")

    monkeypatch.setattr(page_index, "_arrays", rebuilt)
    monkeypatch.setattr(store, "_read_file", rebuilt)
    for path in pages:
        r = env.client.get(h + path)
        assert r.status_code == 200, path
