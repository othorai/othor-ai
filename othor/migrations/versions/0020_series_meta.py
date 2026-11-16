# SPDX-License-Identifier: Apache-2.0
"""one row per series: history, staleness, reconstruction (M5; values live in DuckDB)

Revision ID: 0020_series_meta
Revises: 0019_runs_ledger
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_series_meta"
down_revision = "0019_runs_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "series_meta",
        sa.Column("metric", sa.String(length=100), nullable=False),
        sa.Column("grain", sa.String(length=8), nullable=False),
        sa.Column("breakdown", sa.String(length=64), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("last_good_at", sa.Date(), nullable=True),
        sa.Column("history_start", sa.Date(), nullable=True),
        sa.Column("history_days", sa.Integer(), nullable=False),
        sa.Column("reconstructed_to", sa.Date(), nullable=True),
        sa.Column("reconstructed_from", sa.String(length=120), nullable=True),
        sa.Column("last_run_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_series_meta_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_series_meta")),
        sa.UniqueConstraint(
            "company_id",
            "metric",
            "grain",
            "breakdown",
            name=op.f("uq_series_meta_company_id_metric_grain_breakdown"),
        ),
    )
    with op.batch_alter_table("series_meta", schema=None) as b:
        b.create_index(b.f("ix_series_meta_company_id"), ["company_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("series_meta", schema=None) as b:
        b.drop_index(b.f("ix_series_meta_company_id"))
    op.drop_table("series_meta")
