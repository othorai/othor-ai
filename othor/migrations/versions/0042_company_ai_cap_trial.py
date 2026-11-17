# SPDX-License-Identifier: Apache-2.0
"""company.ai_cap_tokens, trial_ends_at, trial_reminded_at: open sign-up's AI cap and 14-day
trial (owner, 16 Nov 2026; docs/plans/open-signup.md).

Revision ID: 0042_company_ai_cap_trial
Revises: 0041_big_picture
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0042_company_ai_cap_trial"
down_revision = "0041_big_picture"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # recreate="never", as in 0036, 0039 and 0041: company is added to in place
    with op.batch_alter_table("company", recreate="never") as b:
        b.add_column(sa.Column("ai_cap_tokens", sa.Integer(), nullable=True))
        b.add_column(sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("trial_reminded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("company", recreate="never") as b:
        b.drop_column("trial_reminded_at")
        b.drop_column("trial_ends_at")
        b.drop_column("ai_cap_tokens")
