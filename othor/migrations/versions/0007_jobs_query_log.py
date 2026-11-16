# SPDX-License-Identifier: Apache-2.0
"""the job table and the source query log

Revision ID: 0007_jobs_query_log
Revises: 0006_sources
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_jobs_query_log"
down_revision = "0006_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job",
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_job_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job")),
    )

    with op.batch_alter_table("job", schema=None) as batch_op:
        batch_op.create_index("ix_job_claim", ["status", "run_after"], unique=False)
        batch_op.create_index(batch_op.f("ix_job_company_id"), ["company_id"], unique=False)

    op.create_table(
        "run_query",
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("scan_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_run_query_company_id_company")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_run_query_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_query")),
    )

    with op.batch_alter_table("run_query", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_run_query_company_id"), ["company_id"], unique=False)
        batch_op.create_index("ix_run_query_scan", ["company_id", "scan_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("run_query", schema=None) as batch_op:
        batch_op.drop_index("ix_run_query_scan")
        batch_op.drop_index(batch_op.f("ix_run_query_company_id"))

    op.drop_table("run_query")

    with op.batch_alter_table("job", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_job_company_id"))
        batch_op.drop_index("ix_job_claim")

    op.drop_table("job")
