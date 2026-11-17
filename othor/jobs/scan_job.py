# SPDX-License-Identifier: Apache-2.0
"""The scan job (docs/05 "Scan", boards 2k, 6g, 10b): runs in the worker with decrypted
credentials, commits after every table so the page can show progress, and resumes where it
stopped if the worker restarts.

payload: {"source_id": str, "scan_id": str, "mode": "scan" | "test", "tabs": {...overrides}}
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.connectors import credentials, kinds
from othor.connectors.classify import (
    Col,
    TableFacts,
    looks_like,
    schema_fingerprint,
    stable_key,
)
from othor.connectors.models import (
    RunQuery,
    Scan,
    SheetTab,
    Source,
    SourceColumn,
    SourceSchema,
    SourceTable,
)
from othor.connectors.service import mark_disconnected
from othor.connectors.sheets.access import client_for
from othor.connectors.sheets.client import AccessRemoved, TokenRevoked
from othor.connectors.sheets.detect import detect, needs_check, tab_use
from othor.connectors.sheets.extract import header_fingerprint
from othor.connectors.sql.engine import SqlCreds, creds_from_source
from othor.connectors.sql.query_log import QueryLog
from othor.connectors.sql.scan import Catalog, TableScan, check_connection, scan
from othor.core import plugins
from othor.core.events import SYSTEM_ACTOR, record_event
from othor.jobs.models import Job
from othor.jobs.queue import enqueue
from othor.jobs.runner import WorkerContext
from othor.ontology import service as ontology
from othor.ontology import table_use
from othor.setup.discover import offer_splits

DIALECT = {"postgres": "postgresql", "mysql": "mysql"}
PREVIEW_HEAD = 6  # rows shown on the 10c check grid from the top of the tab
PREVIEW_TAIL = 2


def sql_creds(src: Source, secret: dict[str, Any]) -> SqlCreds:
    return creds_from_source(src, secret)


def run(ctx: WorkerContext, job: Job) -> None:
    source_id = uuid.UUID(job.payload["source_id"])
    with ctx.sf() as s:
        src = s.scalars(sa.select(Source).where(Source.id == source_id)).one()
        secret = credentials.load(s, ctx.key, src.company_id, src.id)
        s.expunge(src)
    k = kinds.get(src.kind)
    test = job.payload.get("mode") == "test"
    if test and k.test is not None:  # a plug-in kind (M20.1)
        k.test(ctx, src, secret or {})
    elif not test and k.scan is not None:
        k.scan(ctx, src, secret, job.payload)
    elif test:
        _test(ctx, src, secret or {})
    elif k.family == "local":
        _scan_sheets(ctx, src, secret, job.payload)
    else:
        _scan_sql(ctx, src, secret or {}, uuid.UUID(job.payload["scan_id"]))


# ---------------------------------------------------------------------------------------------
# Test connection (2j, 10a1, 10r): the result lands on source.last_test for the page to poll


def _test(ctx: WorkerContext, src: Source, secret: dict[str, Any]) -> None:
    r = check_connection(sql_creds(src, secret))
    result = {
        "ok": r.ok,
        "message": r.message,
        "latency_ms": r.latency_ms,
        "tables": r.tables,
        "refused": list(r.refused),
        "at": ctx.clock().isoformat(),
        "pending": False,
    }
    with ctx.sf() as s:
        row = s.scalars(sa.select(Source).where(Source.id == src.id)).one()
        row.last_test = result
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="source.tested"
            if r.ok
            else ("source.refused" if r.refused else "source.test_failed"),
            object_type="source",
            object_id=src.id,
            after={k: v for k, v in result.items() if k != "pending"},
        )
        s.commit()


# ---------------------------------------------------------------------------------------------
# SQL scan


def _flush_log(s: Session, log: QueryLog, n_saved: int, src: Source, scan_id: uuid.UUID) -> int:
    for q in log.entries[n_saved:]:
        s.add(
            RunQuery(
                scan_id=scan_id,
                source_id=src.id,
                sql=q.sql,
                params=q.params,
                rows=q.rows,
                duration_ms=q.duration_ms,
                hash=q.hash,
            )
        )
    return len(log.entries)


def _progress(s: Session, scan_row: Scan, source_id: uuid.UUID, durations: list[int]) -> None:
    schemas = s.scalars(
        sa.select(SourceSchema)
        .where(SourceSchema.source_id == source_id)
        .order_by(SourceSchema.name)
    ).all()
    total = sum(x.table_count for x in schemas if not x.skipped)
    done = sum(min(x.scanned_count, x.table_count) for x in schemas if not x.skipped)
    left = max(total - done, 0)
    avg = (sum(durations[-50:]) / len(durations[-50:])) if durations else 300
    scan_row.progress = {
        "total": total,
        "scanned": done,
        "schemas_done": sum(1 for x in schemas if x.skipped or x.scanned_count >= x.table_count),
        "schemas": len(schemas),
        "eta_s": int(left * avg / 1000) + (5 if left else 0),
    }


def _apply_table(
    s: Session,
    ctx: WorkerContext,
    src: Source,
    scan_id: uuid.UUID,
    row: SourceTable,
    t: TableScan,
    base_tables: set[str],
) -> None:
    sorted_ = t.sorted
    first_time = row.scan_use is None
    row.scan_id = scan_id
    row.state = "done"
    row.row_count = t.row_count
    row.row_count_estimated = t.row_count_estimated
    row.last_modified = t.last_modified
    row.newest_date = t.newest_date
    row.scan_use, row.scan_reason = sorted_.use, sorted_.reason
    row.personal_count = len(sorted_.personal)
    row.watermark_column = t.watermark
    if row.looks_like_by != "ai":
        row.looks_like = looks_like(t.facts, sorted_, base_tables)[:200]
        row.looks_like_by = "rules"
    changed = False
    if sorted_.use == "excluded" and not row.without_personal:
        # only the scan can exclude; it overrides an earlier choice for a table that now
        # has personal columns
        changed = row.use != "excluded"
        row.use, row.use_reason, row.use_set_by = "excluded", None, "scan"
    elif row.use_set_by != "user":
        changed = (row.use, row.use_reason) != (sorted_.use, sorted_.reason)
        row.use, row.use_reason = sorted_.use, sorted_.reason
    if changed or first_time:
        row.use_set_at = ctx.clock()  # the Ontology record is written once, at the scan's end
    s.execute(sa.delete(SourceColumn).where(SourceColumn.table_id == row.id))
    for i, c in enumerate(t.columns):
        s.add(
            SourceColumn(
                table_id=row.id,
                position=i,
                name=c.name[:200],
                data_type=c.data_type[:64],
                kind=c.kind,
                is_personal=c.personal_kind is not None,
                personal_kind=c.personal_kind,
                stats=c.stats or None,
            )
        )


def _scan_sql(ctx: WorkerContext, src: Source, secret: dict[str, Any], scan_id: uuid.UUID) -> None:
    creds = sql_creds(src, secret)
    today = ctx.clock().date()
    with ctx.sf() as s:
        sc = s.scalars(sa.select(Scan).where(Scan.id == scan_id)).one()
        sc.status, sc.started_at = "running", sc.started_at or ctx.clock()
        record_event(
            s, actor=SYSTEM_ACTOR, action="scan.started", object_type="scan", object_id=scan_id
        )
        s.commit()

    log = QueryLog()
    saved = 0
    durations: list[int] = []
    base_tables: set[str] = set()

    def on_catalog(cat: Catalog) -> None:
        with ctx.sf() as s:
            have = {
                (t.schema, t.name): t
                for t in s.scalars(sa.select(SourceTable).where(SourceTable.source_id == src.id))
            }
            seen: set[tuple[str, str]] = set()
            for schema, tables in cat.tables.items():
                row = s.scalars(
                    sa.select(SourceSchema).where(
                        SourceSchema.source_id == src.id, SourceSchema.name == schema
                    )
                ).first()
                if row is None:
                    row = SourceSchema(source_id=src.id, name=schema)
                    s.add(row)
                row.table_count = len(tables)
                row.scanned_count = 0
                for name, _cols in tables:
                    seen.add((schema, name))
                    base_tables.add(f"{schema}.{name}".lower())
                    t = have.get((schema, name))
                    if t is None:
                        s.add(
                            SourceTable(
                                source_id=src.id,
                                schema=schema,
                                name=name,
                                state="queued",
                                stable_key=stable_key(schema, name),
                                row_count=cat.estimates.get((schema, name)),
                            )
                        )
                    else:
                        t.state, t.gone_at = "queued", None
            for key, t in have.items():
                if key not in seen and t.gone_at is None:
                    t.gone_at = ctx.clock()
            sc = s.scalars(sa.select(Scan).where(Scan.id == scan_id)).one()
            s.flush()
            _progress(s, sc, src.id, durations)
            s.commit()

    def skipped(schema: str) -> bool:
        with ctx.sf() as s:
            return bool(
                s.scalars(
                    sa.select(SourceSchema.skipped).where(
                        SourceSchema.source_id == src.id, SourceSchema.name == schema
                    )
                ).first()
            )

    def done(schema: str, table: str) -> bool:
        with ctx.sf() as s:
            row = s.scalars(
                sa.select(SourceTable).where(
                    SourceTable.source_id == src.id,
                    SourceTable.schema == schema,
                    SourceTable.name == table,
                )
            ).first()
            return row is not None and row.scan_id == scan_id and row.state == "done"

    facts: list[TableFacts] = []
    for t in scan(creds, log=log, today=today, skip=skipped, on_catalog=on_catalog, done=done):
        durations.append(t.duration_ms)
        facts.append(t.facts)
        with ctx.sf() as s:
            row = s.scalars(
                sa.select(SourceTable).where(
                    SourceTable.source_id == src.id,
                    SourceTable.schema == t.schema,
                    SourceTable.name == t.name,
                )
            ).one()
            _apply_table(s, ctx, src, scan_id, row, t, base_tables)
            sch = s.scalars(
                sa.select(SourceSchema).where(
                    SourceSchema.source_id == src.id, SourceSchema.name == t.schema
                )
            ).one()
            sch.scanned_count += 1
            saved = _flush_log(s, log, saved, src, scan_id)
            s.flush()
            sc = s.scalars(sa.select(Scan).where(Scan.id == scan_id)).one()
            _progress(s, sc, src.id, durations)
            s.commit()

    _finish(ctx, src, scan_id, facts, log, saved)


def _record_uses(s: Session, src: Source, scan_id: uuid.UUID) -> None:
    """One Ontology version with every table's use after this scan (unchanged ones skipped)."""
    rows = s.scalars(
        sa.select(SourceTable)
        .where(
            SourceTable.source_id == src.id,
            SourceTable.gone_at.is_(None),
            SourceTable.state == "done",
        )
        .order_by(SourceTable.schema, SourceTable.name)
    ).all()
    changes = [
        table_use.change(
            source_id=src.id,
            stable_key=r.stable_key,
            body={
                "source_id": str(src.id),
                "schema": r.schema,
                "table": r.name,
                "use": r.use,
                "reason": r.use_reason,
                "without_personal": r.without_personal,
            },
            source_ref={"scan": str(scan_id)},
        )
        for r in rows
    ]
    ontology.apply(
        s,
        changes,
        actor=SYSTEM_ACTOR,
        reason=f"Table uses from the scan of {src.name}",
        source_ref={"scan": str(scan_id)},
    )


def _finish(
    ctx: WorkerContext,
    src: Source,
    scan_id: uuid.UUID,
    facts: list[TableFacts],
    log: QueryLog | None,
    saved: int,
) -> None:
    with ctx.sf() as s:
        if log is not None:
            _flush_log(s, log, saved, src, scan_id)
        # tables left queued belong to schemas skipped during the scan
        for t in s.scalars(
            sa.select(SourceTable).where(
                SourceTable.source_id == src.id, SourceTable.state.in_(("queued", "reading"))
            )
        ):
            t.state = "skipped"
        row = s.scalars(sa.select(Source).where(Source.id == src.id)).one()
        fp = schema_fingerprint(facts) if facts else row.schema_fingerprint
        if (
            row.schema_fingerprint
            and fp
            and fp != row.schema_fingerprint
            and row.status != "scanning"
        ):
            row.schema_change = {
                "at": ctx.clock().isoformat(),
                "before": row.schema_fingerprint,
                "after": fp,
                "acknowledged": False,
            }
        row.schema_fingerprint = fp
        if row.status == "scanning":
            row.status = "connected"
            row.connected_at = ctx.clock()
        sc = s.scalars(sa.select(Scan).where(Scan.id == scan_id)).one()
        sc.status, sc.finished_at = "done", ctx.clock()
        _progress(s, sc, src.id, [])
        counts = dict(
            s.execute(
                sa.select(SourceTable.use, sa.func.count())
                .where(SourceTable.source_id == src.id, SourceTable.gone_at.is_(None))
                .group_by(SourceTable.use)
            ).all()
        )
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="scan.finished",
            object_type="scan",
            object_id=scan_id,
            after={"tables": len(facts), "uses": {str(k): v for k, v in counts.items()}},
        )
        _record_uses(s, row, scan_id)
        enqueue(s, "label", {"source_id": str(src.id), "scan_id": str(scan_id)})
        # new dimension tables can split numbers already confirmed (v0.11)
        offer_splits(s, row.company_id)
        plugins.after_scan(s, row.company_id, src.id)  # e.g. Discover looks again (Phase 0)
        s.commit()


# ---------------------------------------------------------------------------------------------
# Google Sheets: inspect the picked spreadsheets, every tab (10b, 6eb, 6g)


def _preview(sheet: dict[str, Any], header_row: int | None, last: int | None) -> dict[str, Any]:
    """The few rows the 10c grid shows: the top of the tab and the rows around its end."""
    vals = sheet.get("values", [])
    head_end = max(PREVIEW_HEAD, (header_row or 1) + 3)
    rows = [(i + 1, r) for i, r in enumerate(vals[:head_end])]
    tail_from = max(head_end, len(vals) - PREVIEW_TAIL - 1)
    rows += [(i + 1, r) for i, r in enumerate(vals) if i >= tail_from]
    return {"rows": [[n, [str(v) for v in r]] for n, r in rows], "total_rows": len(vals)}


def _scan_sheets(
    ctx: WorkerContext, src: Source, secret: dict[str, Any] | None, payload: dict[str, Any]
) -> None:
    scan_id = uuid.UUID(payload["scan_id"])
    overrides: dict[str, dict[str, Any]] = payload.get("tabs", {})
    today = ctx.clock().date()
    with ctx.sf() as s:
        sc = s.scalars(sa.select(Scan).where(Scan.id == scan_id)).one()
        sc.status, sc.started_at = "running", sc.started_at or ctx.clock()
        s.commit()
    try:
        client, new_secret = client_for(ctx.settings, secret)
        account = client.account()
    except TokenRevoked as exc:
        _disconnect(ctx, src, str(exc), scan_id)
        return
    if new_secret:
        with ctx.sf() as s:
            credentials.store(s, ctx.key, src.company_id, src.id, new_secret)
            s.commit()

    files = list(src.config.get("files", []))
    total = 0
    facts: list[TableFacts] = []
    for f in files:
        with ctx.sf() as s:
            try:
                meta = client.file_meta(f["id"])
                doc = client.get(f["id"])
            except AccessRemoved:
                for tab in s.scalars(
                    sa.select(SheetTab).where(
                        SheetTab.source_id == src.id, SheetTab.spreadsheet_id == f["id"]
                    )
                ):
                    tab.status = "access_removed"
                record_event(
                    s,
                    actor=SYSTEM_ACTOR,
                    action="sheet_file.access_removed",
                    object_type="source",
                    object_id=src.id,
                    after={"file": f["id"]},
                )
                s.commit()
                continue
            title = meta.name
            f.update(
                {
                    "title": title,
                    "editor": meta.editor,
                    "modified": meta.modified.isoformat() if meta.modified else None,
                }
            )
            for sheet in doc.get("sheets", []):
                props = sheet.get("properties", {})
                name = str(props.get("title", ""))
                found_tab = s.scalars(
                    sa.select(SheetTab).where(
                        SheetTab.source_id == src.id,
                        SheetTab.spreadsheet_id == f["id"],
                        SheetTab.tab_name == name,
                    )
                ).first()
                new_tab = found_tab is None
                tab = found_tab or SheetTab(
                    source_id=src.id,
                    spreadsheet_id=f["id"],
                    spreadsheet_title=title,
                    tab_name=name,
                    sheet_id=int(props.get("sheetId", 0)),
                    selected=False,
                    status="unchecked",
                )
                if new_tab:
                    s.add(tab)
                o = overrides.get(name, {})
                hr = o.get("header_row") or (tab.header_row if tab.status != "unchecked" else None)
                lr = o.get("last_row") or (tab.last_row if tab.status != "unchecked" else None)
                un = o.get("units") or (tab.units if tab.status != "unchecked" else None)
                shape = detect(
                    sheet,
                    today=today,
                    modified=meta.modified,
                    header_row=hr,
                    last_row=lr,
                    units=un,
                )
                use = tab_use(shape)
                fp = header_fingerprint(shape) if shape.kind == "table" else None
                was_confirmed = tab.status == "confirmed" and tab.confirmed_by != "scan"
                tab.spreadsheet_title = title
                tab.header_row, tab.last_row = shape.header_row, shape.last_row
                tab.range = shape.range_a1 or None
                tab.skipped_rows = shape.title_rows + shape.total_rows
                tab.filled_down = [list(x) for x in shape.filled_down]
                tab.blank_cols = shape.blank_cols
                tab.units = {c.header: c.unit for c in shape.columns if c.unit}
                tab.date_formats = {
                    c.header: list(c.date_formats) for c in shape.columns if c.date_formats
                }
                tab.shape = _shape_json(shape, use.use)
                if shape.kind == "table" and use.use != "excluded":
                    tab.shape = {**tab.shape, "totals": _column_totals(sheet, shape)}
                if new_tab:
                    tab.selected = use.use in ("metric_source", "dimension")
                if was_confirmed and fp != tab.header_fingerprint:
                    tab.status = "needs_check"
                    record_event(
                        s,
                        actor=SYSTEM_ACTOR,
                        action="sheet_tab.needs_check",
                        object_type="sheet_tab",
                        object_id=tab.id or name,
                        before={"fingerprint": tab.header_fingerprint},
                        after={"fingerprint": fp},
                    )
                elif tab.status in ("unchecked", "access_removed") or (
                    tab.confirmed_by == "scan" and tab.status == "confirmed"
                ):
                    if shape.kind == "table" and not needs_check(shape) and use.use != "excluded":
                        tab.status, tab.confirmed_by = "confirmed", "scan"
                        tab.confirmed_at, tab.header_fingerprint = ctx.clock(), fp
                    else:
                        tab.status = "unchecked"
                if (
                    shape.kind == "table"
                    and use.use != "excluded"
                    and tab.status in ("unchecked", "needs_check")
                ):
                    tab.shape = {
                        **tab.shape,
                        "preview": _preview(sheet, shape.header_row, shape.last_row),
                    }
                # the tab as a table, for the Use menu and the Ontology
                row = s.scalars(
                    sa.select(SourceTable).where(
                        SourceTable.source_id == src.id,
                        SourceTable.schema == title,
                        SourceTable.name == name,
                    )
                ).first()
                if row is None:
                    row = SourceTable(
                        source_id=src.id,
                        schema=title,
                        name=name,
                        kind="tab",
                        stable_key=stable_key(f["id"], name),
                    )
                    s.add(row)
                    s.flush()
                tab.table_id = row.id
                cols = tuple(
                    Col(c.header, {"date": "date", "number": "decimal"}.get(c.kind, "text"))
                    for c in shape.data_columns
                )
                tf = TableFacts(title, name, cols, shape.row_count, shape.newest_date)
                facts.append(tf)
                ts = TableScan(
                    title,
                    name,
                    tuple(_tab_column(c, use) for c in shape.data_columns),
                    shape.row_count,
                    False,
                    meta.modified,
                    shape.newest_date,
                    use,
                    None,
                )
                _apply_table(s, ctx, src, scan_id, row, ts, set())
                if use.use == "ignored":
                    row.looks_like = {
                        "copy": "Pivot of another tab",
                        "no_table": "Free text",
                        "old": f"Last edited {meta.modified:%b %Y}"
                        if shape.newest_date is None and meta.modified
                        else (f"Data to {shape.newest_date:%b %Y}" if shape.newest_date else "Old"),
                    }.get(use.reason or "", row.looks_like)
                total += 1
                sc = s.scalars(sa.select(Scan).where(Scan.id == scan_id)).one()
                sc.progress = {"total": total, "scanned": total, "tabs": True}
                s.commit()
    with ctx.sf() as s:
        source_row = s.scalars(sa.select(Source).where(Source.id == src.id)).one()
        source_row.config = {**source_row.config, "files": files, "account": account}
        s.commit()
    # A Sheets source becomes connected when the owner confirms its tabs (10c), not here.
    _finish_sheets(ctx, src, scan_id, facts)


def _finish_sheets(
    ctx: WorkerContext, src: Source, scan_id: uuid.UUID, facts: list[TableFacts]
) -> None:
    with ctx.sf() as s:
        row = s.scalars(sa.select(Source).where(Source.id == src.id)).one()
        fp = schema_fingerprint(facts) if facts else row.schema_fingerprint
        row.schema_fingerprint = fp
        sc = s.scalars(sa.select(Scan).where(Scan.id == scan_id)).one()
        sc.status, sc.finished_at = "done", ctx.clock()
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="scan.finished",
            object_type="scan",
            object_id=scan_id,
            after={"tabs": len(facts)},
        )
        _record_uses(s, row, scan_id)
        enqueue(s, "label", {"source_id": str(src.id), "scan_id": str(scan_id)})
        offer_splits(s, row.company_id)
        plugins.after_scan(s, row.company_id, src.id)  # e.g. Discover looks again (Phase 0)
        s.commit()


def _column_totals(sheet: dict[str, Any], shape: Any) -> dict[str, str]:
    """Sum of each number column in rupees (or its own unit), for "₹1,512.4 L in total"."""
    from othor.connectors.sheets.extract import column_name, extract

    df = extract(sheet, shape)
    out: dict[str, str] = {}
    for c in shape.data_columns:
        name = column_name(c.header)
        if c.kind == "number" and name in df.columns:
            out[c.header] = str(sum(v for v in df[name] if v is not None))
    return out


def _tab_column(c: Any, use: Any) -> Any:
    from othor.connectors.sql.scan import ColumnScan

    personal = dict(use.personal).get(c.header)
    stats = {"distinct": c.distinct, "nulls": c.nulls} if not personal else {}
    if c.unit:
        stats["unit"] = c.unit
    if c.date_formats:
        stats["date_formats"] = list(c.date_formats)
    kind = {"date": "date", "number": "decimal"}.get(c.kind, "text")
    return ColumnScan(c.header, c.kind, kind, personal, stats)


def _shape_json(shape: Any, use: str) -> dict[str, Any]:
    return {
        "kind": shape.kind,
        "use": use,
        "header_row": shape.header_row,
        "fingerprint": header_fingerprint(shape) if shape.kind == "table" else None,
        "title_rows": shape.title_rows,
        "first_row": shape.first_row,
        "last_row": shape.last_row,
        "total_rows": shape.total_rows,
        "row_count": shape.row_count,
        "range": shape.range_a1,
        "blank_cols": shape.blank_cols,
        "notes_cols": shape.notes_cols,
        "filled_down": [list(x) for x in shape.filled_down],
        "grid_rows": shape.grid_rows,
        "newest_date": shape.newest_date.isoformat() if shape.newest_date else None,
        "yearless": list(shape.yearless) if shape.yearless else None,
        "columns": [
            {
                "letter": c.letter,
                "header": c.header,
                "kind": c.kind,
                "unit": c.unit,
                "date_formats": list(c.date_formats),
                "distinct": c.distinct,
            }
            for c in shape.columns
        ],
    }


def _disconnect(ctx: WorkerContext, src: Source, reason: str, scan_id: uuid.UUID | None) -> None:
    with ctx.sf() as s:
        mark_disconnected(s, src.id, reason, now=ctx.clock())
        if scan_id:
            sc = s.scalars(sa.select(Scan).where(Scan.id == scan_id)).one()
            sc.status, sc.error, sc.finished_at = "failed", reason[:300], ctx.clock()
        s.commit()
