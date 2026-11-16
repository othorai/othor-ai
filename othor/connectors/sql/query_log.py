# SPDX-License-Identifier: Apache-2.0
"""Every SQL statement sent to a customer source goes through here (docs/03 `run_query`).

`guard_sql` refuses a statement that names a personal column of a table it touches (non-negotiable
3: personal columns are never read). `QueryLog` keeps what ran, for the `run_query` table.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

_STRING = re.compile(r"'(?:[^']|'')*'")
_IDENT = re.compile(r'"((?:[^"]|"")+)"|`([^`]+)`|\b([A-Za-z_][A-Za-z0-9_$]*)\b')


class PersonalColumnInQuery(RuntimeError):
    """A query named a personal column. It was not sent."""


def identifiers(sql: str) -> set[str]:
    body = _STRING.sub("''", sql)
    out = set()
    for m in _IDENT.finditer(body):
        out.add((m.group(1) or m.group(2) or m.group(3)).lower())
    return out


def guard_sql(sql: str, personal: Mapping[str, set[str]]) -> None:
    """`personal` maps a table name to its personal column names (lower case)."""
    ids = identifiers(sql)
    for table, cols in personal.items():
        if table.lower() in ids:
            hit = ids & {c.lower() for c in cols}
            if hit:
                # name the table only: column names of personal data stay out of logs too
                raise PersonalColumnInQuery(f"query names a personal column of {table}")


@dataclass(frozen=True)
class LoggedQuery:
    sql: str
    params: dict[str, Any]
    rows: int
    duration_ms: int
    hash: str


@dataclass
class QueryLog:
    personal: Mapping[str, set[str]] = field(default_factory=dict)
    entries: list[LoggedQuery] = field(default_factory=list)

    def record(self, sql: str, params: dict[str, Any], *, rows: int, ms: int) -> None:
        h = hashlib.sha256(sql.encode()).hexdigest()
        self.entries.append(LoggedQuery(sql, dict(params), rows, ms, h))

    def run(self, conn: sa.Connection, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
        """Guard, execute, log. Returns all rows (aggregates only; never table rows)."""
        guard_sql(sql, self.personal)
        t0 = time.perf_counter()
        res = conn.execute(sa.text(sql), params or {})
        rows = list(res.fetchall()) if res.returns_rows else []
        self.record(sql, params or {}, rows=len(rows), ms=int((time.perf_counter() - t0) * 1000))
        return rows
