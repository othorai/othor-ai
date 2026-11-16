# SPDX-License-Identifier: Apache-2.0
"""sources, scans, tables, columns, Sheets tabs, discoveries

Revision ID: 0006_sources
Revises: 0005_company_delivery
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_sources"
down_revision = "0005_company_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("read_window", sa.String(length=32), nullable=False),
        sa.Column("last_test", sa.JSON(), nullable=True),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("health", sa.JSON(), nullable=True),
        sa.Column("schema_fingerprint", sa.String(length=16), nullable=True),
        sa.Column("schema_change", sa.JSON(), nullable=True),
        sa.Column("connected_by", sa.String(length=64), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnect_reason", sa.String(length=300), nullable=True),
        sa.Column("in_setup", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_source_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source")),
    )

    with op.batch_alter_table("source", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_source_company_id"), ["company_id"], unique=False)

    op.create_table(
        "source_secret",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_source_secret_company_id_company")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_source_secret_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_secret")),
        sa.UniqueConstraint("source_id", name=op.f("uq_source_secret_source_id")),
    )

    with op.batch_alter_table("source_secret", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_source_secret_company_id"), ["company_id"], unique=False
        )

    op.create_table(
        "scan",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("previous_scan_id", sa.Uuid(), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_scan_company_id_company")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_scan_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scan")),
    )

    with op.batch_alter_table("scan", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_scan_company_id"), ["company_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_scan_source_id"), ["source_id"], unique=False)

    op.create_table(
        "source_schema",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("skipped", sa.Boolean(), nullable=False),
        sa.Column("table_count", sa.Integer(), nullable=False),
        sa.Column("scanned_count", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_source_schema_company_id_company")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_source_schema_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_schema")),
        sa.UniqueConstraint("source_id", "name", name=op.f("uq_source_schema_source_id_name")),
    )

    with op.batch_alter_table("source_schema", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_source_schema_company_id"), ["company_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_source_schema_source_id"), ["source_id"], unique=False)

    op.create_table(
        "source_table",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("scan_id", sa.Uuid(), nullable=True),
        sa.Column("schema", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("state", sa.String(length=8), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=True),
        sa.Column("row_count_estimated", sa.Boolean(), nullable=False),
        sa.Column("last_modified", sa.DateTime(timezone=True), nullable=True),
        sa.Column("newest_date", sa.Date(), nullable=True),
        sa.Column("use", sa.String(length=16), nullable=True),
        sa.Column("use_reason", sa.String(length=32), nullable=True),
        sa.Column("use_set_by", sa.String(length=8), nullable=False),
        sa.Column("use_set_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("use_set_by_user", sa.String(length=64), nullable=True),
        sa.Column("scan_use", sa.String(length=16), nullable=True),
        sa.Column("scan_reason", sa.String(length=32), nullable=True),
        sa.Column("without_personal", sa.Boolean(), nullable=False),
        sa.Column("personal_count", sa.Integer(), nullable=False),
        sa.Column("looks_like", sa.String(length=200), nullable=True),
        sa.Column("looks_like_by", sa.String(length=8), nullable=True),
        sa.Column("stable_key", sa.String(length=16), nullable=False),
        sa.Column("watermark_column", sa.String(length=200), nullable=True),
        sa.Column("gone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_source_table_company_id_company")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_source_table_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_table")),
        sa.UniqueConstraint(
            "source_id", "schema", "name", name=op.f("uq_source_table_source_id_schema_name")
        ),
    )

    with op.batch_alter_table("source_table", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_source_table_company_id"), ["company_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_source_table_source_id"), ["source_id"], unique=False)
        batch_op.create_index(
            "ix_source_table_use", ["company_id", "source_id", "use"], unique=False
        )

    op.create_table(
        "source_column",
        sa.Column("table_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("data_type", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("is_personal", sa.Boolean(), nullable=False),
        sa.Column("personal_kind", sa.String(length=16), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_source_column_company_id_company")
        ),
        sa.ForeignKeyConstraint(
            ["table_id"], ["source_table.id"], name=op.f("fk_source_column_table_id_source_table")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_column")),
        sa.UniqueConstraint("table_id", "name", name=op.f("uq_source_column_table_id_name")),
    )

    with op.batch_alter_table("source_column", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_source_column_company_id"), ["company_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_source_column_table_id"), ["table_id"], unique=False)

    op.create_table(
        "sheet_tab",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("table_id", sa.Uuid(), nullable=True),
        sa.Column("spreadsheet_id", sa.String(length=128), nullable=False),
        sa.Column("spreadsheet_title", sa.String(length=300), nullable=False),
        sa.Column("tab_name", sa.String(length=200), nullable=False),
        sa.Column("sheet_id", sa.Integer(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("header_row", sa.Integer(), nullable=True),
        sa.Column("last_row", sa.Integer(), nullable=True),
        sa.Column("range", sa.String(length=32), nullable=True),
        sa.Column("skipped_rows", sa.JSON(), nullable=True),
        sa.Column("filled_down", sa.JSON(), nullable=True),
        sa.Column("blank_cols", sa.JSON(), nullable=True),
        sa.Column("units", sa.JSON(), nullable=True),
        sa.Column("date_formats", sa.JSON(), nullable=True),
        sa.Column("shape", sa.JSON(), nullable=True),
        sa.Column("confirmed_by", sa.String(length=64), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("header_fingerprint", sa.String(length=16), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_sheet_tab_company_id_company")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_sheet_tab_source_id_source")
        ),
        sa.ForeignKeyConstraint(
            ["table_id"], ["source_table.id"], name=op.f("fk_sheet_tab_table_id_source_table")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sheet_tab")),
        sa.UniqueConstraint(
            "source_id",
            "spreadsheet_id",
            "tab_name",
            name=op.f("uq_sheet_tab_source_id_spreadsheet_id_tab_name"),
        ),
    )

    with op.batch_alter_table("sheet_tab", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sheet_tab_company_id"), ["company_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_sheet_tab_source_id"), ["source_id"], unique=False)

    op.create_table(
        "scan_discovery",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("becomes_rule_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_scan_discovery_company_id_company")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_scan_discovery_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_discovery")),
    )

    with op.batch_alter_table("scan_discovery", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_scan_discovery_company_id"), ["company_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_scan_discovery_source_id"), ["source_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("source_column", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_source_column_table_id"))
        batch_op.drop_index(batch_op.f("ix_source_column_company_id"))

    op.drop_table("source_column")

    with op.batch_alter_table("sheet_tab", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sheet_tab_source_id"))
        batch_op.drop_index(batch_op.f("ix_sheet_tab_company_id"))

    op.drop_table("sheet_tab")

    with op.batch_alter_table("source_table", schema=None) as batch_op:
        batch_op.drop_index("ix_source_table_use")
        batch_op.drop_index(batch_op.f("ix_source_table_source_id"))
        batch_op.drop_index(batch_op.f("ix_source_table_company_id"))

    op.drop_table("source_table")

    with op.batch_alter_table("source_secret", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_source_secret_company_id"))

    op.drop_table("source_secret")

    with op.batch_alter_table("source_schema", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_source_schema_source_id"))
        batch_op.drop_index(batch_op.f("ix_source_schema_company_id"))

    op.drop_table("source_schema")

    with op.batch_alter_table("scan_discovery", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scan_discovery_source_id"))
        batch_op.drop_index(batch_op.f("ix_scan_discovery_company_id"))

    op.drop_table("scan_discovery")

    with op.batch_alter_table("scan", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scan_source_id"))
        batch_op.drop_index(batch_op.f("ix_scan_company_id"))

    op.drop_table("scan")

    with op.batch_alter_table("source", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_source_company_id"))

    op.drop_table("source")
