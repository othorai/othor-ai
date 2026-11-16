# SPDX-License-Identifier: Apache-2.0
"""forecasts stored before their actuals (M7)

Revision ID: 0025_forecast
Revises: 0024_reset_runs
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_forecast"
down_revision = "0024_reset_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forecast",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("series", sa.String(length=120), nullable=False),
        sa.Column("metric", sa.String(length=100), nullable=False),
        sa.Column("grain", sa.String(length=8), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("method", sa.String(length=24), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("point", sa.String(length=40), nullable=False),
        sa.Column("lower80", sa.String(length=40), nullable=True),
        sa.Column("upper80", sa.String(length=40), nullable=True),
        sa.Column("reconstructed", sa.Boolean(), nullable=False),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_forecast_company_id_company")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_forecast_run_id_run")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_forecast")),
    )
    with op.batch_alter_table("forecast", schema=None) as b:
        b.create_index(b.f("ix_forecast_company_id"), ["company_id"], unique=False)
        b.create_index(b.f("ix_forecast_run_id"), ["run_id"], unique=False)
        b.create_index(b.f("ix_forecast_series"), ["series"], unique=False)
        b.create_index(b.f("ix_forecast_period"), ["period"], unique=False)


def downgrade() -> None:
    op.drop_table("forecast")
