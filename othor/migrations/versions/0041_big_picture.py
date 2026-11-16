# SPDX-License-Identifier: Apache-2.0
"""big_picture: one Othor AI report per run and language; company.report_language (v0.19, M19.5).

Revision ID: 0041_big_picture
Revises: 0040_purge_lock
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0041_big_picture"
down_revision = "0040_purge_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # recreate="never", as in 0036 and 0039: company is added to in place
    with op.batch_alter_table("company", recreate="never") as b:
        b.add_column(
            sa.Column("report_language", sa.String(length=8), nullable=False, server_default="en")
        )
    op.create_table(
        "big_picture",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("prompt", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=120), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("guard", sa.String(length=32), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_big_picture_company_id_company")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_big_picture_run_id_run")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_big_picture")),
        sa.UniqueConstraint(
            "company_id",
            "run_id",
            "language",
            name=op.f("uq_big_picture_company_id_run_id_language"),
        ),
    )
    op.create_index(op.f("ix_big_picture_company_id"), "big_picture", ["company_id"], unique=False)
    op.create_index(op.f("ix_big_picture_run_id"), "big_picture", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_big_picture_run_id"), table_name="big_picture")
    op.drop_index(op.f("ix_big_picture_company_id"), table_name="big_picture")
    op.drop_table("big_picture")
    with op.batch_alter_table("company", recreate="never") as b:
        b.drop_column("report_language")
