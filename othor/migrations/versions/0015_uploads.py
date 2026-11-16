# SPDX-License-Identifier: Apache-2.0
"""uploads, their citable refs, and the business message

Revision ID: 0015_uploads
Revises: 0014_setup_drafts_unversioned
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_uploads"
down_revision = "0014_setup_drafts_unversioned"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upload",
        sa.Column("filename", sa.String(length=300), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("read_by", sa.String(length=8), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("uploaded_by", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_upload_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_upload")),
        sa.UniqueConstraint("company_id", "sha256", name=op.f("uq_upload_company_id_sha256")),
    )

    with op.batch_alter_table("upload", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_upload_company_id"), ["company_id"], unique=False)

    op.create_table(
        "upload_ref",
        sa.Column("upload_id", sa.Uuid(), nullable=False),
        sa.Column("locator", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_upload_ref_company_id_company")
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"], ["upload.id"], name=op.f("fk_upload_ref_upload_id_upload")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_upload_ref")),
    )

    with op.batch_alter_table("upload_ref", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_upload_ref_company_id"), ["company_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_upload_ref_upload_id"), ["upload_id"], unique=False)

    op.create_table(
        "business_message",
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("draft_text", sa.Text(), nullable=True),
        sa.Column("not_covered", sa.JSON(), nullable=True),
        sa.Column("data_lives", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_business_message_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_message")),
    )

    with op.batch_alter_table("business_message", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_business_message_company_id"), ["company_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("upload_ref", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_upload_ref_upload_id"))
        batch_op.drop_index(batch_op.f("ix_upload_ref_company_id"))

    op.drop_table("upload_ref")

    with op.batch_alter_table("upload", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_upload_company_id"))

    op.drop_table("upload")

    with op.batch_alter_table("business_message", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_business_message_company_id"))

    op.drop_table("business_message")
