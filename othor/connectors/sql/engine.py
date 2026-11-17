# SPDX-License-Identifier: Apache-2.0
"""SQLAlchemy engines for customer databases: read-only sessions, SSL modes, timeouts (docs/05).

Credentials are passed in per job and never logged; the URL is built with `URL.create` so the
password is never part of a string we format.
"""

from __future__ import annotations

import ssl as _ssl
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import sqlalchemy as sa

from othor.connectors.sql import hostguard

Dialect = Literal["postgresql", "mysql"]
SslMode = Literal["require", "verify-ca", "disable"]
STATEMENT_TIMEOUT_S = 120
CONNECT_TIMEOUT_S = 10
DEFAULT_PORT = {"postgresql": 5432, "mysql": 3306}


@dataclass(frozen=True)
class SqlCreds:
    dialect: Dialect
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)
    ssl: SslMode = "require"
    # Postgres schemas to read; empty = every schema the role can see (M11c). MySQL: `database`
    schemas: tuple[str, ...] = ()
    # whose source (the source-host guard, 17 Nov 2026); never shown or stored with the config
    company_id: uuid.UUID | None = field(default=None, repr=False, compare=False)

    def public(self) -> dict[str, Any]:
        """Everything except the password: safe to store as source config and show."""
        return {
            "dialect": self.dialect,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "ssl": self.ssl,
            "schemas": list(self.schemas),
        }

    @property
    def scan_schemas(self) -> tuple[str, ...]:
        """The schemas named in the config. Empty for Postgres means "every schema the role can
        see", resolved on a connection by `othor.connectors.sql.scan.schemas_to_read`."""
        if self.dialect == "mysql":
            return (self.database,)
        return self.schemas


def _connect_args(c: SqlCreds, timeout_s: int = STATEMENT_TIMEOUT_S) -> dict[str, Any]:
    if c.dialect == "postgresql":
        return {
            "sslmode": {"require": "require", "verify-ca": "verify-ca", "disable": "disable"}[
                c.ssl
            ],
            "connect_timeout": CONNECT_TIMEOUT_S,
            "application_name": "othor_ai",
            "options": f"-c statement_timeout={timeout_s * 1000} "
            "-c default_transaction_read_only=on",
        }
    args: dict[str, Any] = {
        "connect_timeout": CONNECT_TIMEOUT_S,
        "read_timeout": timeout_s,
        "init_command": "SET SESSION TRANSACTION READ ONLY",
    }
    if c.ssl == "require":
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        args["ssl"] = ctx
    elif c.ssl == "verify-ca":
        args["ssl"] = _ssl.create_default_context()
    return args


def make_engine(c: SqlCreds, timeout_s: int = STATEMENT_TIMEOUT_S) -> sa.Engine:
    """A read-only engine; `timeout_s` bounds every statement (6a3's preview: 20 s, M10b)."""
    driver = "postgresql+psycopg" if c.dialect == "postgresql" else "mysql+pymysql"
    url = sa.URL.create(
        driver, username=c.user, password=c.password, host=c.host, port=c.port, database=c.database
    )
    eng = sa.create_engine(
        url, connect_args=_connect_args(c, timeout_s), poolclass=sa.pool.NullPool
    )
    if hostguard.applies(c.company_id):
        # othor.ai (owner, 16 Nov 2026): every connection checks the host again and goes to the
        # address it checked; Postgres keeps the name for TLS (hostaddr), MySQL connects by the
        # address unless verify-ca needs the name
        @sa.event.listens_for(eng, "do_connect")
        def _guard(_d: Any, _rec: Any, _cargs: Any, cparams: dict[str, Any]) -> None:
            addr = hostguard.check(c.host, c.port)
            if c.dialect == "postgresql":
                cparams["hostaddr"] = addr
            elif c.ssl != "verify-ca":
                cparams["host"] = addr

    if c.dialect == "mysql":

        @sa.event.listens_for(eng, "connect")
        def _mysql_session(dbapi_conn: Any, _rec: Any) -> None:
            with dbapi_conn.cursor() as cur:
                cur.execute(f"SET SESSION max_execution_time = {timeout_s * 1000}")

    return eng


def friendly_error(exc: BaseException, c: SqlCreds) -> str:
    """A one-line reason for the owner. Never includes the password or the driver's DSN."""
    if isinstance(exc, hostguard.PrivateHost):
        return hostguard.MESSAGE
    if isinstance(exc, hostguard.Unresolved):
        return f"Can't reach {c.host}:{c.port}. Check the host, port and that our IP is allowed."
    text = str(getattr(exc, "orig", exc)).lower()
    if "password authentication failed" in text or "access denied" in text:
        return "Wrong user or password."
    if ("does not exist" in text and "database" in text) or "unknown database" in text:
        return f"Database {c.database} wasn't found on {c.host}."
    if "ssl" in text and ("not support" in text or "refused" in text or "required" in text):
        return "SSL setting doesn't match the server. Try another SSL option."
    if any(
        k in text
        for k in (
            "could not connect",
            "connection refused",
            "timed out",
            "timeout",
            "can't connect",
            "name or service not known",
            "nodename",
            "could not translate",
        )
    ):
        return f"Can't reach {c.host}:{c.port}. Check the host, port and that our IP is allowed."
    return "The connection failed. Check the details and try again."


def creds_from_source(src: Any, secret: dict[str, Any]) -> SqlCreds:
    """A PostgreSQL or MySQL `Source` row and its decrypted secret → creds (never logged)."""
    c = src.config
    from othor.connectors import kinds

    dialect: Dialect = kinds.get(src.kind).sqlalchemy or "mysql"  # type: ignore[assignment]
    return SqlCreds(
        dialect,
        c["host"],
        int(c["port"]),
        c["database"],
        c["user"],
        secret["password"],
        c.get("ssl", "require"),
        tuple(c.get("schemas") or ()),
        company_id=getattr(src, "company_id", None),
    )


def ping(engine: sa.Engine) -> None:
    """Raises when the source doesn't answer (a run's read step, before any metric query)."""
    with engine.connect() as c:
        c.execute(sa.text("SELECT 1"))
