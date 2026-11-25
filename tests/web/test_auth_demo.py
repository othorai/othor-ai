# SPDX-License-Identifier: Apache-2.0
"""`make demo`'s one-time sign-in link (owner, 25 Nov 2026): 15 minutes, once, dev only. On any
other OTHOR_ENV the address doesn't exist and `othor demo` refuses to run."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from othor.core import auth
from othor.core.models import AppUser, Company, Membership
from othor.core.tenancy import system_scope
from tests.web.conftest import Env, make_env, signup


@pytest.fixture
def denv(tmp_path: Path) -> Iterator[Env]:
    with make_env(tmp_path, env="dev") as e:
        yield e


def _auth(env: Env) -> auth.AuthService:
    return env.app.state.services.auth  # type: ignore[attr-defined, no-any-return]


def _ids(env: Env, slug: str = "indigothread") -> tuple[uuid.UUID, uuid.UUID]:
    sf = env.app.state.services.sf  # type: ignore[attr-defined]
    with sf() as s, system_scope("test"):
        c = s.scalars(sa.select(Company).where(Company.slug == slug)).one()
        m = s.scalars(sa.select(Membership).where(Membership.company_id == c.id)).one()
        return m.user_id, c.id


def _link(env: Env, slug: str = "indigothread") -> str:
    uid, cid = _ids(env, slug)
    return _auth(env).create_handoff(uid, cid, ttl=auth.DEMO_LINK_TTL)


def _go(env: Env, path: str) -> str:
    r = env.client.get(f"{env.host('indigothread')}{path}", follow_redirects=False)
    assert r.status_code == 303, r.status_code
    return str(r.headers["location"])


def _signed_in(env: Env) -> bool:
    r = env.client.get(f"{env.host('indigothread')}/settings", follow_redirects=False)
    return r.status_code == 200


def test_the_link_signs_in_once(denv: Env) -> None:
    signup(denv)
    denv.client.cookies.clear()
    assert not _signed_in(denv)
    t = _link(denv)
    denv.clock.tick(minutes=14)  # still inside the 15 minutes
    assert _go(denv, f"/auth/demo?t={t}") != "/login"
    assert _signed_in(denv)
    assert any(e.after == {"via": "demo link"} for e in denv.events("auth.signed_in"))
    denv.client.cookies.clear()
    assert _go(denv, f"/auth/demo?t={t}") == "/login"  # once
    assert not _signed_in(denv)


def test_an_expired_or_another_companys_link_goes_to_sign_in(denv: Env) -> None:
    signup(denv)
    signup(denv, email="a@acme.example", company="Acme", slug="acme", name="Asha Rao")
    denv.client.cookies.clear()
    other = _link(denv, "acme")  # another company's link on this company's address
    assert _go(denv, f"/auth/demo?t={other}") == "/login"
    t = _link(denv)
    denv.clock.tick(minutes=16)
    assert _go(denv, f"/auth/demo?t={t}") == "/login"
    assert not _signed_in(denv)


def test_the_two_links_dont_cross(denv: Env) -> None:
    """The signup handoff's route refuses a demo link, and the demo route a signup handoff."""
    signup(denv)
    denv.client.cookies.clear()
    t = _link(denv)
    assert _go(denv, f"/auth/handoff?t={t}") == "/login"
    uid, cid = _ids(denv)
    short = _auth(denv).create_handoff(uid, cid)
    assert _go(denv, f"/auth/demo?t={short}") == "/login"
    assert not _signed_in(denv)
    # the refused demo link is still unused: the demo route takes it
    assert _go(denv, f"/auth/demo?t={t}") != "/login"
    assert _signed_in(denv)


def test_only_the_two_lifetimes(denv: Env) -> None:
    signup(denv)
    uid, cid = _ids(denv)
    with pytest.raises(ValueError):
        _auth(denv).create_handoff(uid, cid, ttl=dt.timedelta(days=1))


@pytest.mark.parametrize("othor_env", ["test", "prod"])
def test_outside_dev_the_address_is_not_there(tmp_path: Path, othor_env: str) -> None:
    from othor.core.crypto import InstanceKey

    extra: dict[str, Any] = {"secret_key": "x" * 48} if othor_env == "prod" else {}
    InstanceKey.load_or_create(tmp_path / "instance.key", allow_create=True)  # prod never makes one
    with make_env(tmp_path, env=othor_env, **extra) as e:
        # prod's cookies are Secure, so the company is made here, not through sign-up over http
        sf = e.app.state.services.sf  # type: ignore[attr-defined]
        with sf() as s, system_scope("test"):
            c = Company(slug="indigothread", name="Indigo Thread Co.")
            u = AppUser(email="uk@indigothread.example", name="Uday Kamath")
            s.add_all([c, u])
            s.flush()
            c.workspace_id = c.id
            s.add(Membership(company_id=c.id, user_id=u.id, role="owner"))
            s.commit()
        t = _link(e)
        e.client.cookies.clear()
        r = e.client.get(f"{e.host('indigothread')}/auth/demo?t={t}", follow_redirects=False)
        assert r.status_code == 404
        assert not _signed_in(e)


@pytest.mark.parametrize("othor_env", ["test", "prod"])
def test_othor_demo_refuses_outside_dev(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, othor_env: str
) -> None:
    from othor.cli import app
    from othor.core.config import get_settings

    monkeypatch.setenv("OTHOR_ENV", othor_env)
    monkeypatch.setenv("OTHOR_SECRET_KEY", "x" * 48)
    monkeypatch.setenv("OTHOR_DB_URL", f"sqlite:///{tmp_path / 'never.db'}")
    monkeypatch.setenv("OTHOR_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        r = CliRunner().invoke(app, ["demo"])
    finally:
        get_settings.cache_clear()
    assert r.exit_code == 2 and "dev-only" in r.output
    assert not (tmp_path / "never.db").exists()
