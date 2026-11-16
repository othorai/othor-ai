# SPDX-License-Identifier: Apache-2.0
"""/_components renders every component and the 9a–9c boards; templates stay style-free."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from othor.core.config import Settings
from othor.migrations.runner import upgrade
from othor.web.app import create_app

TEMPLATES = Path(__file__).resolve().parents[2] / "othor" / "web" / "templates"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    url = f"sqlite:///{tmp_path / 'c.db'}"
    upgrade(url)
    return TestClient(create_app(Settings(_env_file=None, db_url=url, data_dir=str(tmp_path))))  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("path", "must"),
    [
        ("/_components", "Every shared component"),
        ("/_components/9a", "No sources connected"),
        ("/_components/9b", "Interactive states"),
        ("/_components/9c", "Chart library"),
    ],
)
def test_pages_render(client: TestClient, path: str, must: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert must in r.text
    assert "fonts.googleapis" not in r.text and "unpkg" not in r.text  # no CDN


def test_9c_charts_come_from_specs(client: TestClient) -> None:
    html = client.get("/_components/9c").text
    assert html.count("data-chart=") == 10


def test_components_hidden_in_prod(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'p.db'}"
    upgrade(url)
    from othor.core.crypto import InstanceKey

    InstanceKey.load_or_create(tmp_path / "instance.key", allow_create=True)
    app = create_app(
        Settings(
            _env_file=None,
            db_url=url,
            env="prod",
            secret_key="x" * 40,  # type: ignore[call-arg]
            data_dir=str(tmp_path),
            mail_relay="smtp",
            smtp_url="smtp://relay:25",
        )
    )
    assert TestClient(app).get("/_components").status_code == 404


def test_no_inline_styles_in_templates() -> None:
    """Pages use the shared classes only (CLAUDE.md UI quality). Email templates are exempt:
    mail clients need inline CSS."""
    for path in TEMPLATES.rglob("*.html"):
        if "mail" in path.parts:
            continue
        assert not re.search(r'\sstyle\s*=\s*["\']', path.read_text()), path
