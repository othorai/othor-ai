# SPDX-License-Identifier: Apache-2.0
"""The free trial of a company that signed itself up (open sign-up, owner 16 Nov 2026).

`company.trial_ends_at` is set at sign-up when `OTHOR_SIGNUP=open` and `OTHOR_TRIAL_DAYS` > 0
(othor.ai: 14). From the worker's minute loop: 3 days before the end, one email to every member
(how to continue; download the data first if they want a copy); at the end, the company is
closed exactly as Delete company closes it (`closing.close`, actor "trial"): runs, briefs,
sign-in and links stop, the sources' credentials are deleted, and the existing purge removes it
for good 30 days later (`purging`, with its own reminder). `othor admin trial` extends or ends a
trial; `othor admin reopen` brings back a closed one before its purge date.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.core.config import Settings
from othor.core.events import record_platform_event
from othor.core.models import Company

REMIND_BEFORE = dt.timedelta(days=3)
ACTOR = "trial"


def _on(c: Company, t: dt.datetime) -> str:
    from othor.core import fmt
    from othor.core.localtime import local_date

    return fmt.day_month_year(local_date(c, t))


def _mail(s: Session, c: Company, relay: Any, build: Any) -> int:
    from othor.closing import members
    from othor.core.tenancy import tenant_scope
    from othor.mail.relay import MailSendError

    with tenant_scope(c.id):
        people = members(s)
    sent = 0
    for to, _name in people:
        try:
            relay.send(build(to))
            sent += 1
        except MailSendError:
            pass
    return sent


def remind(s: Session, settings: Settings, relay: Any, c: Company, now: dt.datetime) -> int:
    assert c.trial_ends_at is not None
    ends = _on(c, c.trial_ends_at)
    from othor.mail.messages import trial_reminder

    sent = _mail(
        s,
        c,
        relay,
        lambda to: trial_reminder(
            to=to, company_name=c.name, slug=c.slug, ends_on=ends, settings=settings
        ),
    )
    c.trial_reminded_at = now
    record_platform_event(
        s, actor=ACTOR, action="mail.trial_reminder", object_type="company", object_id=c.id,
        after={"sent": sent, "ends_on": ends}, at=now,
    )  # fmt: skip
    return sent


def end(
    s: Session, settings: Settings, relay: Any, auth: Any, c: Company, now: dt.datetime
) -> None:
    from othor import closing
    from othor.core.tenancy import tenant_scope
    from othor.mail.messages import trial_ended

    assert c.trial_ends_at is not None
    ended = auth.end_company(c.id) if auth is not None else 0
    with tenant_scope(c.id):
        done = closing.close(s, c, actor=ACTOR, now=now, sessions_ended=ended)
    _mail(
        s, c, relay,
        lambda to: trial_ended(
            to=to, company_name=c.name, slug=c.slug, ended_on=_on(c, c.trial_ends_at or now),
            purge_on=_on(c, done.purge_after), settings=settings,
        ),
    )  # fmt: skip


def tick(
    sf: sessionmaker[Session], settings: Settings, relay: Any, auth: Any, now: dt.datetime
) -> int:
    """Every minute in the worker: reminders 3 days before, the close at the end. Returns the
    trials ended."""
    from othor.core.tenancy import system_scope

    with sf() as s, system_scope("trials: companies on a free trial"):
        due = [
            (c.id, c.trial_ends_at, c.trial_reminded_at)
            for c in s.scalars(
                sa.select(Company).where(
                    Company.trial_ends_at.is_not(None), Company.deleted_at.is_(None)
                )
            )
        ]
    ended = 0
    for cid, ends_at, reminded in due:
        if ends_at is None:
            continue
        if now >= ends_at:
            with sf() as s, system_scope("trials: end one trial"):
                c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
                if c.deleted_at is None:
                    end(s, settings, relay, auth, c, now)
                    s.commit()
                    ended += 1
        elif reminded is None and now >= ends_at - REMIND_BEFORE:
            with sf() as s, system_scope("trials: remind one trial"):
                c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
                remind(s, settings, relay, c, now)
                s.commit()
    return ended
