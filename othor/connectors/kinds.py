# SPDX-License-Identifier: Apache-2.0
"""Data-source kinds (M20.1, v0.20): the facts each kind of source has, in one place, so a
plug-in can add a kind (owner, 16 Nov 2026: "a different type of datasource") without the core
naming it.

Two families:
- `sql`: read in place over a read-only role (PostgreSQL, MySQL); the compiled SQL runs on the
  source in its own dialect.
- `local`: fetched each night into the instance's own DuckDB (Google Sheets), and the compiled
  SQL runs there. A plug-in kind (an API, an outside data feed) is a local kind: its `fetch`
  writes its tables the way Sheets does (`connectors.sheets.read.duckdb_table` naming).

The core's three kinds keep their own scan and read code; a plug-in kind brings `scan`, `test`
and `fetch`. Read-only, personal columns and determinism are checked on the shared path, not
per kind."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

Family = Literal["sql", "local"]


@dataclass(frozen=True)
class ConnectorKind:
    kind: str  # stored on source.kind
    label: str  # "PostgreSQL", "Google Sheets"
    short: str  # the source tile's letters: "PG"
    family: Family
    dialect: str  # the compiler's dialect: postgres · mysql · duckdb
    sqlalchemy: str | None = None  # the driver's dialect for a sql kind
    # a plug-in kind's own work; the core's kinds leave these empty
    scan: Callable[..., Any] | None = None  # (ctx, src, secret, payload)
    test: Callable[..., Any] | None = None  # (ctx, src, secret)
    fetch: Callable[..., Any] | None = None  # (settings, sf, key, source_id, clock) -> result


CORE: tuple[ConnectorKind, ...] = (
    ConnectorKind("postgres", "PostgreSQL", "PG", "sql", "postgres", "postgresql"),
    ConnectorKind("mysql", "MySQL", "MY", "sql", "mysql", "mysql"),
    ConnectorKind("gsheets", "Google Sheets", "GS", "local", "duckdb"),
)


def all_kinds() -> tuple[ConnectorKind, ...]:
    """The core's kinds, then each plug-in's."""
    from othor.core import plugins

    return CORE + tuple(c for c in plugins.connectors() if isinstance(c, ConnectorKind))


def get(kind: str) -> ConnectorKind:
    for k in all_kinds():
        if k.kind == kind:
            return k
    raise KeyError(f"unknown source kind {kind!r}")


def is_local(kind: str) -> bool:
    return get(kind).family == "local"
