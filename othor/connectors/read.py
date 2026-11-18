# SPDX-License-Identifier: Apache-2.0
"""One safe reader for a few rows from a company's own source (0.26: moved from Look back so the
chat's data previews use the same path). Every read: read-only credentials and engine (the host
guard re-checked on connect), the statement checked for personal columns (`guard_sql`), only the
SQL and the row count logged (`QueryLog`), Sheets through the company's DuckDB file opened
read-only. A failure is `Unreachable` with a plain reason (no value, no secret)."""

from __future__ import annotations

import datetime as dt
import time
import uuid
from typing import Any

from othor.ontology.compiler.catalog import Catalog


class Unreachable(RuntimeError):
    """The source didn't answer, or answered with an error; the message is the plain reason (no
    value, no secret)."""


def run(
    ctx: Any,
    company_id: uuid.UUID,
    cat: Catalog,
    source_name: str,
    sql: str,
    params: dict[str, Any],
) -> tuple[list[tuple[Any, ...]], list[str], int]:
    """Run one read-only query on a source; (rows, column names, ms). Never logs a value."""
    import sqlalchemy as sa

    from othor.connectors import credentials, kinds
    from othor.connectors.models import Source
    from othor.connectors.sql.engine import creds_from_source, friendly_error, make_engine
    from othor.connectors.sql.query_log import QueryLog, guard_sql

    sid, kind = cat.sources[source_name]
    personal = {t.name.lower(): set(t.personal) for t in cat.tables.values() if t.personal}
    t0 = time.perf_counter()
    if kinds.is_local(kind):
        import duckdb

        guard_sql(sql, personal)
        p = {k: v.isoformat() if isinstance(v, dt.date) else v for k, v in params.items()}
        con = duckdb.connect(str(ctx.settings.analytics_path(company_id)), read_only=True)
        try:
            rel = con.execute(sql, {k: v for k, v in p.items() if f"${k}" in sql})
            names = [d[0] for d in rel.description]
            rows = [tuple(r) for r in rel.fetchall()]
        finally:
            con.close()
        return rows, names, int((time.perf_counter() - t0) * 1000)
    with ctx.sf() as s:
        src = s.scalars(sa.select(Source).where(Source.id == uuid.UUID(sid))).one()
        secret = credentials.load(s, ctx.key, src.company_id, src.id)
        s.expunge(src)
    creds = creds_from_source(src, secret or {})
    log = QueryLog(personal=personal)
    try:
        engine = make_engine(creds)
    except Exception as e:  # the host guard, a bad address
        raise Unreachable(friendly_error(e, creds)[:300]) from e
    try:
        try:
            conn = engine.connect()  # read-only and host-checked on connect (make_engine)
        except (sa.exc.SQLAlchemyError, OSError) as e:
            raise Unreachable(friendly_error(e, creds)[:300]) from e
        with conn:
            used = {k: v for k, v in params.items() if f":{k}" in sql}
            try:
                got = log.run(conn, sql, used)  # guarded; logs the SQL and the row count only
            except sa.exc.DBAPIError as e:  # connected, but the read itself failed
                code = getattr(getattr(e, "orig", None), "args", [""])[0]
                raise Unreachable(
                    "the read failed"
                    + (f" (error {code})" if isinstance(code, int | str) and code else "")
                    + "; the table or a column may have changed since the last scan."
                ) from e
            names = list(got[0]._fields) if got else []
            rows = [tuple(r) for r in got]
            conn.rollback()
    finally:
        engine.dispose()
    return rows, names, int((time.perf_counter() - t0) * 1000)
