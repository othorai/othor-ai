# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures.

Metadata DB tests run on SQLite always and on Postgres (the compose `meta-db`) as well.
If Postgres is unreachable the run fails with "run `make dev` first", unless
OTHOR_TEST_SKIP_DB=1 is set, which skips the Postgres and integration tests on purpose.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.core.db import make_engine, make_session_factory
from othor.core.models import AppUser, Company, Membership
from othor.core.tenancy import system_scope
from othor.migrations.runner import upgrade

PG_ADMIN_URL = os.environ.get(
    "OTHOR_TEST_PG_ADMIN_URL", "postgresql+psycopg://othor:othor@localhost:5442/othor"
)
PG_TEST_DB = "othor_test"
SKIP_DB = os.environ.get("OTHOR_TEST_SKIP_DB") == "1"
# Sign-up by invitation is the cloud default (M11d part C); tests sign up openly unless a test
# sets `signup="invite"` itself.
os.environ.setdefault("OTHOR_SIGNUP", "open")


def _test_licence() -> None:
    """Several companies per install needs an enterprise licence (M20.3). Tests sign companies
    up on subdomains, so the session trusts a throwaway signing key and carries a licence from
    it (multi-company, no limit, no expiry); a test that checks the licence sets its own."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from othor.core import licence

    priv = Ed25519PrivateKey.generate()
    licence.PUBLIC_KEYS = (*licence.PUBLIC_KEYS, licence.public_key_text(priv.public_key()))
    payload = {
        "id": "lic_tests",
        "customer": "Tests",
        "edition": "enterprise",
        "features": ["multi_company"],
        "limits": {},
        "issued": "2026-01-01",
        "expires": None,
    }
    os.environ.setdefault("OTHOR_LICENCE", licence.sign(priv, payload))
    os.environ["OTHOR_TEST_LICENCE_KEY"] = licence.public_key_text(priv.public_key())
    _TEST_SIGNER.append(priv)


_TEST_SIGNER: list[object] = []  # the session's signing key, for tests that issue their own
_test_licence()


def _pg_reachable() -> bool:
    try:
        eng = sa.create_engine(PG_ADMIN_URL, connect_args={"connect_timeout": 2})
        with eng.connect():
            pass
        eng.dispose()
        return True
    except Exception:
        return False


def require_services(what: str) -> None:
    """Fail loudly (or skip when asked) if the compose services are not up."""
    if SKIP_DB:
        pytest.skip(f"{what}: skipped (OTHOR_TEST_SKIP_DB=1)")
    pytest.fail(f"{what} is not reachable: run `make dev` first", pytrace=False)


def _pg_test_url() -> str:
    url = sa.make_url(PG_ADMIN_URL)
    admin = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{PG_TEST_DB}" WITH (FORCE)'))
        c.execute(sa.text(f'CREATE DATABASE "{PG_TEST_DB}"'))
    admin.dispose()
    return url.set(database=PG_TEST_DB).render_as_string(hide_password=False)


@pytest.fixture(params=["sqlite", "postgres"])
def db_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    """A freshly migrated metadata DB (alembic upgrade head), on SQLite and Postgres."""
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'meta.db'}"
    else:
        if not _pg_reachable():
            require_services("metadata Postgres (localhost:5442)")
        url = _pg_test_url()
    from othor.core import plugins
    from othor.core.config import Settings

    if not plugins.loaded():  # as the CLI does: the installed plug-ins' branches are migrated
        plugins.load(Settings(_env_file=None, env="test"))  # type: ignore[call-arg]
    upgrade(url)
    return url


@pytest.fixture
def session_factory(db_url: str) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(db_url)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def two_companies(session_factory: sessionmaker[Session]) -> tuple[uuid.UUID, uuid.UUID]:
    """Companies A (Indigo Thread) and B (Other Co), each with an owner membership."""
    with session_factory() as s, system_scope("test setup"):
        a = Company(slug="indigothread", name="Indigo Thread Co.")
        b = Company(slug="otherco", name="Other Co")
        ua = AppUser(email="uk@indigothread.example", name="Uday Kamath")
        ub = AppUser(email="owner@otherco.in", name="Other Owner")
        s.add_all([a, b, ua, ub])
        s.flush()
        s.add_all(
            [
                Membership(company_id=a.id, user_id=ua.id, role="owner"),
                Membership(company_id=b.id, user_id=ub.id, role="owner"),
            ]
        )
        s.commit()
        return a.id, b.id


# Integration and web tests read the compose sample sources. Make sure they hold the sample
# company before any test reads them, whatever order the tests run in (CI starts empty).
@pytest.fixture(scope="session")
def sample_sources_seeded(tmp_path_factory: pytest.TempPathFactory) -> None:
    from tests.integration.test_seed import DW_ADMIN, ERP_ADMIN, _reachable

    if not (_reachable(ERP_ADMIN) and _reachable(DW_ADMIN)):
        return  # tests that need the sources skip themselves
    import sqlalchemy as sa

    eng = sa.create_engine(ERP_ADMIN)
    with eng.connect() as c:
        has = c.execute(
            sa.text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'erp_db' AND table_name = 'invoices'"
            )
        ).scalar_one()
    eng.dispose()
    if has:
        return
    from tests.fixtures.sample_company.generate import generate
    from tests.fixtures.sample_company.load import load_all

    load_all(
        generate(),
        mysql_url=ERP_ADMIN,
        pg_url=DW_ADMIN,
        sheets_dir=tmp_path_factory.mktemp("sheets"),
    )


@pytest.fixture(autouse=True)
def _source_host_guard_off() -> Iterator[None]:
    """The source-host guard (othor.ai only) is process state set by create_app and the worker;
    a test that turns it on must not leave it on for the next one."""
    yield
    from types import SimpleNamespace

    from othor.connectors.sql import hostguard

    hostguard.configure(SimpleNamespace())
