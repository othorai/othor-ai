# SPDX-License-Identifier: Apache-2.0
"""Plug-ins in the running app (M20.1): a plug-in's page, menu item, fragments inside open
features, settings field, data-source kind, job, command, prompt, record type and migration
branch; and without plug-ins, no trace of any."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from othor.core import plugins
from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from tests.web.conftest import make_env, signup

DEMO = "tests.plugins.demo_plugin:plugin"


@pytest.fixture(autouse=True)
def fresh():
    plugins.reset()
    yield
    plugins.reset()


def test_every_hook_reaches_the_running_app(tmp_path: Path) -> None:
    with make_env(tmp_path, plugins=DEMO) as env:
        signup(env)
        h = env.host("indigothread")
        c = env.client
        # its page answers before the core's catch-all, and its static file is served
        assert "The demo plug-in page" in c.get(h + "/plugin-demo").text
        assert c.get(h + "/static/p/demo/hello.txt").text.strip() == "demo static"
        home = c.get(h + "/home").text
        # its menu item sits in the company group, right after Summary
        assert (
            home.index('href="/summary"')
            < home.index('href="/plugin-demo"')
            < home.index(">Setup<")
        )
        # its badge count sits on its item (Advanced Intelligence Phase 0)
        demo_item = home[home.index('href="/plugin-demo"') : home.index(">Setup<")]
        assert 'class="nav-badge"' in demo_item and ">3<" in demo_item
        # its fragment fills a slot with the page's context and its own
        from othor.web.templating import templates

        got = templates.env.from_string('{{ slot("home.end") }}').render(company={"name": "Acme"})
        assert "Hi from the demo plug-in · Acme" in got
        settings = c.get(h + "/settings").text
        assert "data-demo-advanced" in settings
        assert settings.index("data-demo-advanced") < settings.index('aria-label="Delivery"')
        # its field saves through the core's settings path, with an event
        tok = env.csrf(h + "/settings")
        bad = c.post(h + "/settings", data={"csrf": tok, "field": "demo_level", "value": "x"})
        assert bad.status_code == 400 and "Pick basic or advanced." in bad.text
        ok = c.post(h + "/settings", data={"csrf": tok, "field": "demo_level", "value": "advanced"})
        assert ok.status_code == 204
        svc = env.app.state.services  # type: ignore[attr-defined]
        with svc.sf() as s:
            cid = uuid.UUID(str(s.scalar(sa.text("select id from company"))))
        with svc.sf() as s, tenant_scope(cid):
            ev = s.scalars(sa.select(Event).where(Event.action == "settings.changed")).all()
            assert any((e.after or {}).get("demo_level") == "advanced" for e in ev)
            # its migration branch was applied
            s.execute(sa.text("select count(*) from demo_note")).scalar()
        # its data-source kind is in "Add a source" and in the registry
        add = c.get(h + "/sources?panel=add").text
        assert "data-demo-kind" in add
        # Phase 0's slots: the end of Data sources and Edit brief's scope tab
        assert "data-demo-sources-end" in c.get(h + "/sources").text
        got = templates.env.from_string('{{ slot("brief_edit.scope") }}').render()
        assert "data-demo-brief-scope" in got
        # its outbound host is allowed while it is loaded
        from othor.core.http import allowed_hosts

        assert "demo-feed.example" in allowed_hosts()
        from othor.connectors import kinds

        assert kinds.get("demo_feed").family == "local" and kinds.is_local("demo_feed")
        # its prompt is found after the core's; its record type validates
        from othor.ai.gateway import load_prompt
        from othor.ontology.types import validate_body

        assert load_prompt("demo_prompt", "v1")[0].startswith("You are the demo prompt")
        assert validate_body("demo_note", {"text": "hi"}) == {"text": "hi"}


def test_its_job_runs_in_the_worker(tmp_path: Path) -> None:
    from othor.jobs.queue import enqueue
    from othor.jobs.runner import WorkerContext, run_pending

    with make_env(tmp_path, plugins=DEMO) as env:
        signup(env)
        svc = env.app.state.services  # type: ignore[attr-defined]
        with svc.sf() as s:
            cid = uuid.UUID(str(s.scalar(sa.text("select id from company"))))
        with svc.sf() as s, tenant_scope(cid):
            enqueue(s, "demo_job", {})
            s.commit()
        run_pending(WorkerContext(svc.settings, svc.sf, svc.key, clock=svc.clock))
        with svc.sf() as s, tenant_scope(cid):
            assert s.scalars(sa.select(Event).where(Event.action == "demo.ran")).first()


def test_its_command_joins_the_cli() -> None:
    from othor import cli  # importing it loads the default plug-ins first
    from othor.core.config import Settings

    plugins.reset()
    plugins.load(Settings(_env_file=None, env="test", plugins=DEMO))  # type: ignore[call-arg]
    cli._plugin_commands()  # as at import, with the demo plug-in loaded
    out = CliRunner().invoke(cli.app, ["demo", "hello"])
    assert out.exit_code == 0, out.output
    assert "hello from the demo plug-in" in out.output


def test_without_plugins_no_trace(tmp_path: Path) -> None:
    with make_env(tmp_path, plugins="none") as env:
        signup(env)
        h = env.host("indigothread")
        for path in ("/home", "/settings", "/sources?panel=add", "/sources"):
            page = env.client.get(h + path).text
            assert "demo" not in page.lower(), path
            assert "nav-badge" not in page, path
        assert env.client.get(h + "/plugin-demo").status_code == 404


def test_a_plugins_own_subdomain_moves_to_the_root_host(tmp_path: Path) -> None:
    """Plugin.host_redirects (23 Nov 2026): `<label>.<base>` answers a 301 to the path the
    plug-in gives, on the root host, before any company routing; without it, an unknown host."""
    from tests.web.conftest import BASE, ROOT

    with make_env(tmp_path, plugins=DEMO) as env:
        r = env.client.get(f"http://old-demo.{BASE}/a/b?x=1", follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == f"{ROOT}/moved/a/b"
        other = env.client.get(f"http://not-demo.{BASE}/a", follow_redirects=False)
        assert other.status_code != 301
    plugins.reset()
    (tmp_path / "none").mkdir()
    with make_env(tmp_path / "none", plugins="none") as env:
        r = env.client.get(f"http://old-demo.{BASE}/a", follow_redirects=False)
        assert r.status_code != 301
