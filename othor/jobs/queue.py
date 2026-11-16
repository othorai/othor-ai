# SPDX-License-Identifier: Apache-2.0
"""Enqueue a job. Imports only the job model, so connectors and web can enqueue without
importing the handlers (which include the AI label job)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from othor.jobs.models import Job


def enqueue(
    s: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    run_after: datetime | None = None,
    dedupe_key: str | None = None,
) -> Job:
    """Inside the caller's tenant scope and transaction. A `dedupe_key` is unique per company
    (e.g. one nightly run per local day)."""
    job = Job(kind=kind, payload=payload, run_after=run_after, dedupe_key=dedupe_key)
    s.add(job)
    s.flush()
    return job
