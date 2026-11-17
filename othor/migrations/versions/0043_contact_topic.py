# SPDX-License-Identifier: Apache-2.0
"""access_request.topic and company_slug: one Contact us form with a topic (owner, 17 Nov 2026);
one message per email, topic and day.

Revision ID: 0043_contact_topic
Revises: 0042_company_ai_cap_trial
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0043_contact_topic"
down_revision = "0042_company_ai_cap_trial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("access_request") as t:
        t.add_column(sa.Column("topic", sa.String(length=32), nullable=True))
        t.add_column(sa.Column("company_slug", sa.String(length=63), nullable=True))
        t.drop_constraint("uq_access_request_email_day", type_="unique")
        t.create_unique_constraint("uq_access_request_email_day_topic", ["email", "day", "topic"])


def downgrade() -> None:
    # back to one a day: keep each day's first message from an email
    op.execute(
        "DELETE FROM access_request WHERE EXISTS (SELECT 1 FROM access_request b "
        "WHERE b.email = access_request.email AND b.day = access_request.day "
        "AND b.created_at < access_request.created_at)"
    )
    with op.batch_alter_table("access_request") as t:
        t.drop_constraint("uq_access_request_email_day_topic", type_="unique")
        t.create_unique_constraint("uq_access_request_email_day", ["email", "day"])
        t.drop_column("company_slug")
        t.drop_column("topic")
