# SPDX-License-Identifier: Apache-2.0
"""The purge after 30 days (v0.18, M18.3; Delete step 2).

A company its owner deleted (M18.2: `deleted_at`, `purge_after`) keeps everything until
`purge_after`, so support can restore it. Then `purge()` removes every row it owns and its folder
on the data volume, and scrubs the `company` row to a placeholder (its web address freed), so the
instance's own log (`platform_event`) still points at something. A reminder goes to every member
7 days before.

The tables are read from the metadata: every table with a `company_id` column is purged, children
first, so a new tenant table is covered with no edit here. Every other table is named in `GLOBAL`
with what happens to it; a guard test fails on one that isn't. The append-only `event` and `spec`
rows can go only while this company's `purge_lock` row exists, which is only inside the purge's
own transaction (migration 0040); UPDATE stays refused, and `platform_event` is never purged.
"""

from __future__ import annotations

import datetime as dt
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from othor.core.config import Settings
from othor.core.events import record_platform_event
from othor.core.models import AppUser, Base, Company, Membership, PlatformEvent, PurgeLock

REMIND_BEFORE = dt.timedelta(days=7)
# carry company_id but only othor.core.auth touches them: purged by `auth.purge_rows`
AUTH = ("auth_session", "login_code")

# every table without a company_id column, and what the purge does with it
GLOBAL: dict[str, str] = {
    "company": "scrubbed to a placeholder: name, address, website and logo removed",
    "app_user": "deleted when the company's membership was its last",
    "invitation": "deleted when the company used it",
    "access_request": "kept: a request made on the website, not company data",
    "platform_event": "kept: the instance's own log (ids, the old address and counts only)",
    "rate_limit": "kept: hashed counters, no company data",
    "outside_point": "kept: public series (External Signals), no company data",
    "outside_fetch": "kept: the fetch log of public series, no company data",
    "worker_heartbeat": "kept: the worker's heartbeat, no company data",
    "purge_lock": "the lock itself: a row only inside the purge's transaction",
    "auth_session": "deleted by othor.core.auth.purge_rows: the company's, and gone users'",
    "login_code": "deleted by othor.core.auth.purge_rows: the company's, and gone users'",
}


@dataclass
class Purged:
    company_id: uuid.UUID
    slug: str
    tables: dict[str, int] = field(default_factory=dict)
    users: int = 0
    files_bytes: int = 0

    @property
    def rows(self) -> int:
        return sum(self.tables.values()) + self.users


def tenant_tables() -> list[sa.Table]:
    """Every table with a company_id column, children before the tables they point at."""
    import othor.all_models  # noqa: F401  (every table in the metadata)

    return [
        t
        for t in reversed(Base.metadata.sorted_tables)
        if "company_id" in t.columns and t.name not in ("company", "purge_lock", *AUTH)
    ]


def _bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def purge(s: Session, settings: Settings, company_id: uuid.UUID, now: dt.datetime) -> Purged:
    """Everything the company owns, in one transaction (the caller commits); the folder on the
    data volume is removed by `tick` after the commit. Run with no tenant scope: every statement
    names the company itself."""
    c = s.scalars(sa.select(Company).where(Company.id == company_id)).one()
    if c.deleted_at is None or c.purge_after is None or now < c.purge_after:
        raise ValueError(f"{c.slug} is not due for purging")
    if c.purged_at is not None:
        raise ValueError(f"{c.slug} is already purged")
    done = Purged(company_id, c.slug, files_bytes=_bytes(settings.company_dir(company_id)))
    s.add(PurgeLock(company_id=company_id, started_at=now))
    s.flush()  # a second worker's insert fails here: one purge at a time
    users = list(
        s.scalars(sa.select(Membership.user_id).where(Membership.company_id == company_id))
    )
    email_of = {
        u: e
        for u, e in s.execute(sa.select(AppUser.id, AppUser.email).where(AppUser.id.in_(users)))
    }
    for t in tenant_tables():
        n = s.execute(sa.delete(t).where(t.c.company_id == company_id)).rowcount  # type: ignore[attr-defined]
        if n:
            done.tables[t.name] = int(n)
    invitation = Base.metadata.tables["invitation"]
    n = s.execute(sa.delete(invitation).where(invitation.c.used_by_company == company_id)).rowcount  # type: ignore[attr-defined]
    if n:
        done.tables["invitation"] = int(n)
    # a user whose last membership this was: their sessions and codes (othor.core.auth), then
    # the user
    from othor.core.auth import purge_rows

    gone = {
        u: email_of[u]
        for u in users
        if s.scalar(sa.select(sa.func.count()).where(Membership.user_id == u)) == 0
    }
    auth_rows = purge_rows(s, company_id, gone)
    if auth_rows:
        done.tables["auth_session+login_code"] = auth_rows
    if gone:
        app_user = Base.metadata.tables["app_user"]
        done.users = int(
            s.execute(sa.delete(app_user).where(app_user.c.id.in_(list(gone)))).rowcount  # type: ignore[attr-defined]
        )
    # the placeholder: the web address freed, nothing of the company's left
    s.execute(
        sa.update(Company.__table__)  # type: ignore[arg-type]
        .where(Company.__table__.c.id == company_id)  # type: ignore[attr-defined]
        .values(
            slug=f"deleted-{company_id.hex[:8]}",
            name="Deleted company",
            website=None,
            logo_sha=None,
            closed_by=None,
            purged_at=now,
        )
    )
    lock = Base.metadata.tables["purge_lock"]
    s.execute(sa.delete(lock).where(lock.c.company_id == company_id))
    record_platform_event(
        s,
        actor="system",
        action="company.purged",
        object_type="company",
        object_id=company_id,
        after={
            "slug": done.slug,
            "rows": done.rows,
            "tables": dict(sorted(done.tables.items())),
            "users": done.users,
            "files_bytes": done.files_bytes,
        },
        at=now,
    )
    return done


def _sent(s: Session, action: str, company_id: uuid.UUID) -> bool:
    return (
        s.scalar(
            sa.select(sa.func.count()).where(
                PlatformEvent.action == action, PlatformEvent.object_id == str(company_id)
            )
        )
        or 0
    ) > 0


def remind(s: Session, settings: Settings, relay: Any, c: Company, now: dt.datetime) -> int:
    """The reminder 7 days before, to every member, once. Returns the emails sent."""
    from othor.closing import members
    from othor.core import fmt
    from othor.core.localtime import local_date
    from othor.core.tenancy import tenant_scope
    from othor.mail.messages import purge_reminder
    from othor.mail.relay import MailSendError

    if _sent(s, "mail.purge_reminder", c.id):
        return 0
    assert c.purge_after is not None
    on = fmt.day_month_year(local_date(c, c.purge_after))
    with tenant_scope(c.id):
        people = members(s)
    sent = 0
    for to, _name in people:
        try:
            relay.send(
                purge_reminder(
                    to=to, company_name=c.name, slug=c.slug, purge_on=on, settings=settings
                )
            )
            sent += 1
        except MailSendError:
            pass
    record_platform_event(
        s,
        actor="system",
        action="mail.purge_reminder",
        object_type="company",
        object_id=c.id,
        after={"members": len(people), "sent": sent, "purge_on": on},
        at=now,
    )
    return sent


def tick(sf: sessionmaker[Session], settings: Settings, relay: Any, now: dt.datetime) -> int:
    """Every minute in the worker: reminders from 7 days before, the purge when it's due.
    Returns the companies purged."""
    from othor.core.tenancy import system_scope

    purged = 0
    with sf() as s, system_scope("purge: deleted companies due a reminder or a purge"):
        due = s.scalars(
            sa.select(Company).where(
                Company.deleted_at.is_not(None),
                Company.purge_after.is_not(None),
                Company.purged_at.is_(None),
            )
        ).all()
        ids = [
            (c.id, c.purge_after)
            for c in due
            if c.purge_after is not None and now >= c.purge_after - REMIND_BEFORE
        ]
    for cid, purge_after in ids:
        with sf() as s, system_scope("purge: one deleted company"):
            c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
            if now < purge_after:
                remind(s, settings, relay, c, now)
                s.commit()
                continue
            try:
                purge(s, settings, cid, now)
                s.commit()
            except IntegrityError:  # another worker holds this company's lock
                s.rollback()
                continue
        shutil.rmtree(settings.company_dir(cid), ignore_errors=True)
        purged += 1
    # a folder a crash left behind after its company was purged
    with sf() as s, system_scope("purge: folders of purged companies"):
        done = list(s.scalars(sa.select(Company.id).where(Company.purged_at.is_not(None))))
    for cid in done:
        if settings.company_dir(cid).exists():
            shutil.rmtree(settings.company_dir(cid), ignore_errors=True)
    return purged
