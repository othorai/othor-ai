# SPDX-License-Identifier: Apache-2.0
"""setup drafts, chats, and the purpose of each source query

Revision ID: 0016_setup_drafts
Revises: 0015_uploads
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_setup_drafts"
down_revision = "0015_uploads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "setup_draft",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("draft_no", sa.Integer(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("prev_body", sa.JSON(), nullable=True),
        sa.Column("origin", sa.JSON(), nullable=False),
        sa.Column("refs", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.String(length=8), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("changed", sa.Boolean(), nullable=False),
        sa.Column("frozen", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_setup_draft_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_setup_draft")),
        sa.UniqueConstraint(
            "company_id", "kind", "key", name=op.f("uq_setup_draft_company_id_kind_key")
        ),
    )

    with op.batch_alter_table("setup_draft", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_setup_draft_company_id"), ["company_id"], unique=False)

    op.create_table(
        "chat_thread",
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_chat_thread_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_thread")),
    )

    with op.batch_alter_table("chat_thread", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_chat_thread_company_id"), ["company_id"], unique=False)

    op.create_table(
        "chat_message",
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=8), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("proposal", sa.JSON(), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_chat_message_company_id_company")
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["chat_thread.id"], name=op.f("fk_chat_message_thread_id_chat_thread")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_message")),
    )

    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_chat_message_company_id"), ["company_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_chat_message_thread_id"), ["thread_id"], unique=False)

    with op.batch_alter_table("run_query", schema=None) as batch_op:
        batch_op.add_column(sa.Column("draft_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column("purpose", sa.String(length=8), nullable=False, server_default="scan")
        )


def downgrade() -> None:
    with op.batch_alter_table("run_query", schema=None) as batch_op:
        batch_op.drop_column("purpose")
        batch_op.drop_column("draft_id")

    with op.batch_alter_table("chat_message", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_chat_message_thread_id"))
        batch_op.drop_index(batch_op.f("ix_chat_message_company_id"))

    op.drop_table("chat_message")

    with op.batch_alter_table("setup_draft", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_setup_draft_company_id"))

    op.drop_table("setup_draft")

    with op.batch_alter_table("chat_thread", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_chat_thread_company_id"))

    op.drop_table("chat_thread")
