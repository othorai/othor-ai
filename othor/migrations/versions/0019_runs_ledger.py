# SPDX-License-Identifier: Apache-2.0
"""runs, their steps and the ledger hashes (M5); run_query by run

Revision ID: 0019_runs_ledger
Revises: 0018_setup_changes
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_runs_ledger"
down_revision = "0018_setup_changes"
branch_labels = None
depends_on = None


def _tenant() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "run",
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=True),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("spec_id", sa.Uuid(), nullable=False),
        sa.Column("image_version", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("prev_run_hash", sa.String(length=64), nullable=True),
        sa.Column("run_hash", sa.String(length=64), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=False),
        *_tenant(),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_run_company_id_company")
        ),
        sa.ForeignKeyConstraint(["spec_id"], ["spec.id"], name=op.f("fk_run_spec_id_spec")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run")),
        sa.UniqueConstraint("company_id", "number", name=op.f("uq_run_company_id_number")),
    )
    with op.batch_alter_table("run", schema=None) as b:
        b.create_index(b.f("ix_run_company_id"), ["company_id"], unique=False)

    op.create_table(
        "run_step",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(length=300), nullable=True),
        *_tenant(),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_run_step_company_id_company")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_run_step_run_id_run")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_step")),
        sa.UniqueConstraint("run_id", "name", name=op.f("uq_run_step_run_id_name")),
    )
    with op.batch_alter_table("run_step", schema=None) as b:
        b.create_index(b.f("ix_run_step_company_id"), ["company_id"], unique=False)
        b.create_index(b.f("ix_run_step_run_id"), ["run_id"], unique=False)

    with op.batch_alter_table("run_query", schema=None) as b:
        b.create_index("ix_run_query_run", ["company_id", "run_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("run_query", schema=None) as b:
        b.drop_index("ix_run_query_run")
    with op.batch_alter_table("run_step", schema=None) as b:
        b.drop_index(b.f("ix_run_step_run_id"))
        b.drop_index(b.f("ix_run_step_company_id"))
    op.drop_table("run_step")
    with op.batch_alter_table("run", schema=None) as b:
        b.drop_index(b.f("ix_run_company_id"))
    op.drop_table("run")
