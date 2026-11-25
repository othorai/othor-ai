# SPDX-License-Identifier: Apache-2.0
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from othor.cli import app
from othor.core.config import Settings
from othor.migrations.runner import upgrade
from othor.web.app import create_app


@pytest.fixture
def cli_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A throwaway metadata DB for the CLI (never the dev database from .env)."""
    from othor.core import config

    url = f"sqlite:///{tmp_path / 'cli.db'}"
    upgrade(url)
    monkeypatch.setenv("OTHOR_DB_URL", url)
    monkeypatch.setenv("OTHOR_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    yield url
    config.get_settings.cache_clear()


def test_run_resume_replay_and_ledger_say_what_is_missing(cli_db: str) -> None:
    from othor.core.db import make_engine, make_session_factory
    from othor.core.models import Company
    from othor.core.tenancy import system_scope

    sf = make_session_factory(make_engine(cli_db))
    with sf() as s, system_scope("test"):
        s.add(Company(slug="indigothread", name="Indigo Thread Co."))
        s.commit()
    run = CliRunner().invoke(app, ["run", "--company", "indigothread", "--date", "2026-11-02"])
    assert run.exit_code == 1 and "Nothing to run: no confirmed Ontology." in run.output
    other = CliRunner().invoke(app, ["run", "--company", "nobody", "--date", "2026-11-02"])
    assert other.exit_code == 1 and "No company 'nobody'." in other.output
    rid = "00000000-0000-0000-0000-000000000001"
    for cmd in (["replay", rid], ["resume", rid]):
        res = CliRunner().invoke(app, cmd)
        assert res.exit_code == 1 and f"No run {rid}." in res.output, cmd
    res = CliRunner().invoke(app, ["ledger", "verify", "--company", "indigothread"])
    assert res.exit_code == 0 and "0 runs · chain intact" in res.output


def test_worker_once_runs_queued_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from othor.core import config
    from othor.migrations.runner import upgrade

    url = f"sqlite:///{tmp_path / 'w.db'}"
    upgrade(url)
    monkeypatch.setenv("OTHOR_DB_URL", url)
    monkeypatch.setenv("OTHOR_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    res = CliRunner().invoke(app, ["worker", "--once"])
    config.get_settings.cache_clear()
    assert res.exit_code == 0, res.output
    assert "0 jobs run" in res.output


def test_migrate_creates_key_and_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from othor.core import config

    monkeypatch.setenv("OTHOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OTHOR_DB_URL", f"sqlite:///{tmp_path / 'm.db'}")
    monkeypatch.setenv("OTHOR_INSTANCE_KEY_FILE", str(tmp_path / "instance.key"))  # beat .env
    config.get_settings.cache_clear()
    try:
        res = CliRunner().invoke(app, ["migrate"])
    finally:
        config.get_settings.cache_clear()
    assert res.exit_code == 0, res.output
    assert (tmp_path / "instance.key").exists()


def test_healthz(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'h.db'}"
    upgrade(url)
    client = TestClient(create_app(Settings(_env_file=None, db_url=url, data_dir=str(tmp_path))))  # type: ignore[call-arg]
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_seed_rejects_unknown_scale() -> None:
    res = CliRunner().invoke(app, ["seed", "--scale", "huge"])
    assert res.exit_code != 0
    assert "default or large" in res.output


def test_load_all_passes_scale_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tests.fixtures.sample_company import load

    calls: list[str] = []
    monkeypatch.setattr(load, "load_mysql", lambda *a, **k: {})
    monkeypatch.setattr(load, "load_postgres", lambda *a, **k: {})
    monkeypatch.setattr(load, "write_sheets", lambda *a, **k: {})
    from tests.fixtures.sample_company import uploads

    monkeypatch.setattr(uploads, "write_uploads", lambda *a, **k: [])
    monkeypatch.setattr(load, "load_large", lambda *a, **k: calls.append("large") or {})
    monkeypatch.setattr(load, "_drop_large", lambda *a, **k: calls.append("drop"))
    stub = types.SimpleNamespace(erp={}, dw={})
    for scale in ("large", "default"):
        load.load_all(stub, mysql_url="", pg_url="", sheets_dir=tmp_path, scale=scale)  # type: ignore[arg-type]
    assert calls == ["large", "drop"]
    with pytest.raises(ValueError, match="default or large"):
        load.load_all(stub, mysql_url="", pg_url="", sheets_dir=tmp_path, scale="huge")  # type: ignore[arg-type]


def test_a_closed_company_is_refused_and_reopened_by_support(cli_db: str) -> None:
    """M18.2: the CLI leaves a closed company alone; `admin reopen` opens it before its purge."""
    import datetime as dt

    import sqlalchemy as sa

    from othor.core.db import make_engine, make_session_factory
    from othor.core.models import Company
    from othor.core.tenancy import system_scope

    sf = make_session_factory(make_engine(cli_db))
    now = dt.datetime.now(dt.UTC)
    with sf() as s, system_scope("test"):
        s.add(
            Company(
                slug="shut",
                name="Shut Co.",
                deleted_at=now,
                purge_after=now + dt.timedelta(days=30),
            )
        )
        s.add(Company(slug="gone", name="Gone Co.", deleted_at=now, purge_after=now))
        s.commit()
    run = CliRunner().invoke(app, ["run", "--company", "shut", "--date", "2026-11-02"])
    assert (
        run.exit_code == 1 and "shut is closed" in run.output and "admin reopen shut" in run.output
    )
    late = CliRunner().invoke(app, ["admin", "reopen", "gone", "--yes"])
    assert late.exit_code == 1 and "past its purge date" in late.output
    ok = CliRunner().invoke(app, ["admin", "reopen", "shut", "--yes"])
    assert ok.exit_code == 0 and "open again" in ok.output
    with sf() as s, system_scope("test"):
        c = s.scalars(sa.select(Company).where(Company.slug == "shut")).one()
        assert c.deleted_at is None and c.purge_after is None


def test_admin_reread_asks_the_next_run_and_logs_it(cli_db: str) -> None:
    """0.28.3: `othor admin reread` leaves a request the next run reads (and clears on seal),
    and an event; an unknown company is refused."""
    import sqlalchemy as sa

    from othor.core.config import get_settings
    from othor.core.db import make_engine, make_session_factory
    from othor.core.models import Company, Event
    from othor.core.tenancy import system_scope
    from othor.engine.sources import REREAD
    from othor.engine.store import read_json

    sf = make_session_factory(make_engine(cli_db))
    with sf() as s, system_scope("test"):
        s.add(Company(slug="cut", name="Cut Co."))
        s.commit()
        cid = s.scalars(sa.select(Company.id).where(Company.slug == "cut")).one()
    assert CliRunner().invoke(app, ["admin", "reread", "nope", "--yes"]).exit_code == 1
    ok = CliRunner().invoke(app, ["admin", "reread", "cut", "--metric", "cogs", "--yes"])
    assert ok.exit_code == 0, ok.output
    assert read_json(get_settings().company_dir(cid) / REREAD)["metrics"] == ["cogs"]
    with sf() as s, system_scope("test"):
        ev = s.scalars(sa.select(Event).where(Event.action == "series.reread_requested")).one()
        assert ev.after == {"metrics": ["cogs"]}


def test_migrate_create_key_only_on_a_fresh_install(
    cli_db: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Self-host first start (M20.4): prod refuses to invent an instance key, except with
    --create-key on a database with no company yet; never over existing companies."""
    from othor.core import config
    from othor.core.db import make_engine, make_session_factory
    from othor.core.models import Company
    from othor.core.tenancy import system_scope

    monkeypatch.setenv("OTHOR_ENV", "prod")
    monkeypatch.setenv("OTHOR_SECRET_KEY", "x" * 40)
    key = tmp_path / "instance.key"
    monkeypatch.setenv("OTHOR_INSTANCE_KEY_FILE", str(key))
    config.get_settings.cache_clear()
    assert CliRunner().invoke(app, ["migrate"]).exit_code != 0 and not key.exists()
    res = CliRunner().invoke(app, ["migrate", "--create-key"])
    assert res.exit_code == 0, res.output
    assert key.exists() and oct(key.stat().st_mode & 0o777) == "0o600"
    # the data volume lost, the database kept: no new key
    key.unlink()
    sf = make_session_factory(make_engine(cli_db))
    with sf() as s, system_scope("test"):
        s.add(Company(slug="acme", name="Acme"))
        s.commit()
    res = CliRunner().invoke(app, ["migrate", "--create-key"])
    assert res.exit_code == 1 and "restore /data" in res.output and not key.exists()


def test_web_runs_the_configured_number_of_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load test, 25 Nov 2026: othor.ai runs 2 web processes (OTHOR_WEB_WORKERS); 1 by default
    and always 1 with --reload (dev)."""
    import uvicorn

    from othor.core import config

    got: list[dict[str, object]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *_a, **kw: got.append(kw))
    monkeypatch.delenv("OTHOR_WEB_WORKERS", raising=False)
    config.get_settings.cache_clear()
    try:
        assert CliRunner().invoke(app, ["web"]).exit_code == 0
        monkeypatch.setenv("OTHOR_WEB_WORKERS", "2")
        config.get_settings.cache_clear()
        assert CliRunner().invoke(app, ["web"]).exit_code == 0
        assert CliRunner().invoke(app, ["web", "--reload"]).exit_code == 0
        assert CliRunner().invoke(app, ["web", "--workers", "3"]).exit_code == 0
    finally:
        config.get_settings.cache_clear()
    assert [kw["workers"] for kw in got] == [1, 2, 1, 3]
    assert all(kw["factory"] is True for kw in got)
