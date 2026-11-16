# SPDX-License-Identifier: Apache-2.0
"""The scanned catalog the compiler resolves names against (source_table / source_column, M2)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.connectors import kinds
from othor.connectors.models import SheetTab, Source, SourceColumn, SourceTable

DIALECT = {"postgres": "postgres", "mysql": "mysql", "gsheets": "duckdb"}
KIND_NAME = {"postgres": "PostgreSQL", "mysql": "MySQL", "gsheets": "Google Sheets"}


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    kind: str
    personal: bool


@dataclass(frozen=True)
class TableInfo:
    source_id: str
    source_name: str
    source_kind: str  # postgres · mysql · gsheets
    schema: str
    name: str
    stable_key: str
    use: str | None
    without_personal: bool
    columns: dict[str, ColumnInfo]  # by lower-case name
    watermark: str | None = None
    duck_table: str | None = None  # Sheets: the extracted DuckDB table
    row_count: int = 0

    @property
    def dialect(self) -> str:
        return DIALECT[self.source_kind]

    @property
    def personal(self) -> set[str]:
        return {c.name.lower() for c in self.columns.values() if c.personal}

    def column(self, name: str) -> ColumnInfo | None:
        return self.columns.get(name.lower())


@dataclass
class Catalog:
    tables: dict[tuple[str, str, str], TableInfo] = field(default_factory=dict)
    sources: dict[str, tuple[str, str]] = field(default_factory=dict)  # name → (id, kind)

    def table(self, source: str, schema: str, name: str) -> TableInfo | None:
        return self.tables.get((source, schema, name))

    def by_name(self, source: str, name: str) -> TableInfo | None:
        hits = [t for (s, _sc, n), t in self.tables.items() if s == source and n == name]
        return hits[0] if len(hits) == 1 else None


log = logging.getLogger(__name__)


def unchosen_tabs(s: Session) -> set[object]:
    """The source_table ids of Sheets tabs the owner didn't choose (10b). A scan sorts every tab
    of a picked spreadsheet, but only the chosen ones are read, drafted from or counted."""
    return set(
        s.scalars(
            sa.select(SheetTab.table_id).where(
                SheetTab.selected.is_(False), SheetTab.table_id.is_not(None)
            )
        )
    )


def load_catalog(s: Session) -> Catalog:
    """Within the caller's tenant scope: connected or paused sources, their scanned tables."""
    from othor.connectors.sheets.extract import column_name

    cat = Catalog()
    sources = s.scalars(
        sa.select(Source)
        .where(Source.deleted_at.is_(None), Source.status != "draft")
        .order_by(Source.created_at, Source.id)
    ).all()
    # names are unique from 11 Nov 2026 (connectors.service.unique_name); a company made before
    # may have two sources with one name: the first connected keeps it, the later one is left
    # out and logged, the same way every time (it never silently overwrites the first)
    seen: set[str] = set()
    kept = []
    for src in sources:
        if src.name in seen:
            log.warning("source name taken; left out of the catalog", extra={"source": str(src.id)})
            continue
        seen.add(src.name)
        kept.append(src)
    sources = kept
    by_id = {x.id: x for x in sources}
    for src in sources:
        cat.sources[src.name] = (str(src.id), src.kind)
    tables = s.scalars(
        sa.select(SourceTable).where(
            SourceTable.source_id.in_(list(by_id)),
            SourceTable.gone_at.is_(None),
            SourceTable.state == "done",
        )
    ).all()
    skip = unchosen_tabs(s)
    tables = [t for t in tables if t.id not in skip]
    cols: dict[object, list[SourceColumn]] = {}
    for c in s.scalars(
        sa.select(SourceColumn).where(SourceColumn.table_id.in_([t.id for t in tables]))
    ):
        cols.setdefault(c.table_id, []).append(c)
    for t in tables:
        src = by_id[t.source_id]
        sheets = kinds.is_local(src.kind)
        info = {}
        for c in cols.get(t.id, []):
            name = column_name(c.name) if sheets else c.name
            info[name.lower()] = ColumnInfo(name, c.kind, c.is_personal)
        cat.tables[(src.name, t.schema, t.name)] = TableInfo(
            str(src.id),
            src.name,
            src.kind,
            t.schema,
            t.name,
            t.stable_key,
            t.use,
            t.without_personal,
            info,
            t.watermark_column,
            f"sheet_{t.id.hex[:12]}" if sheets else None,
            int(t.row_count or 0),
        )
    return cat
