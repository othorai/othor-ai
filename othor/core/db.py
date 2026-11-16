# SPDX-License-Identifier: Apache-2.0
"""Engine and session factory for the metadata DB (Postgres in cloud, SQLite on-premise)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.core.tenancy import install_guards


def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.close()


def make_engine(url: str, **kw: Any) -> sa.Engine:
    engine = sa.create_engine(url, **kw)
    if engine.dialect.name == "sqlite":
        sa.event.listen(engine, "connect", _sqlite_pragmas)
    return engine


def make_session_factory(engine: sa.Engine) -> sessionmaker[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    install_guards(factory)
    return factory
