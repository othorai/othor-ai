# SPDX-License-Identifier: Apache-2.0
"""Load a SampleCompany into the sample sources (dev only): MySQL erp_db, PostgreSQL sales_dw,
and the Sheets API stand-in (JSON files). Idempotent: tables are dropped and reloaded.

Runs as the sample databases' admin users. Othor AI itself reads as `othor_ro` (read-only);
the grants are re-applied after every load so new tables are covered.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import sqlalchemy as sa

from tests.fixtures.sample_company.generate import SampleCompany

FOUR_DP = re.compile(r"^(fx_rate|eur_inr)$")
ONE_DP = re.compile(r"(qty|mtrs|score)")
STRESS_BATCH = 200  # scan_stress tables per transaction
MONEY = re.compile(r"(amount|value|net|gst|gross|debit|credit|balance|limit|rate|open)")


def _kind(name: str, s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "bool"
    if pd.api.types.is_integer_dtype(s):
        return "int"
    if pd.api.types.is_float_dtype(s):
        if FOUR_DP.search(name):
            return "dec4"
        if ONE_DP.search(name):
            return "dec1"
        if MONEY.search(name):
            return "dec2"
        return "float"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "ts"
    first = next(
        (v for v in s if v is not None and not (isinstance(v, float) and math.isnan(v))), None
    )
    if isinstance(first, pd.Timestamp | dt.datetime):
        return "ts"
    if isinstance(first, dt.date):
        return "date"
    if isinstance(first, bool | np.bool_):
        return "bool"
    return "text"


def _sql_type(kind: str, s: pd.Series, dialect: str) -> str:
    if kind == "text":
        n = int(s.dropna().astype(str).str.len().max()) if s.notna().any() else 16
        n = max(16, 1 << max(n - 1, 1).bit_length())
        return f"VARCHAR({n})" if n <= 512 else "TEXT"
    return {
        "bool": "BOOLEAN",
        "int": "BIGINT",
        "dec4": "NUMERIC(12,4)",
        "dec1": "NUMERIC(14,1)",
        "dec2": "NUMERIC(16,2)",
        "float": "DOUBLE PRECISION" if dialect == "postgresql" else "DOUBLE",
        "ts": "TIMESTAMP" if dialect == "postgresql" else "DATETIME",
        "date": "DATE",
    }[kind]


def _cell(v: Any, kind: str) -> Any:
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NaT:
        return None
    if isinstance(v, np.generic):
        v = v.item()
    if kind == "dec2":
        return f"{v:.2f}"
    if kind == "dec4":
        return f"{v:.4f}"
    if kind == "dec1":
        return f"{v:.1f}"
    if kind == "ts":
        return pd.Timestamp(v).strftime("%Y-%m-%d %H:%M:%S")
    if kind == "date":
        return v.isoformat()
    if kind == "bool":
        return bool(v)
    return v


def _ddl(table: str, df: pd.DataFrame, dialect: str) -> tuple[str, list[str]]:
    q = (lambda x: f'"{x}"') if dialect == "postgresql" else (lambda x: f"`{x}`")
    kinds, cols = [], []
    for name in df.columns:
        k = _kind(name, df[name])
        kinds.append(k)
        cols.append(f"{q(name)} {_sql_type(k, df[name], dialect)}")
    first = df.columns[0] if len(df.columns) else None
    if (
        first
        and re.search(r"(_id|_key)$|^id$", first)
        and df[first].is_unique
        and df[first].notna().all()
    ):
        cols.append(f"PRIMARY KEY ({q(first)})")
    return f"CREATE TABLE {table} ({', '.join(cols)})", kinds


def load_mysql(url: str, tables: dict[str, pd.DataFrame]) -> dict[str, int]:
    eng = sa.create_engine(url)
    counts = {}
    with eng.begin() as c:
        c.execute(sa.text("SET FOREIGN_KEY_CHECKS = 0"))
        for (name,) in c.execute(sa.text("SHOW TABLES")).all():
            c.execute(sa.text(f"DROP TABLE `{name}`"))
        for name in sorted(tables):
            df = tables[name]
            ddl, kinds = _ddl(f"`{name}`", df, "mysql")
            c.execute(sa.text(ddl))
            if len(df):
                cols = ", ".join(f"`{x}`" for x in df.columns)
                marks = ", ".join(["%s"] * len(df.columns))
                rows = [
                    tuple(_cell(v, k) for v, k in zip(r, kinds, strict=True))
                    for r in df.itertuples(index=False, name=None)
                ]
                cur = c.connection.cursor()
                for i in range(0, len(rows), 5000):
                    cur.executemany(
                        f"INSERT INTO `{name}` ({cols}) VALUES ({marks})", rows[i : i + 5000]
                    )
            counts[f"erp_db.{name}"] = len(df)
    eng.dispose()
    return counts


def load_postgres(url: str, schemas: dict[str, dict[str, pd.DataFrame]]) -> dict[str, int]:
    eng = sa.create_engine(url)
    counts = {}
    with eng.begin() as c:
        for schema in sorted(schemas):
            c.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            existing = (
                c.execute(
                    sa.text("SELECT tablename FROM pg_tables WHERE schemaname = :s"), {"s": schema}
                )
                .scalars()
                .all()
            )
            for name in existing:
                c.execute(sa.text(f'DROP TABLE "{schema}"."{name}" CASCADE'))
        raw = c.connection.driver_connection
        assert raw is not None
        for schema in sorted(schemas):
            for name in sorted(schemas[schema]):
                df = schemas[schema][name]
                ddl, kinds = _ddl(f'"{schema}"."{name}"', df, "postgresql")
                c.execute(sa.text(ddl))
                if len(df):
                    cols = ", ".join(f'"{x}"' for x in df.columns)
                    with raw.cursor().copy(f'COPY "{schema}"."{name}" ({cols}) FROM STDIN') as cp:
                        for r in df.itertuples(index=False, name=None):
                            cp.write_row([_cell(v, k) for v, k in zip(r, kinds, strict=True)])
                counts[f"sales_dw.{schema}.{name}"] = len(df)
            c.execute(sa.text(f'GRANT USAGE ON SCHEMA "{schema}" TO othor_ro, othor_rw'))
            c.execute(sa.text(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO othor_ro'))
            c.execute(
                sa.text(
                    f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO othor_rw'
                )
            )
    eng.dispose()
    return counts


def _copy_frame(c: Any, raw: Any, qualified: str, df: pd.DataFrame, kinds: list[str]) -> None:
    cols = ", ".join(f'"{x}"' for x in df.columns)
    with raw.cursor().copy(f"COPY {qualified} ({cols}) FROM STDIN") as cp:
        for r in df.itertuples(index=False, name=None):
            cp.write_row([_cell(v, k) for v, k in zip(r, kinds, strict=True)])


def load_large(
    company: SampleCompany,
    pg_url: str,
    *,
    row_cap: int | None = None,
    stress_tables: int | None = None,
) -> dict[str, int]:
    """SCALE=large extras (see large.py). `row_cap` and `stress_tables` shrink it for tests."""
    from tests.fixtures.sample_company import large

    eng = sa.create_engine(pg_url)
    counts: dict[str, int] = {}
    # One transaction per big table and per batch of stress tables: creating all 4,000
    # tables in one transaction exceeds Postgres's lock table (max_locks_per_transaction).
    with eng.begin() as c:
        for schema in ("perf", "scan_stress"):
            c.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            c.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
    for bt in large.big_tables(company, row_cap):
        with eng.begin() as c:
            raw = c.connection.driver_connection
            q = f'"{bt.schema}"."{bt.name}"'
            c.execute(sa.text(f"DROP TABLE IF EXISTS {q} CASCADE"))
            ddl, kinds = _ddl(q, bt.template, "postgresql")
            c.execute(sa.text(re.sub(r"VARCHAR\(\d+\)", "TEXT", ddl)))  # suffixed numbers
            for chunk in bt.chunks():
                _copy_frame(c, raw, q, chunk, kinds)
        counts[f"sales_dw.{bt.schema}.{bt.name}"] = bt.rows
    n = stress_tables if stress_tables is not None else large.STRESS_TABLES
    batch: list[tuple[str, pd.DataFrame]] = []

    def _flush() -> None:
        with eng.begin() as c:
            raw = c.connection.driver_connection
            for name, df in batch:
                q = f'"scan_stress"."{name}"'
                ddl, kinds = _ddl(q, df, "postgresql")
                c.execute(sa.text(ddl))
                if len(df):
                    _copy_frame(c, raw, q, df, kinds)
                counts[f"sales_dw.scan_stress.{name}"] = len(df)
        batch.clear()

    for item in large.stress_tables(company.seed, n):
        batch.append(item)
        if len(batch) == STRESS_BATCH:
            _flush()
    if batch:
        _flush()
    with eng.begin() as c:
        for schema in ("public", "sales_dw", "perf", "scan_stress"):
            c.execute(sa.text(f'GRANT USAGE ON SCHEMA "{schema}" TO othor_ro, othor_rw'))
            c.execute(sa.text(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO othor_ro'))
    eng.dispose()
    return counts


def write_sheets(company: SampleCompany, out: Path) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()
    counts = {}
    index = []
    for sid, doc in sorted(company.sheets.items()):
        (out / f"{sid}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True)
        )
        index.append({"spreadsheetId": sid, "title": doc["properties"]["title"]})
        for tab in doc["sheets"]:
            counts[f"sheets.{doc['properties']['title']}.{tab['properties']['title']}"] = len(
                tab["values"]
            )
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
    (out / "auth.json").write_text(json.dumps(company.sheets_auth, indent=1))
    (out / "auth_revoked_2026-10-31.json").write_text(
        json.dumps(company.sheets_auth_revoked, indent=1)
    )
    return counts


def load_all(
    company: SampleCompany,
    *,
    mysql_url: str,
    pg_url: str,
    sheets_dir: Path,
    scale: str = "default",
) -> dict[str, int]:
    if scale not in ("default", "large"):
        raise ValueError(f"scale is default or large, not {scale!r}")
    counts = load_mysql(mysql_url, company.erp)
    counts |= load_postgres(pg_url, company.dw)
    if scale == "large":
        counts |= load_large(company, pg_url)
    else:
        _drop_large(pg_url)
    counts |= write_sheets(company, sheets_dir)
    from tests.fixtures.sample_company.uploads import write_uploads

    counts["uploads"] = len(write_uploads(company, sheets_dir.parent / "uploads"))
    return counts


def _drop_large(pg_url: str) -> None:
    """A default seed after a large one returns to the small, fast shape."""
    eng = sa.create_engine(pg_url)
    # Drop the 4,000 stress tables in batches first: one DROP SCHEMA … CASCADE over all of them
    # needs more locks than one transaction may hold (max_locks_per_transaction).
    with eng.connect() as c:
        names = (
            c.execute(sa.text("SELECT tablename FROM pg_tables WHERE schemaname = 'scan_stress'"))
            .scalars()
            .all()
        )
    for i in range(0, len(names), STRESS_BATCH):
        with eng.begin() as c:
            for n in names[i : i + STRESS_BATCH]:
                c.execute(sa.text(f'DROP TABLE IF EXISTS "scan_stress"."{n}" CASCADE'))
    with eng.begin() as c:
        for schema in ("perf", "scan_stress"):
            c.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    eng.dispose()


__all__ = ["io", "load_all", "load_large", "load_mysql", "load_postgres", "write_sheets"]
