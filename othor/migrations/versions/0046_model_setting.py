# SPDX-License-Identifier: Apache-2.0
"""model_setting: a company's own model for all kinds of work or one kind, its key encrypted
(Any model, 0.37, owner 24 Nov 2026).

Revision ID: 0046_model_setting
Revises: 0045_source_display_name
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0046_model_setting"
down_revision = "0045_source_display_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_setting",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=300), nullable=False),
        sa.Column("model_id", sa.String(length=120), nullable=False),
        sa.Column("key_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("set_by", sa.String(length=64), nullable=False),
        sa.Column("check_result", sa.String(length=32), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_model_setting_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_setting")),
    )
    op.create_index(
        op.f("ix_model_setting_company_id"), "model_setting", ["company_id"], unique=False
    )
    op.create_index("ix_model_setting_kind", "model_setting", ["company_id", "kind"], unique=False)
    # a call on the company's own model: outside the monthly AI allowance (owner, 25 Nov 2026)
    with op.batch_alter_table("model_call", recreate="never") as b:
        b.add_column(sa.Column("own_key", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("model_call", recreate="never") as b:
        b.drop_column("own_key")
    op.drop_index("ix_model_setting_kind", table_name="model_setting")
    op.drop_index(op.f("ix_model_setting_company_id"), table_name="model_setting")
    op.drop_table("model_setting")
