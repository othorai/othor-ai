# SPDX-License-Identifier: Apache-2.0
"""content hashes: ontology_version.content_hash (its live records without number, time, author,
reason or citations) and spec.content_hash (the compiled document without its version number)
(M10b, decided 6 Nov 2026). Versions are backfilled here; specs are append-only (trigger), so
older specs are read through `content.spec_content_hash`.

Revision ID: 0029_content_hash
Revises: 0028_home
Create Date: 2026-11-06
"""

import hashlib
import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0029_content_hash"
down_revision = "0028_home"
branch_labels = None
depends_on = None


def _canonical(doc: Any) -> str:
    # frozen copy of compiler.api.canonical for this migration (no floats in records)
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def upgrade() -> None:
    with op.batch_alter_table("ontology_version", schema=None) as b:
        b.add_column(sa.Column("content_hash", sa.String(length=64), nullable=True))
    with op.batch_alter_table("spec", schema=None) as b:
        b.add_column(sa.Column("content_hash", sa.String(length=64), nullable=True))

    conn = op.get_bind()
    rec = sa.table(
        "ontology_record",
        sa.column("company_id", sa.Uuid()),
        sa.column("type", sa.String()),
        sa.column("key", sa.String()),
        sa.column("body", sa.JSON()),
        sa.column("version_from", sa.Integer()),
        sa.column("version_to", sa.Integer()),
    )
    ver = sa.table(
        "ontology_version",
        sa.column("id", sa.Uuid()),
        sa.column("company_id", sa.Uuid()),
        sa.column("number", sa.Integer()),
        sa.column("content_hash", sa.String()),
    )
    rows = conn.execute(
        sa.select(
            rec.c.company_id,
            rec.c.type,
            rec.c.key,
            rec.c.body,
            rec.c.version_from,
            rec.c.version_to,
        ).where(rec.c.version_from.is_not(None))
    ).all()
    by_company: dict[Any, list[Any]] = {}
    for r in rows:
        by_company.setdefault(r.company_id, []).append(r)
    for v in conn.execute(sa.select(ver.c.id, ver.c.company_id, ver.c.number)).all():
        live = [
            [r.type, r.key, r.body if isinstance(r.body, dict) else json.loads(r.body)]
            for r in by_company.get(v.company_id, [])
            if r.version_from <= v.number and (r.version_to is None or r.version_to > v.number)
        ]
        h = hashlib.sha256(_canonical({"records": sorted(live)}).encode()).hexdigest()
        conn.execute(sa.update(ver).where(ver.c.id == v.id).values(content_hash=h))


def downgrade() -> None:
    with op.batch_alter_table("spec", schema=None) as b:
        b.drop_column("content_hash")
    with op.batch_alter_table("ontology_version", schema=None) as b:
        b.drop_column("content_hash")
