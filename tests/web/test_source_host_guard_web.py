# SPDX-License-Identifier: Apache-2.0
"""The source-host guard on the connect panel (owner, 16 Nov 2026; othor.ai only): a sample or
private host is refused in place before anything is saved, in plain words, with an event that
never holds the host; a company on the allow-list saves it as before."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from othor.connectors.models import Source
from othor.connectors.sql import hostguard
from othor.core.models import Event
from othor.core.tenancy import system_scope
from tests.web.conftest import Env, make_env, signup

FORM = {
    "host": "erp-db",
    "port": "3306",
    "database": "erp",
    "user": "othor_ro",
    "password": "secret-pw",
    "ssl": "disable",
    "setup": "1",
}


def _save(env: Env, host: str) -> str:
    h = env.host("indigothread")
    panel = "/setup/sources?panel=connect&kind=mysql"
    d = {"csrf": env.csrf(h + panel), **FORM, "host": host}
    r = env.client.post(h + "/sources/sql/mysql/test", data=d, follow_redirects=False)
    assert r.status_code == 200
    return r.text


def _rows(env: Env, model: type) -> list:
    sf = env.app.state.services.sf  # type: ignore[attr-defined]
    with sf() as s, system_scope("test"):
        return list(s.scalars(sa.select(model)))


def test_a_private_or_sample_host_is_refused_before_saving(tmp_path: Path) -> None:
    with make_env(tmp_path, signup="open", source_host_guard=True) as env:
        signup(env)
        for host in ("erp-db", "10.0.4.12", "169.254.169.254", "localhost"):
            text = _save(env, host)
            assert hostguard.MESSAGE in text and 'class="field-error"' in text
            assert "Testing the connection" not in text
        assert _rows(env, Source) == []
        refused = [e for e in _rows(env, Event) if e.action == "source.refused_host"]
        assert [e.after["reason"] for e in refused] == ["sample", "private", "private", "private"]
        kept = " ".join(str(e.after) for e in refused)
        assert "10.0.4.12" not in kept and "erp-db" not in kept


def test_an_allowed_company_saves_the_sample_host(tmp_path: Path) -> None:
    with make_env(
        tmp_path, signup="open", source_host_guard=True, source_host_allow="indigo2,indigothread"
    ) as env:
        signup(env)
        text = _save(env, "erp-db")
        assert hostguard.MESSAGE not in text and "Testing the connection" in text
        assert len(_rows(env, Source)) == 1


def test_off_by_default(tmp_path: Path) -> None:
    with make_env(tmp_path, signup="open") as env:
        signup(env)
        assert "Testing the connection" in _save(env, "erp-db")
