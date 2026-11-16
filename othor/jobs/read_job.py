# SPDX-License-Identifier: Apache-2.0
"""Read a Google Sheets source's tabs into the analytics store, before the first Confirm.

After setup, Read now is a source-scoped manual run (M5, `othor.engine`); this job is what it is
during setup, when there is no spec to run yet (docs/OPEN-QUESTIONS 5 Nov).

payload: {"source_id": str}
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from othor.connectors import kinds
from othor.connectors.models import Source
from othor.connectors.sheets.read import duckdb_table, read_tabs
from othor.jobs.models import Job
from othor.jobs.runner import WorkerContext

__all__ = ["duckdb_table", "run"]


def run(ctx: WorkerContext, job: Job) -> None:
    source_id = uuid.UUID(job.payload["source_id"])
    with ctx.sf() as s:
        kind = s.scalars(sa.select(Source.kind).where(Source.id == source_id)).one()
    k = kinds.get(kind)
    if k.family != "local":  # a sql source is read in place by the run
        return
    (k.fetch or read_tabs)(ctx.settings, ctx.sf, ctx.key, source_id, ctx.clock)
