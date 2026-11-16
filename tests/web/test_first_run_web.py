# SPDX-License-Identifier: Apache-2.0
"""2o · First run (M5): Confirm queues dry run #0001; the page follows its steps, shows the
headline numbers it computed and its provenance line; the Forecasts (M7) and Brief (M8) columns,
"First brief tomorrow" and Open the brief; a stopped run offers Run again. No model needed."""

from __future__ import annotations

import sqlalchemy as sa

from othor.core.models import Company, SetupState
from othor.core.tenancy import tenant_scope
from othor.engine.models import Run, RunStep
from othor.jobs.models import Job
from othor.setup import confirm
from othor.setup.models import SetupDraft
from tests.integration import setup_world as w
from tests.web.conftest import Env
from tests.web.test_chat_web import _drafted, _jobs, _post, need_sources, sample  # noqa: F401

SHOWN = (
    "Open the brief",
    "First brief tomorrow at 6:00 AM IST",
    "Add recipients and more briefs from Morning Brief.",
)  # M8


def _confirmed(env: Env, sample, tmp_path):  # noqa: F811
    h, cid = _drafted(env, sample, tmp_path)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, tenant_scope(cid):
        for r in s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "metric")):
            if r.confidence == "conflict":
                confirm.choose(s, r, "source", actor=w.OWNER)
        s.commit()
    r = _post(env, h, "/setup/metrics/confirm", {})
    assert r.headers["location"] == "/setup/first-run"
    return h, cid, svc


def test_first_run_follows_dry_run_0001(env: Env, sample, need_sources, tmp_path):  # noqa: F811
    h, cid, svc = _confirmed(env, sample, tmp_path)
    page = env.client.get(h + "/setup/first-run").text
    # M11c (owner's O3): one card of stages, Run details folded, no preview
    assert "Writing your first brief." in page and "#0001 · dry run" in page
    assert "Starting" in page and 'hx-trigger="every 1s"' in page
    assert "Go to the app — we’ll tell you when it’s ready" in page
    with svc.sf() as s, tenant_scope(cid):
        job = s.scalars(sa.select(Job).where(Job.dedupe_key == confirm.FIRST_RUN_KEY)).one()
        assert job.kind == "run" and job.payload["kind"] == "dry"
    _jobs(env)
    page = env.client.get(h + "/setup/first-run").text
    with svc.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run)).one()
        assert run.status == "done" and run.number == 1
        sent = run.summary["queries"]
    assert f"{sent} of {sent} ·" in page  # every planned query sent
    assert "Your first brief is ready." in page and "5 of 5 done" in page
    for stage in ("Read your sources", "Learn what’s normal", "Check for anything unusual"):
        assert stage in page, stage
    assert " metrics · " in page and " days" in page
    assert " of 11 · " in page and " tests" in page  # M6b, M7
    assert "Forecasts" in page and "Write the brief" in page  # M7, M8
    assert "What the Leadership brief will say" not in page  # no preview (owner, 8 Nov)
    assert "Run details" in page
    assert "dot accent" not in page  # status dots are never accent (docs/07)
    assert f"#0001 · spec v1 · image {run.image_version} · sha " in page
    assert f"hash {run.run_hash[:4]}…{run.run_hash[-2:]}" in page
    assert 'hx-trigger="every 1s"' not in page  # done: no more polling
    for word in SHOWN:  # M8: the Brief column, the callout and the button (2o)
        assert word in page, word
    assert "Brief" in page and "written" in page
    # Sources: the side pane lists the confirmed metrics this source feeds (6c)
    from othor.connectors.models import Source

    with svc.sf() as s, tenant_scope(cid):
        erp = s.scalars(sa.select(Source.id).where(Source.name == "erp_db")).one()
    # the numbers a source feeds are in its Manage panel (owner, 12 Nov 2026: nothing docked)
    pane = env.client.get(f"{h}/sources?panel=manage&s={erp}").text
    assert "Numbers it feeds · " in pane
    assert pane.count(">confirmed<") >= 3
    # a newer Ontology version that no run has compiled yet: the pane falls back to the latest
    # compiled spec instead of going empty (Indigo Thread on production, 11 Nov 2026)
    from othor.ontology.models import OntologyVersion

    with svc.sf() as s, tenant_scope(cid):
        top = s.scalar(sa.select(sa.func.max(OntologyVersion.number)))
        s.add(OntologyVersion(number=top + 1, created_by="test", reason="not compiled yet"))
        s.commit()
    pane = env.client.get(f"{h}/sources?panel=manage&s={erp}").text
    assert "Numbers it feeds · " in pane
    part = env.client.get(h + "/setup/first-run/progress").text
    assert 'id="first-run"' in part and "<html" not in part
    r = _post(env, h, "/setup/first-run/continue", {})
    assert r.headers["location"] == "/home"
    with svc.sf() as s, tenant_scope(cid):
        assert s.scalars(sa.select(SetupState)).one().current_step == "done"


def test_a_stopped_first_run_offers_run_again(env: Env, sample, need_sources, tmp_path):  # noqa: F811
    h, cid, svc = _confirmed(env, sample, tmp_path)
    _jobs(env)
    with svc.sf() as s, tenant_scope(cid):  # as if it had stopped while querying sales_dw
        run = s.scalars(sa.select(Run)).one()
        run.status, run.run_hash = "running", None
        st = s.scalars(
            sa.select(RunStep).where(
                RunStep.run_id == run.id, RunStep.name == "materialise:sales_dw"
            )
        ).one()
        st.status = "failed"
        for later in s.scalars(
            sa.select(RunStep).where(RunStep.run_id == run.id, RunStep.ordinal > st.ordinal)
        ):
            later.status = "queued"
        s.scalars(sa.select(Job).where(Job.kind == "run")).one().status = "failed"
        s.commit()
    page = env.client.get(h + "/setup/first-run").text
    assert "The run stopped while querying sales_dw." in page and "Run again" in page
    assert "Go to the app" in page
    r = _post(env, h, "/setup/first-run/retry", {})
    assert r.headers["location"] == "/setup/first-run"
    with svc.sf() as s, tenant_scope(cid):
        assert s.scalars(sa.select(Job).where(Job.kind == "run")).one().status == "queued"
    assert [e.after for e in env.events("run.retried")] == [{"status": "queued"}]
    _jobs(env)
    with svc.sf() as s, tenant_scope(cid):
        assert s.scalars(sa.select(Run)).one().status == "done"
        assert s.get(Company, cid) is not None
    assert "The run stopped" not in env.client.get(h + "/setup/first-run").text


def test_leaving_before_the_brief_is_written(env: Env, sample, need_sources, tmp_path):  # noqa: F811
    """M11c (owner, 8 Nov): "Go to the app — we'll tell you when it's ready" holds. The app shows
    a strip while the run writes the first brief, Morning Brief shows its stages, and the strip
    says when the brief is ready; opened, it goes."""
    h, _cid, _svc = _confirmed(env, sample, tmp_path)
    r = _post(env, h, "/setup/first-run/continue", {})
    assert r.headers["location"] == "/home" and "othor_first_run=1" in r.headers["set-cookie"]
    home = env.client.get(h + "/home").text
    assert 'hx-get="/first-run/strip"' in home
    strip = env.client.get(h + "/first-run/strip").text
    assert "Writing your first brief" in strip and 'hx-trigger="every 2s"' in strip
    brief = env.client.get(h + "/brief").text
    assert "Writing your first brief" in brief and "Read your sources" in brief
    assert "The first brief is written after the first nightly run." not in brief
    _jobs(env)
    strip = env.client.get(h + "/first-run/strip").text
    assert "Your first brief is ready." in strip and 'href="/first-run/open"' in strip
    assert "every 2s" not in strip
    r = env.client.get(h + "/first-run/open", follow_redirects=False)
    assert r.headers["location"] == "/brief" and "othor_first_run=" in r.headers["set-cookie"]
    assert 'hx-get="/first-run/strip"' not in env.client.get(h + "/home").text
