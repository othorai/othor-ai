# SPDX-License-Identifier: Apache-2.0
"""delivery: link tokens, deliveries, postmark events, unsubscribe, weekly digest (M9)

Revision ID: 0027_delivery
Revises: 0026_briefs
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_delivery"
down_revision = "0026_briefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "postmark_event",
        sa.Column("record_id", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("delivery_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_postmark_event_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_postmark_event")),
        sa.UniqueConstraint("record_id", name=op.f("uq_postmark_event_record_id")),
    )
    with op.batch_alter_table("postmark_event", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_postmark_event_company_id"), ["company_id"], unique=False
        )

    op.create_table(
        "delivery",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("brief_issue_id", sa.Uuid(), nullable=True),
        sa.Column("recipient_id", sa.Uuid(), nullable=True),
        sa.Column("ref", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_id", sa.String(length=200), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("link_token_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["brief_issue_id"],
            ["brief_issue.id"],
            name=op.f("fk_delivery_brief_issue_id_brief_issue"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_delivery_company_id_company")
        ),
        sa.ForeignKeyConstraint(
            ["recipient_id"], ["recipient.id"], name=op.f("fk_delivery_recipient_id_recipient")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_delivery")),
        sa.UniqueConstraint("company_id", "ref", name=op.f("uq_delivery_company_id_ref")),
    )
    with op.batch_alter_table("delivery", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_delivery_brief_issue_id"), ["brief_issue_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_delivery_company_id"), ["company_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_delivery_provider_id"), ["provider_id"], unique=False)

    op.create_table(
        "link_token",
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.Column("brief_issue_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["brief_issue_id"],
            ["brief_issue.id"],
            name=op.f("fk_link_token_brief_issue_id_brief_issue"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_link_token_company_id_company")
        ),
        sa.ForeignKeyConstraint(
            ["recipient_id"], ["recipient.id"], name=op.f("fk_link_token_recipient_id_recipient")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_link_token")),
        sa.UniqueConstraint("hash", name=op.f("uq_link_token_hash")),
    )
    with op.batch_alter_table("link_token", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_link_token_company_id"), ["company_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_link_token_recipient_id"), ["recipient_id"], unique=False
        )

    with op.batch_alter_table("company", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("weekly_digest", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    with op.batch_alter_table("recipient", schema=None) as batch_op:
        batch_op.add_column(sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("bounced_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("recipient", schema=None) as batch_op:
        batch_op.drop_column("bounced_at")
        batch_op.drop_column("unsubscribed_at")

    with op.batch_alter_table("company", schema=None) as batch_op:
        batch_op.drop_column("weekly_digest")

    with op.batch_alter_table("link_token", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_link_token_recipient_id"))
        batch_op.drop_index(batch_op.f("ix_link_token_company_id"))

    op.drop_table("link_token")
    with op.batch_alter_table("delivery", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_delivery_provider_id"))
        batch_op.drop_index(batch_op.f("ix_delivery_company_id"))
        batch_op.drop_index(batch_op.f("ix_delivery_brief_issue_id"))

    op.drop_table("delivery")
    with op.batch_alter_table("postmark_event", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_postmark_event_company_id"))

    op.drop_table("postmark_event")
