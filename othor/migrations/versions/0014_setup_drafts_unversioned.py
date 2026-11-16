# SPDX-License-Identifier: Apache-2.0
"""setup creates no Ontology versions: for companies still in setup, versions and specs written
before Confirm become draft records again (decided 4 Nov 2026; Confirm makes version 1)

Revision ID: 0014_setup_drafts_unversioned
Revises: 0011_home_questions
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_setup_drafts_unversioned"
down_revision = "0011_home_questions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    in_setup = [
        r[0]
        for r in bind.execute(
            sa.text(
                "SELECT c.id FROM company c LEFT JOIN setup_state s ON s.company_id = c.id "
                "WHERE s.current_step IS NULL OR s.current_step <> 'done'"
            )
        )
    ]
    if not in_setup:
        return
    dialect = bind.dialect.name
    # the spec table is append-only; this one-off clean-up of pre-Confirm specs lifts the guard
    # for its own statements only
    if dialect == "postgresql":
        op.execute("ALTER TABLE spec DISABLE TRIGGER spec_append_only")
    else:
        op.execute("DROP TRIGGER IF EXISTS spec_no_delete")
    for cid in in_setup:
        p = {"c": cid}
        bind.execute(
            sa.text(
                "UPDATE ontology_record SET status = 'draft', version_from = NULL, "
                "version_to = NULL WHERE company_id = :c AND status = 'active'"
            ),
            p,
        )
        bind.execute(
            sa.text(
                "UPDATE ontology_record SET version_from = NULL, version_to = NULL "
                "WHERE company_id = :c AND status = 'retired'"
            ),
            p,
        )
        bind.execute(sa.text("DELETE FROM spec WHERE company_id = :c"), p)
        bind.execute(sa.text("DELETE FROM ontology_version WHERE company_id = :c"), p)
    if dialect == "postgresql":
        op.execute("ALTER TABLE spec ENABLE TRIGGER spec_append_only")
    else:
        op.execute(
            "CREATE TRIGGER spec_no_delete BEFORE DELETE ON spec "
            "BEGIN SELECT RAISE(ABORT, 'spec is append-only'); END"
        )


def downgrade() -> None:
    # Drafts stay drafts: the versions removed here were never confirmed by the owner.
    pass
