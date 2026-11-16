# SPDX-License-Identifier: Apache-2.0
"""Export everything, for a company too large to build while the owner waits (M18.1): build the
zip onto the data volume, then email the owner a link to it (sign-in needed; kept 48 hours).
Idempotent: a retry reuses the file and never sends the email twice."""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa

from othor import exporting
from othor.core.events import SYSTEM_ACTOR, record_event
from othor.core.models import Company
from othor.jobs.models import Job
from othor.jobs.runner import WorkerContext
from othor.mail.delivery import owner
from othor.mail.models import Delivery
from othor.mail.relay import Bounced, MailSendError, Message


def run(ctx: WorkerContext, job: Job) -> None:
    now = ctx.clock()
    path = exporting.zip_path(ctx.settings, job.company_id, job.id)
    ref = f"export:{job.id}"
    with ctx.sf() as s:
        c = s.scalars(sa.select(Company).where(Company.id == job.company_id)).one()
        if not path.exists():
            got = exporting.build(s, ctx.settings, c)
            exporting.write(path, got.data)
            record_event(
                s,
                actor=SYSTEM_ACTOR,
                action="company.exported",
                object_type="company",
                object_id=c.id,
                after={
                    "mode": "job",
                    "files": len(got.manifest["files"]),
                    "bytes": len(got.data),
                    "sha256": _sha(got.data),
                },
                at=now,
            )
            s.commit()
        if s.scalars(
            sa.select(Delivery.id).where(Delivery.ref == ref, Delivery.status == "sent")
        ).first():
            return
        who = owner(s)
        if who is None:
            return
        msg = _email(ctx, c, job, who[0], now)
        row = s.scalars(sa.select(Delivery).where(Delivery.ref == ref)).first()
        if row is None:
            row = Delivery(kind="alert", ref=ref, created_at=now)
            s.add(row)
        row.status = "queued"
        s.commit()
        try:
            row.provider_id = ctx.mail().send(msg)
            row.status, row.sent_at, row.error = "sent", now, None
        except Bounced as e:
            row.status, row.error = "bounced", str(e)[:300]
        except MailSendError as e:
            row.status, row.error = "failed", str(e)[:300]
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="mail.export_ready",
            object_type="company",
            object_id=c.id,
            after={"status": row.status, "ref": ref},
            at=now,
        )
        s.commit()
        if row.status == "failed":
            raise MailSendError("export email not sent")


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _email(ctx: WorkerContext, c: Company, job: Job, to: str, now: dt.datetime) -> Message:
    from othor.mail import alerts

    return alerts.export_ready(ctx.settings, c, job.id, now + exporting.KEEP, to=to)
