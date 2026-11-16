# SPDX-License-Identifier: Apache-2.0
"""The metadata DB unreachable or behind the migration head (M11a): a static maintenance page
and /healthz 503, never a 500; `dev` migrates on start."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from othor.core.config import Settings
from othor.mail.relay import MemoryRelay
from othor.migrations.runner import current, downgrade, head, upgrade
from othor.web.app import create_app
from tests.web.conftest import BASE, ROOT

MAINT = "Othor AI is starting up or under maintenance. Try again in a minute."


def _settings(tmp_path: Path, url: str, env: str) -> Settings:
    return Settings(
        _env_file=None,
        db_url=url,
        data_dir=str(tmp_path),
        env=env,  # type: ignore[arg-type]
        base_domain=BASE,
        mail_relay="memory",
        mail_from_domain="othor.localhost",
    )


def test_behind_head_serves_maintenance_outside_dev(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade(url)
    downgrade(url, "-1")
    app = create_app(_settings(tmp_path, url, "test"), relay=MemoryRelay())
    with TestClient(app, base_url=ROOT, raise_server_exceptions=False) as c:
        r = c.get(f"{ROOT}/login")
        assert r.status_code == 503 and MAINT in r.text
        assert c.get(f"{ROOT}/healthz").status_code == 503
        assert c.get(f"{ROOT}/healthz").json()["status"] in ("migrating", "unavailable")
        assert c.get(f"{ROOT}/static/maintenance.html").status_code == 200
        # a deploy-step migrate clears it without a restart
        upgrade(url)
        app.state.schema.checked = 0.0
        assert c.get(f"{ROOT}/healthz").status_code == 200
        assert c.get(f"{ROOT}/login").status_code == 200


def test_dev_migrates_on_start(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'd.db'}"
    upgrade(url)
    downgrade(url, "-1")
    app = create_app(_settings(tmp_path, url, "dev"), relay=MemoryRelay())
    with TestClient(app, base_url=ROOT) as c:
        assert c.get(f"{ROOT}/healthz").status_code == 200
    from othor.core.db import make_engine

    assert current(make_engine(url)) == head()


def test_unreachable_db_is_maintenance_not_500(tmp_path: Path) -> None:
    url = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none?connect_timeout=1"
    app = create_app(_settings(tmp_path, url, "test"), relay=MemoryRelay())
    with TestClient(app, base_url=ROOT, raise_server_exceptions=False) as c:
        r = c.get("http://indigothread.othor.localhost/home")
        assert r.status_code == 503 and MAINT in r.text
        assert "Traceback" not in r.text
        h = c.get(f"{ROOT}/healthz")
        assert h.status_code == 503 and h.json() == {"status": "unavailable"}
