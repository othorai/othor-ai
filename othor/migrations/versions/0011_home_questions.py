# SPDX-License-Identifier: Apache-2.0
"""question: Home questions (docs/03 Home); screens in M10

Revision ID: 0011_home_questions
Revises: 0010_spec
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_home_questions"
down_revision = "0010_spec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "question",
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column("why", sa.String(length=500), nullable=False),
        sa.Column("unblocks", sa.JSON(), nullable=True),
        sa.Column("origin", sa.JSON(), nullable=False),
        sa.Column("quick_replies", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("answer_record_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_question_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_question")),
        sa.UniqueConstraint(
            "company_id", "fingerprint", name=op.f("uq_question_company_id_fingerprint")
        ),
    )

    with op.batch_alter_table("question", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_question_company_id"), ["company_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("question", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_question_company_id"))

    op.drop_table("question")
