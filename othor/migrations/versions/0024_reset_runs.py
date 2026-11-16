# SPDX-License-Identifier: Apache-2.0
"""reset run-derived data onto the series-versions format (M6a decisions, 5 Nov 2026)

No customer has run yet, so v0.1 keeps one code path: runs sealed before definition hashes
(meaning) and render hashes were separated are removed, with everything derived from them. The
next run of each company is its first again (back-fill included). Owner data stays: the
Ontology, sources, watches, snoozes, mutes, questions, events. Run directories left on disk are
no longer referenced and can be deleted.

Revision ID: 0024_reset_runs
Revises: 0023_snooze_mute_proposal
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0024_reset_runs"
down_revision = "0023_snooze_mute_proposal"
branch_labels = None
depends_on = None

DERIVED = ("finding", "detector_result", "run_step", "proposal", "series_meta")  # forecast: 0025


def upgrade() -> None:
    for table in DERIVED:
        op.execute(sa.text(f"DELETE FROM {table}"))  # noqa: S608 - fixed table names
    op.execute(sa.text("DELETE FROM run_query WHERE run_id IS NOT NULL"))
    op.execute(sa.text("DELETE FROM run"))


def downgrade() -> None:
    """Nothing to restore: the removed rows were derived and are rebuilt by the next run."""
