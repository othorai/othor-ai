# SPDX-License-Identifier: Apache-2.0
"""Sources, scans, tables, columns, Sheets tabs and the source query log (docs/03 "Sources
and scan").

Secrets live only in `source_secret` (encrypted with the instance key); `source.config` holds the
non-secret connection details shown back to the owner.
"""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from othor.core.models import Base, IdMixin, TenantMixin, utcnow

SOURCE_KINDS = ("postgres", "mysql", "gsheets")


class Source(IdMixin, TenantMixin, Base):
    __tablename__ = "source"

    kind: Mapped[str] = mapped_column(sa.String(16))
    name: Mapped[str] = mapped_column(sa.String(200))
    # draft (credentials entered, not connected) · scanning · connected · paused · disconnected
    status: Mapped[str] = mapped_column(sa.String(16), default="draft")
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    read_window: Mapped[str] = mapped_column(sa.String(32), default="nightly")
    last_test: Mapped[dict[str, Any] | None] = mapped_column()
    last_read_at: Mapped[datetime | None] = mapped_column()
    health: Mapped[dict[str, Any] | None] = mapped_column()
    schema_fingerprint: Mapped[str | None] = mapped_column(sa.String(16))
    schema_change: Mapped[dict[str, Any] | None] = mapped_column()
    connected_by: Mapped[str | None] = mapped_column(sa.String(64))
    connected_at: Mapped[datetime | None] = mapped_column()
    disconnected_at: Mapped[datetime | None] = mapped_column()
    disconnect_reason: Mapped[str | None] = mapped_column(sa.String(300))
    in_setup: Mapped[bool] = mapped_column(default=False)
    deleted_at: Mapped[datetime | None] = mapped_column()


class SourceSecret(IdMixin, TenantMixin, Base):
    """Encrypted credentials. Never returned by any route; decrypted in the worker per job."""

    __tablename__ = "source_secret"
    __table_args__ = (sa.UniqueConstraint("source_id"),)

    source_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("source.id"))
    blob: Mapped[bytes] = mapped_column(sa.LargeBinary)
    rotated_at: Mapped[datetime] = mapped_column(default=utcnow)


class Scan(IdMixin, TenantMixin, Base):
    __tablename__ = "scan"

    source_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("source.id"), index=True)
    status: Mapped[str] = mapped_column(
        sa.String(16), default="queued"
    )  # queued running done failed
    progress: Mapped[dict[str, Any]] = mapped_column(default=dict)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
    previous_scan_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    error: Mapped[str | None] = mapped_column(sa.String(300))


class SourceSchema(IdMixin, TenantMixin, Base):
    __tablename__ = "source_schema"
    __table_args__ = (sa.UniqueConstraint("source_id", "name"),)

    source_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("source.id"), index=True)
    name: Mapped[str] = mapped_column(sa.String(200))
    skipped: Mapped[bool] = mapped_column(default=False)
    table_count: Mapped[int] = mapped_column(default=0)
    scanned_count: Mapped[int] = mapped_column(default=0)


class SourceTable(IdMixin, TenantMixin, Base):
    """A table (or a Sheets tab: schema = spreadsheet title) and its use in the Ontology."""

    __tablename__ = "source_table"
    __table_args__ = (
        sa.UniqueConstraint("source_id", "schema", "name"),
        sa.Index("ix_source_table_use", "company_id", "source_id", "use"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("source.id"), index=True)
    scan_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    schema: Mapped[str] = mapped_column(sa.String(200))
    name: Mapped[str] = mapped_column(sa.String(200))
    kind: Mapped[str] = mapped_column(sa.String(8), default="table")  # table · tab
    state: Mapped[str] = mapped_column(sa.String(8), default="queued")  # queued reading done
    row_count: Mapped[int | None] = mapped_column(sa.BigInteger)
    row_count_estimated: Mapped[bool] = mapped_column(default=False)
    last_modified: Mapped[datetime | None] = mapped_column()
    newest_date: Mapped[dt.date | None] = mapped_column(sa.Date)
    use: Mapped[str | None] = mapped_column(
        sa.String(16)
    )  # metric_source dimension ignored excluded
    use_reason: Mapped[str | None] = mapped_column(sa.String(32))
    use_set_by: Mapped[str] = mapped_column(sa.String(8), default="scan")  # scan · user
    use_set_at: Mapped[datetime | None] = mapped_column()
    use_set_by_user: Mapped[str | None] = mapped_column(sa.String(64))
    scan_use: Mapped[str | None] = mapped_column(sa.String(16))
    scan_reason: Mapped[str | None] = mapped_column(sa.String(32))
    without_personal: Mapped[bool] = mapped_column(default=False)
    personal_count: Mapped[int] = mapped_column(default=0)
    looks_like: Mapped[str | None] = mapped_column(sa.String(200))
    looks_like_by: Mapped[str | None] = mapped_column(sa.String(8))  # rules · ai
    stable_key: Mapped[str] = mapped_column(sa.String(16))
    watermark_column: Mapped[str | None] = mapped_column(sa.String(200))
    gone_at: Mapped[datetime | None] = mapped_column()


class SourceColumn(IdMixin, TenantMixin, Base):
    __tablename__ = "source_column"
    __table_args__ = (sa.UniqueConstraint("table_id", "name"),)

    table_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("source_table.id"), index=True)
    position: Mapped[int] = mapped_column(default=0)
    name: Mapped[str] = mapped_column(sa.String(200))
    data_type: Mapped[str] = mapped_column(sa.String(64))
    kind: Mapped[str] = mapped_column(sa.String(16))
    is_personal: Mapped[bool] = mapped_column(default=False)
    personal_kind: Mapped[str | None] = mapped_column(sa.String(16))
    stats: Mapped[dict[str, Any] | None] = mapped_column()


class SheetTab(IdMixin, TenantMixin, Base):
    """One picked tab and its confirmed shape (10c). The tab's use lives on its source_table row."""

    __tablename__ = "sheet_tab"
    __table_args__ = (sa.UniqueConstraint("source_id", "spreadsheet_id", "tab_name"),)

    source_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("source.id"), index=True)
    table_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("source_table.id"))
    spreadsheet_id: Mapped[str] = mapped_column(sa.String(128))
    spreadsheet_title: Mapped[str] = mapped_column(sa.String(300))
    tab_name: Mapped[str] = mapped_column(sa.String(200))
    sheet_id: Mapped[int] = mapped_column(default=0)
    selected: Mapped[bool] = mapped_column(default=True)
    # unchecked · confirmed · needs_check · access_removed
    status: Mapped[str] = mapped_column(sa.String(16), default="unchecked")
    header_row: Mapped[int | None] = mapped_column()
    last_row: Mapped[int | None] = mapped_column()
    range: Mapped[str | None] = mapped_column(sa.String(32))
    skipped_rows: Mapped[list[int] | None] = mapped_column(sa.JSON)
    filled_down: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    blank_cols: Mapped[list[str] | None] = mapped_column(sa.JSON)
    units: Mapped[dict[str, Any] | None] = mapped_column()
    date_formats: Mapped[dict[str, Any] | None] = mapped_column()
    shape: Mapped[dict[str, Any] | None] = mapped_column()
    confirmed_by: Mapped[str | None] = mapped_column(sa.String(64))
    confirmed_at: Mapped[datetime | None] = mapped_column()
    header_fingerprint: Mapped[str | None] = mapped_column(sa.String(16))


class ScanDiscovery(IdMixin, TenantMixin, Base):
    """What a scan found across sources (6h "What we found"). Filled from M4."""

    __tablename__ = "scan_discovery"

    source_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("source.id"), index=True)
    type: Mapped[str] = mapped_column(sa.String(16))  # overlap currency dimension pattern gap
    text: Mapped[str] = mapped_column(sa.Text)
    evidence: Mapped[dict[str, Any] | None] = mapped_column()
    becomes_rule_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)


class RunQuery(IdMixin, TenantMixin, Base):
    """Every statement sent to a source: by a scan (scan_id) or a run (run_id, from M5)."""

    __tablename__ = "run_query"
    __table_args__ = (
        sa.Index("ix_run_query_scan", "company_id", "scan_id"),
        sa.Index("ix_run_query_run", "company_id", "run_id"),
    )

    run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    scan_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    draft_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    purpose: Mapped[str] = mapped_column(sa.String(8), default="scan")  # scan · draft · run
    source_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("source.id"))
    sql: Mapped[str] = mapped_column(sa.Text)
    params: Mapped[dict[str, Any] | None] = mapped_column()
    rows: Mapped[int] = mapped_column(default=0)
    duration_ms: Mapped[int] = mapped_column(default=0)
    hash: Mapped[str] = mapped_column(sa.String(64))


# M15.4 · how the owner reaches a system today (the connector request form)
REACH = {
    "web": "A web login",
    "export": "Export files (Excel, CSV)",
    "api": "An API",
    "database": "Its own database",
    "unsure": "Not sure",
}


class ConnectorRequest(IdMixin, TenantMixin, Base):
    """A connector the owner asked for from Add a source (M15.4): stored per company, emailed to
    support and counted (`othor admin requests`), so the most requested are built first."""

    __tablename__ = "connector_request"

    system: Mapped[str] = mapped_column(sa.String(120))
    version: Mapped[str | None] = mapped_column(sa.String(60))
    wants: Mapped[str | None] = mapped_column(sa.Text)
    reach: Mapped[str | None] = mapped_column(sa.String(16))
    requested_by: Mapped[str] = mapped_column(sa.String(64))
