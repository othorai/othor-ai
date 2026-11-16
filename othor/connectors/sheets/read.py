# SPDX-License-Identifier: Apache-2.0
"""Read a Google Sheets source's confirmed tabs whole, with their confirmed shape, into the
company's DuckDB store (docs/05 "Sync"). Used by the nightly run and, through it, by Read now.

A renamed or moved column marks the tab `needs_check` before any number breaks; a revoked token
disconnects the source. No row values are logged.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.connectors import credentials
from othor.connectors.models import SheetTab, Source, SourceColumn
from othor.connectors.service import mark_disconnected
from othor.connectors.sheets.access import client_for
from othor.connectors.sheets.client import AccessRemoved, TokenRevoked
from othor.connectors.sheets.detect import detect
from othor.connectors.sheets.extract import extract, header_fingerprint, load_duckdb
from othor.core.config import Settings
from othor.core.crypto import InstanceKey
from othor.core.events import SYSTEM_ACTOR, record_event


def duckdb_table(tab: SheetTab) -> str:
    return f"sheet_{tab.table_id.hex[:12]}" if tab.table_id else f"sheet_{tab.id.hex[:12]}"


@dataclass
class SheetsRead:
    ok: bool
    rows: int = 0
    tabs: int = 0
    reason: str | None = None  # why the source couldn't be read (no row values)
    skipped: list[str] = field(default_factory=list)  # tab names: needs_check / access_removed


def read_tabs(
    settings: Settings,
    sf: sessionmaker[Session],
    key: InstanceKey,
    source_id: uuid.UUID,
    clock: Callable[[], dt.datetime],
) -> SheetsRead:
    """Inside the caller's tenant scope."""
    with sf() as s:
        src = s.scalars(sa.select(Source).where(Source.id == source_id)).one()
        secret = credentials.load(s, key, src.company_id, src.id)
        tabs = s.scalars(
            sa.select(SheetTab).where(
                SheetTab.source_id == source_id,
                SheetTab.selected.is_(True),
                SheetTab.status == "confirmed",
            )
        ).all()
        for x in (src, *tabs):
            s.expunge(x)
    t0 = clock()
    try:
        client, new_secret = client_for(settings, secret)
        client.account()
    except TokenRevoked as exc:
        with sf() as s:
            mark_disconnected(s, source_id, str(exc), now=clock())
            s.commit()
        return SheetsRead(ok=False, reason=str(exc)[:300])
    if new_secret:
        with sf() as s:
            credentials.store(s, key, src.company_id, src.id, new_secret)
            s.commit()
    out = SheetsRead(ok=True)
    docs: dict[str, dict[str, Any]] = {}
    path = settings.analytics_path(src.company_id)
    for tab in sorted(tabs, key=lambda t: (t.spreadsheet_id, t.tab_name)):
        with sf() as s:
            row = s.scalars(sa.select(SheetTab).where(SheetTab.id == tab.id)).one()
            try:
                doc = docs.get(tab.spreadsheet_id) or client.get(tab.spreadsheet_id)
            except AccessRemoved:
                row.status = "access_removed"
                out.skipped.append(tab.tab_name)
                s.commit()
                continue
            docs[tab.spreadsheet_id] = doc
            sheet = next(
                (x for x in doc["sheets"] if x["properties"]["title"] == tab.tab_name), None
            )
            if sheet is None:
                row.status = "needs_check"
                out.skipped.append(tab.tab_name)
                s.commit()
                continue
            shape = detect(
                sheet,
                today=clock().date(),
                header_row=tab.header_row,
                last_row=tab.last_row,
                units=tab.units,
            )
            if header_fingerprint(shape) != tab.header_fingerprint:
                row.status = "needs_check"
                out.skipped.append(tab.tab_name)
                record_event(
                    s,
                    actor=SYSTEM_ACTOR,
                    action="sheet_tab.needs_check",
                    object_type="sheet_tab",
                    object_id=tab.id,
                    before={"fingerprint": tab.header_fingerprint},
                    after={"fingerprint": header_fingerprint(shape)},
                )
                s.commit()
                continue
            # personal columns are never read (non-negotiable 3), not even into the store
            personal = set(
                s.scalars(
                    sa.select(SourceColumn.name).where(
                        SourceColumn.table_id == tab.table_id, SourceColumn.is_personal.is_(True)
                    )
                ).all()
            )
            df = extract(sheet, shape, skip=personal, today=clock().date())
            out.rows += load_duckdb(path, duckdb_table(tab), df)
            out.tabs += 1
            s.commit()
    record_read(sf, source_id, clock, rows=out.rows, tabs=out.tabs, ms=_ms(t0, clock()))
    return out


def record_read(
    sf: sessionmaker[Session],
    source_id: uuid.UUID,
    clock: Callable[[], dt.datetime],
    *,
    rows: int,
    ms: int,
    tabs: int | None = None,
    tables: int | None = None,
    run_id: uuid.UUID | None = None,
) -> None:
    """The source's last read: shown on the Sources page and the source panel."""
    with sf() as s:
        src = s.scalars(sa.select(Source).where(Source.id == source_id)).one()
        src.last_read_at = clock()
        last: dict[str, Any] = {"rows": rows, "ms": ms, "at": clock().isoformat()}
        if tabs is not None:
            last["tabs"] = tabs
        if tables is not None:
            last["tables"] = tables
        if run_id is not None:
            last["run_id"] = str(run_id)
        src.health = {**(src.health or {}), "last_read": last}
        after: dict[str, Any] = {"rows": rows}
        if tabs is not None:
            after["tabs"] = tabs
        if tables is not None:
            after["tables"] = tables
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="source.read",
            object_type="source",
            object_id=source_id,
            after=after,
        )
        s.commit()


def _ms(a: dt.datetime, b: dt.datetime) -> int:
    return int((b - a).total_seconds() * 1000)
