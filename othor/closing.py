# SPDX-License-Identifier: Apache-2.0
"""Close a company, and reopen it within 30 days (v0.18, M18.2; the purge is M18.3).

Closing stops everything at once: the company's host stops resolving (sign-in, pages and
recipients' links), the nightly tick and the 06:00 delivery skip it, `claim()` hands out none of
its jobs, every session ends and unused codes are spent (`AuthService.end_company`), and every
source's credentials (passwords and Google tokens) are deleted. Everything else stays until
`purge_after`, so support can reopen it; the sources then stay disconnected until the owner
reconnects them.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core.events import record_event, record_platform_event
from othor.core.models import Company

KEEP_FOR = dt.timedelta(days=30)  # the privacy policy: "deleted 30 days later"
REASON = "Company closed"


class CloseError(ValueError):
    pass


@dataclass(frozen=True)
class Closed:
    purge_after: dt.datetime
    sources: int
    jobs_cancelled: int
    sessions_ended: int


def name_matches(company: Company, typed: str) -> bool:
    """The owner typed the company's name: case and surrounding spaces don't matter."""
    return " ".join(typed.split()).casefold() == " ".join(company.name.split()).casefold()


def close(
    s: Session, company: Company, *, actor: str, now: dt.datetime, sessions_ended: int = 0
) -> Closed:
    """Under the company's tenant scope, in the caller's transaction (the caller commits).
    Sessions and codes are ended by `AuthService.end_company` (the only code that touches the
    auth tables); its count is recorded here."""
    from othor.connectors import credentials
    from othor.connectors.models import Source
    from othor.jobs.models import Job

    if company.deleted_at is not None:
        raise CloseError("This company is already closed")
    purge = now + KEEP_FOR
    company.deleted_at, company.purge_after, company.closed_by = now, purge, str(actor)[:64]
    sources = s.scalars(sa.select(Source).where(Source.deleted_at.is_(None))).all()
    for src in sources:
        credentials.delete(s, src.id)
        before = src.status
        if src.status != "disconnected":
            src.status, src.disconnected_at = "disconnected", now
        src.disconnect_reason = REASON
        record_event(
            s,
            actor=actor,
            action="source.disconnected",
            object_type="source",
            object_id=src.id,
            before={"status": before},
            after={"status": "disconnected", "credentials": "deleted", "reason": REASON},
            at=now,
        )
    queued = s.scalars(sa.select(Job).where(Job.status == "queued")).all()
    for j in queued:
        j.status, j.error, j.finished_at = "cancelled", REASON.lower(), now
    done = Closed(purge, len(sources), len(queued), sessions_ended)
    after: dict[str, Any] = {
        "purge_after": purge,
        "sources": done.sources,
        "jobs_cancelled": done.jobs_cancelled,
        "sessions_ended": done.sessions_ended,
    }
    record_event(
        s,
        actor=actor,
        action="company.closed",
        object_type="company",
        object_id=company.id,
        before={"deleted_at": None},
        after=after,
        at=now,
    )
    record_platform_event(
        s,
        actor=actor,
        action="company.closed",
        object_type="company",
        object_id=company.id,
        after={**after, "slug": company.slug},
        at=now,
    )
    return done


def reopen(s: Session, company: Company, *, actor: str, now: dt.datetime) -> None:
    """Support reopens a closed company before its purge date (`othor admin reopen`)."""
    if company.deleted_at is None:
        raise CloseError(f"{company.slug} isn't closed")
    if company.purge_after is not None and now >= company.purge_after:
        raise CloseError(f"{company.slug} is already past its purge date")
    before = {"deleted_at": company.deleted_at, "purge_after": company.purge_after}
    company.deleted_at = company.purge_after = company.closed_by = None
    record_event(
        s,
        actor=actor,
        action="company.reopened",
        object_type="company",
        object_id=company.id,
        before=before,
        after={"deleted_at": None},
        at=now,
    )
    record_platform_event(
        s,
        actor=actor,
        action="company.reopened",
        object_type="company",
        object_id=company.id,
        after={"slug": company.slug},
        at=now,
    )


def members(s: Session) -> list[tuple[str, str]]:
    """Every member's (email, name), under the tenant scope: each gets the closed email."""
    from othor.core.models import AppUser, Membership

    rows = s.execute(
        sa.select(AppUser.email, AppUser.name)
        .join(Membership, Membership.user_id == AppUser.id)
        .order_by(Membership.created_at)
    ).all()
    return [(str(e), str(n or "")) for e, n in rows]
