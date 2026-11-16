# SPDX-License-Identifier: Apache-2.0
"""Test connection and schema scan for PostgreSQL and MySQL (docs/05, boards 2j, 10a1, 2k).

Schema and statistics only: no rows leave the source. Each table costs at most two small
aggregate queries (a capped count and one statistics query over its non-personal columns).
Personal columns are found from the catalog by name before any table query is built, and the
query log's guard refuses any statement that names one.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from othor.connectors.classify import Col, Sorted, TableFacts, classify, personal_columns
from othor.connectors.sql.engine import SqlCreds, friendly_error, make_engine
from othor.connectors.sql.grants import REFUSED, mysql_check, postgres_check
from othor.connectors.sql.query_log import QueryLog

BIG_TABLE = 1_000_000  # above this, counts are estimates and statistics come from a sample
MAX_STAT_COLUMNS = 40
MYSQL_SAMPLE_ROWS = 200_000
_WATERMARK = ("updated_at", "modified_at", "last_modified", "modified", "updated_on", "updated")

_PG_KIND = {
    "smallint": "int",
    "integer": "int",
    "bigint": "int",
    "numeric": "decimal",
    "decimal": "decimal",
    "money": "decimal",
    "real": "float",
    "double precision": "float",
    "date": "date",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamp",
    "boolean": "bool",
    "text": "text",
    "character varying": "text",
    "character": "text",
    "uuid": "text",
    "citext": "text",
}
_MY_KIND = {
    "tinyint": "int",
    "smallint": "int",
    "mediumint": "int",
    "int": "int",
    "integer": "int",
    "bigint": "int",
    "decimal": "decimal",
    "numeric": "decimal",
    "float": "float",
    "double": "float",
    "date": "date",
    "datetime": "timestamp",
    "timestamp": "timestamp",
    "char": "text",
    "varchar": "text",
    "text": "text",
    "tinytext": "text",
    "mediumtext": "text",
    "longtext": "text",
    "enum": "text",
    "set": "text",
    "year": "int",
    "bit": "bool",
}


def normalize_type(dialect: str, data_type: str) -> str:
    t = data_type.lower().strip()
    table = _PG_KIND if dialect == "postgresql" else _MY_KIND
    return table.get(t, "other")


def quote(dialect: str, name: str) -> str:
    if dialect == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return '"' + name.replace('"', '""') + '"'


def qualified(dialect: str, schema: str, table: str) -> str:
    return f"{quote(dialect, schema)}.{quote(dialect, table)}"


def stats_sql(
    dialect: str, schema: str, table: str, cols: list[tuple[str, str]], *, sample: bool
) -> str:
    """One aggregate row: count, then per column non-null count, distinct count, min, max.

    `cols` must already exclude personal columns; the guard checks again before it runs.
    """

    def q(n: str) -> str:
        return quote(dialect, n)

    parts = ["count(*)"]
    for name, kind in cols:
        parts.append(f"count({q(name)})")
        parts.append(f"count(DISTINCT {q(name)})" if kind != "other" else "NULL")
        if kind in ("int", "decimal", "float", "date", "timestamp"):
            parts += [f"min({q(name)})", f"max({q(name)})"]
        else:
            parts += ["NULL", "NULL"]
    src = qualified(dialect, schema, table)
    if sample and dialect == "postgresql":
        src += " TABLESAMPLE SYSTEM (1) REPEATABLE (20261101)"  # same sample each scan
    elif sample:
        names = ", ".join(q(n) for n, _ in cols) or "1"
        src = f"(SELECT {names} FROM {src} LIMIT {MYSQL_SAMPLE_ROWS}) s"  # noqa: S608
    # identifiers come from the catalog and are quoted; no value is ever interpolated
    return f"SELECT {', '.join(parts)} FROM {src}"  # noqa: S608


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()
    return v


@dataclass(frozen=True)
class ColumnScan:
    name: str
    data_type: str
    kind: str
    personal_kind: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TableScan:
    schema: str
    name: str
    columns: tuple[ColumnScan, ...]
    row_count: int
    row_count_estimated: bool
    last_modified: dt.datetime | None
    newest_date: dt.date | None
    sorted: Sorted
    watermark: str | None
    duration_ms: int = 0

    @property
    def facts(self) -> TableFacts:
        return TableFacts(
            self.schema,
            self.name,
            tuple(Col(c.name, c.kind) for c in self.columns),
            self.row_count,
            self.newest_date,
        )


@dataclass(frozen=True)
class ConnectionCheck:
    ok: bool
    message: str
    latency_ms: int | None = None
    tables: int | None = None
    refused: tuple[str, ...] = ()


# Postgres's own schemas: never business data, never read
_PG_SYSTEM = ("pg_catalog", "information_schema", "pg_toast")


def schemas_to_read(conn: sa.Connection, c: SqlCreds) -> tuple[str, ...]:
    """The schemas a scan reads. MySQL: the database. Postgres: the ones named in the config, or
    (M11c, the usual case) every schema the read-only role has USAGE on, leaving out Postgres's
    own. Catalog names only: no table is touched to find them. Sorted, so a scan is repeatable."""
    if c.dialect == "mysql" or c.scan_schemas:
        return c.scan_schemas
    rows = conn.execute(
        sa.text(
            "SELECT nspname FROM pg_namespace "
            "WHERE has_schema_privilege(oid, 'USAGE') AND nspname NOT IN :sys "
            "AND nspname NOT LIKE 'pg\\_temp\\_%' AND nspname NOT LIKE 'pg\\_toast\\_temp\\_%' "
            "ORDER BY nspname"
        ).bindparams(sa.bindparam("sys", expanding=True)),
        {"sys": list(_PG_SYSTEM)},
    ).all()
    return tuple(str(r[0]) for r in rows)


def check_connection(c: SqlCreds) -> ConnectionCheck:
    """Reachability and latency, then the read-only check, then a table count."""
    eng = make_engine(c)
    try:
        t0 = time.perf_counter()
        with eng.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
            latency = int((time.perf_counter() - t0) * 1000)
            schemas = schemas_to_read(conn, c)
            bad = (
                postgres_check(conn, schemas)
                if c.dialect == "postgresql"
                else mysql_check(conn, c.database)
            )
            if bad:
                return ConnectionCheck(False, REFUSED, latency, None, tuple(bad))
            n = conn.execute(
                sa.text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema IN :s AND table_type IN ('BASE TABLE', 'VIEW')"
                ).bindparams(sa.bindparam("s", expanding=True)),
                {"s": list(schemas) or [""]},
            ).scalar_one()
        return ConnectionCheck(True, f"Connected · read-only · {n:,} tables", latency, int(n))
    except Exception as exc:
        return ConnectionCheck(False, friendly_error(exc, c))
    finally:
        eng.dispose()


@dataclass
class Catalog:
    """What the catalog says, before any table is touched."""

    tables: dict[str, list[tuple[str, list[tuple[str, str]]]]]  # schema → [(table, [(col, type)])]
    estimates: dict[tuple[str, str], int]
    updated: dict[tuple[str, str], dt.datetime | None]

    def count(self, schema: str) -> int:
        return len(self.tables.get(schema, []))


def read_catalog(conn: sa.Connection, c: SqlCreds, log: QueryLog) -> Catalog:
    tables: dict[str, list[tuple[str, list[tuple[str, str]]]]] = {}
    estimates: dict[tuple[str, str], int] = {}
    updated: dict[tuple[str, str], dt.datetime | None] = {}
    for schema in schemas_to_read(conn, c):
        rows = log.run(
            conn,
            "SELECT c.table_name, c.column_name, c.data_type FROM information_schema.columns c "
            "JOIN information_schema.tables t ON t.table_schema = c.table_schema "
            "AND t.table_name = c.table_name "
            "WHERE c.table_schema = :s AND t.table_type IN ('BASE TABLE', 'VIEW') "
            "ORDER BY c.table_name, c.ordinal_position",
            {"s": schema},
        )
        by: dict[str, list[tuple[str, str]]] = {}
        for t, col, typ in rows:
            by.setdefault(str(t), []).append((str(col), str(typ)))
        tables[schema] = sorted(by.items())
        if c.dialect == "postgresql":
            est = log.run(
                conn,
                "SELECT c.relname, c.reltuples::bigint FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :s AND c.relkind IN ('r', 'p', 'm')",
                {"s": schema},
            )
            for t, n in est:
                estimates[(schema, str(t))] = int(n)
        else:
            est = log.run(
                conn,
                "SELECT table_name, table_rows, update_time FROM information_schema.tables "
                "WHERE table_schema = :s",
                {"s": schema},
            )
            for t, n, u in est:
                estimates[(schema, str(t))] = int(n or -1)
                updated[(schema, str(t))] = (
                    u.replace(tzinfo=dt.UTC) if isinstance(u, dt.datetime) else None
                )
    return Catalog(tables, estimates, updated)


def scan_table(
    conn: sa.Connection,
    c: SqlCreds,
    cat: Catalog,
    schema: str,
    table: str,
    raw_cols: list[tuple[str, str]],
    log: QueryLog,
    today: dt.date,
) -> TableScan:
    t0 = time.perf_counter()
    d = c.dialect
    kinds = [(n, normalize_type(d, typ), typ) for n, typ in raw_cols]
    pre = TableFacts(schema, table, tuple(Col(n, k) for n, k, _ in kinds), 0)
    personal = dict(personal_columns(pre))
    if personal:
        log.personal = {**log.personal, table.lower(): {p.lower() for p in personal}}

    # 1. row count: exact up to BIG_TABLE, else the catalog's estimate
    est = cat.estimates.get((schema, table), -1)
    estimated = False
    if est >= BIG_TABLE:
        rows, estimated = est, True
    else:
        capped = log.run(
            conn,
            f"SELECT count(*) FROM (SELECT 1 AS x FROM {qualified(d, schema, table)} "  # noqa: S608
            f"LIMIT {BIG_TABLE + 1}) s",
        )[0][0]
        rows = int(capped)
        if rows > BIG_TABLE:
            rows, estimated = max(est, rows), True

    # 2. statistics over non-personal columns only
    stat_cols = [(n, k) for n, k, _ in kinds if n not in personal][:MAX_STAT_COLUMNS]
    stats: dict[str, dict[str, Any]] = {}
    if rows and stat_cols:
        r = log.run(conn, stats_sql(d, schema, table, stat_cols, sample=estimated))[0]
        n_sample = int(r[0]) or 1
        for i, (name, _k) in enumerate(stat_cols):
            nonnull, distinct, lo, hi = r[1 + 4 * i : 5 + 4 * i]
            stats[name] = {
                "null_share": round(1 - int(nonnull) / n_sample, 4),
                "distinct": int(distinct) if distinct is not None else None,
                "min": _jsonable(lo),
                "max": _jsonable(hi),
                "sampled": estimated,
            }

    columns = tuple(ColumnScan(n, typ, k, personal.get(n), stats.get(n, {})) for n, k, typ in kinds)
    facts = TableFacts(schema, table, tuple(Col(n, k) for n, k, _ in kinds), rows)
    newest: dt.date | None = None
    last_mod = cat.updated.get((schema, table))
    for col in facts.columns:
        hi = stats.get(col.name, {}).get("max")
        if not hi or col.kind not in ("date", "timestamp"):
            continue
        v = dt.datetime.fromisoformat(str(hi))
        if col.is_business_date:
            newest = max(newest, v.date()) if newest else v.date()
        elif col.is_audit:
            v = v if v.tzinfo else v.replace(tzinfo=dt.UTC)
            last_mod = max(last_mod, v) if last_mod else v
    facts = TableFacts(schema, table, facts.columns, rows, newest)
    sorted_ = classify(facts, today)
    names = [n.lower() for n, _, _ in kinds]
    watermark = next((n for n in _WATERMARK if n in names), None)
    if watermark is None and sorted_.use == "metric_source":
        watermark = next((col.name for col in facts.columns if col.is_business_date), None)
    return TableScan(
        schema,
        table,
        columns,
        rows,
        estimated,
        last_mod,
        newest,
        sorted_,
        watermark,
        int((time.perf_counter() - t0) * 1000),
    )


def scan(
    c: SqlCreds,
    *,
    log: QueryLog,
    today: dt.date,
    skip: Callable[[str], bool] = lambda _s: False,
    on_catalog: Callable[[Catalog], None] = lambda _c: None,
    done: Callable[[str, str], bool] = lambda _s, _t: False,
) -> Iterator[TableScan]:
    """Yield one TableScan per table. `skip(schema)` is asked before each table, so a schema
    skipped mid-scan stops there; `done(schema, table)` lets a resumed job pass tables it has."""
    eng = make_engine(c)
    try:
        with eng.connect() as conn:
            cat = read_catalog(conn, c, log)
            on_catalog(cat)
            for schema, tables in cat.tables.items():
                for table, cols in tables:
                    if skip(schema):
                        break
                    if done(schema, table):
                        continue
                    yield scan_table(conn, c, cat, schema, table, cols, log, today)
                    conn.rollback()  # end the read-only transaction between tables
    finally:
        eng.dispose()
