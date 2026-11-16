# SPDX-License-Identifier: Apache-2.0
"""Read one upload (docs/01 §3): code first; with the model, PDFs and images by Claude.

payload: {"upload_id": str}
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from othor.jobs.models import Job
from othor.jobs.runner import WorkerContext
from othor.setup import limits, uploads
from othor.setup.models import Upload


def run(ctx: WorkerContext, job: Job) -> None:
    read(ctx, uuid.UUID(job.payload["upload_id"]))


def read(ctx: WorkerContext, uid: uuid.UUID) -> None:
    with ctx.sf() as s:
        up = s.scalars(sa.select(Upload).where(Upload.id == uid)).one()
        up.status = "reading"
        s.commit()
        raw = uploads.read_bytes(ctx.settings, ctx.key, up)
        cid = up.company_id
        s.expunge(up)
    got = limits.read_limited(raw, up.kind, up.filename)  # time and memory limits (M11b §6b)
    if up.kind in ("pdf", "image"):
        by_model = uploads.model_read(ctx.gateway, cid, up, raw)
        if by_model is not None:
            got = by_model
    with ctx.sf() as s:
        row = s.scalars(sa.select(Upload).where(Upload.id == uid)).one()
        uploads.store_parse(s, row, got)
        s.commit()
