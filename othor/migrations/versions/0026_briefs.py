# SPDX-License-Identifier: Apache-2.0
"""briefs, versions, issues, recipients and suggestions (M8)

Revision ID: 0026_briefs
Revises: 0025_forecast
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0026_briefs"
down_revision = "0025_forecast"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brief",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_brief_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brief")),
    )
    with op.batch_alter_table("brief", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_brief_company_id"), ["company_id"], unique=False)

    op.create_table(
        "suggested_brief",
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("draft", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("prompt_version", sa.String(length=16), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_suggested_brief_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suggested_brief")),
    )
    with op.batch_alter_table("suggested_brief", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_suggested_brief_company_id"), ["company_id"], unique=False
        )

    op.create_table(
        "brief_version",
        sa.Column("brief_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("diff", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["brief_id"], ["brief.id"], name=op.f("fk_brief_version_brief_id_brief")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_brief_version_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brief_version")),
        sa.UniqueConstraint("brief_id", "number", name=op.f("uq_brief_version_brief_id_number")),
    )
    with op.batch_alter_table("brief_version", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_brief_version_brief_id"), ["brief_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_brief_version_company_id"), ["company_id"], unique=False
        )

    op.create_table(
        "recipient",
        sa.Column("brief_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("role", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("added_by", sa.String(length=120), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["brief_id"], ["brief.id"], name=op.f("fk_recipient_brief_id_brief")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_recipient_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipient")),
        sa.UniqueConstraint("brief_id", "email", name=op.f("uq_recipient_brief_id_email")),
    )
    with op.batch_alter_table("recipient", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_recipient_brief_id"), ["brief_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_recipient_company_id"), ["company_id"], unique=False)

    op.create_table(
        "brief_issue",
        sa.Column("brief_id", sa.Uuid(), nullable=False),
        sa.Column("brief_version", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("quiet", sa.Boolean(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("issue_hash", sa.String(length=64), nullable=False),
        sa.Column("prose", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["brief_id"], ["brief.id"], name=op.f("fk_brief_issue_brief_id_brief")
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_brief_issue_company_id_company")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_brief_issue_run_id_run")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brief_issue")),
        sa.UniqueConstraint("brief_id", "run_id", name=op.f("uq_brief_issue_brief_id_run_id")),
    )
    with op.batch_alter_table("brief_issue", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_brief_issue_brief_id"), ["brief_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_brief_issue_company_id"), ["company_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_brief_issue_date"), ["date"], unique=False)
        batch_op.create_index(batch_op.f("ix_brief_issue_run_id"), ["run_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("brief_issue", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_brief_issue_run_id"))
        batch_op.drop_index(batch_op.f("ix_brief_issue_date"))
        batch_op.drop_index(batch_op.f("ix_brief_issue_company_id"))
        batch_op.drop_index(batch_op.f("ix_brief_issue_brief_id"))

    op.drop_table("brief_issue")
    with op.batch_alter_table("recipient", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_recipient_company_id"))
        batch_op.drop_index(batch_op.f("ix_recipient_brief_id"))

    op.drop_table("recipient")
    with op.batch_alter_table("brief_version", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_brief_version_company_id"))
        batch_op.drop_index(batch_op.f("ix_brief_version_brief_id"))

    op.drop_table("brief_version")
    with op.batch_alter_table("suggested_brief", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_suggested_brief_company_id"))

    op.drop_table("suggested_brief")
    with op.batch_alter_table("brief", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_brief_company_id"))

    op.drop_table("brief")
