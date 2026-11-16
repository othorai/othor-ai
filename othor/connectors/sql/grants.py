# SPDX-License-Identifier: Apache-2.0
"""Read-only verification at Test connection (docs/05, non-negotiable 3).

Any right to change data or structure refuses the user. TEMP on Postgres is allowed: PUBLIC holds
it by default and we never create temp tables.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa

REFUSED = "This user can change data. Create a read-only user and try again."

# MySQL privileges that read only.
_MYSQL_READ = {"SELECT", "SHOW VIEW", "USAGE", "SHOW DATABASES", "PROCESS"}
_GRANT = re.compile(r"^GRANT\s+(?P<privs>.+?)\s+ON\s+(?P<on>\S+)\s+TO\s", re.I)
_ROLE_GRANT = re.compile(r"^GRANT\s+`?(?P<role>[^`@\s]+)`?@\S+\s+TO\s", re.I)


def _scope_covers(on: str, database: str) -> bool:
    on = on.replace("`", "").strip()
    if on.upper().startswith(("FUNCTION ", "PROCEDURE ")):
        return False
    db = on.split(".", 1)[0]
    return db == "*" or db.lower() == database.lower()


def mysql_write_privileges(grants: Sequence[str], database: str) -> list[str]:
    """The privileges in `SHOW GRANTS` output that can change `database`. Empty = read-only."""
    bad: list[str] = []
    for line in grants:
        line = line.strip()
        m = _GRANT.match(line)
        if not m:
            r = _ROLE_GRANT.match(line)
            if r:  # a granted role's rights aren't listed here: refuse rather than guess
                bad.append(f"ROLE {r.group('role')}")
            continue
        if not _scope_covers(m.group("on"), database):
            continue
        for p in m.group("privs").split(","):
            p = re.sub(r"\s*\(.*\)\s*$", "", p).strip().upper()  # column-level: UPDATE (`x`)
            if p and p not in _MYSQL_READ:
                bad.append(p)
        if re.search(r"WITH\s+GRANT\s+OPTION", line, re.I):
            bad.append("GRANT OPTION")
    return sorted(set(bad))


def mysql_check(conn: sa.Connection, database: str) -> list[str]:
    rows = conn.exec_driver_sql("SHOW GRANTS FOR CURRENT_USER()").fetchall()
    return mysql_write_privileges([str(r[0]) for r in rows], database)


def postgres_check(conn: sa.Connection, schemas: Sequence[str]) -> list[str]:
    """Role attributes, database and schema CREATE, and any write right on a table we'd read."""
    bad: list[str] = []
    role = conn.exec_driver_sql(
        "SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls "
        "FROM pg_roles WHERE rolname = current_user"
    ).one()
    for flag, label in zip(
        role,
        ("SUPERUSER", "CREATEROLE", "CREATEDB", "REPLICATION", "BYPASSRLS"),
        strict=True,
    ):
        if flag:
            bad.append(label)
    if conn.exec_driver_sql(
        "SELECT has_database_privilege(current_database(), 'CREATE')"
    ).scalar_one():
        bad.append("CREATE ON DATABASE")
    for s in schemas:
        ok = conn.execute(
            sa.text("SELECT has_schema_privilege(:s, 'CREATE')"), {"s": s}
        ).scalar_one()
        if ok:
            bad.append(f"CREATE ON SCHEMA {s}")
    writable = (
        conn.execute(
            sa.text(
                "SELECT n.nspname || '.' || c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = ANY(:schemas) AND c.relkind IN ('r','p','v','m','f') "
                "AND has_table_privilege(c.oid, "
                "'INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES') "
                "ORDER BY 1 LIMIT 5"
            ),
            {"schemas": list(schemas)},
        )
        .scalars()
        .all()
    )
    bad += [f"WRITE ON {t}" for t in writable]
    return bad
