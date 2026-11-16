# SPDX-License-Identifier: Apache-2.0
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from othor.core.config import Settings


def make(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[call-arg]


def test_reads_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OTHOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OTHOR_EGRESS_IPS", "203.0.113.24, 203.0.113.25")
    monkeypatch.setenv("OTHOR_FDR_Q", "0.01")
    monkeypatch.setenv("OTHOR_MATERIALITY_FLOOR_INR", "500000")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.data_dir == tmp_path
    assert s.egress_ips == ["203.0.113.24", "203.0.113.25"]
    assert s.fdr_q == Decimal("0.01")
    assert s.materiality_floor_inr == 500000


def test_defaults_are_local_and_offline() -> None:
    s = make()
    assert s.env == "dev"
    assert s.model_provider == "template"
    assert s.mail_relay == "console"


def test_prod_refuses_default_secret() -> None:
    with pytest.raises(ValidationError, match="OTHOR_SECRET_KEY"):
        make(env="prod", secret_key="change-me")
    assert make(env="prod", secret_key="x" * 40).env == "prod"


def test_data_dir_layout(tmp_path: Path) -> None:
    s = make(data_dir=str(tmp_path))
    cid = uuid.UUID(int=7)
    assert s.instance_key_path == tmp_path / "instance.key"
    assert s.company_dir(cid) == tmp_path / "companies" / str(cid)
    assert s.analytics_path(cid) == tmp_path / "companies" / str(cid) / "analytics.duckdb"
    assert s.uploads_dir(cid) == tmp_path / "companies" / str(cid) / "uploads"


def test_secrets_never_in_repr() -> None:
    s = make(secret_key="super-secret-value", model_api_key="sk-abc")
    assert "super-secret-value" not in repr(s)
    assert "sk-abc" not in repr(s)
