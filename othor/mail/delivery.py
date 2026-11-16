# SPDX-License-Identifier: Apache-2.0
"""The 06:00 delivery (docs/02, docs/09): one brief's stored issue for the night, emailed to
each of its active recipients with their own signed links.

- Reads the issue the run wrote (`brief_issue`) and the words the page shows; computes nothing,
  calls no model (othor.mail never imports othor.ai).
- One `delivery` row per issue and recipient (`ref` unique): a retried job skips what was sent,
  and sends again only what failed. Unsubscribed, removed and bounced recipients get nothing.
- The run not sealed yet → `wait` (the job comes back in 5 minutes). The run failed → no brief;
  8c went to the owner when it failed.
- Every send writes `brief.delivered` (recipient id and status, never the body).
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.briefs.models import Brief, BriefIssue, BriefVersion, Recipient
from othor.briefs.words import shown
from othor.core import fmt
from othor.core.config import Settings
from othor.core.crypto import InstanceKey
from othor.core.events import SYSTEM_ACTOR, record_event
from othor.core.localtime import local_now
from othor.core.models import AppUser, Company, Membership
from othor.engine.models import Run, RunStep
from othor.mail import links
from othor.mail.messages import BriefMail, brief_email
from othor.mail.models import OPEN, UNSUBSCRIBE, Delivery
from othor.mail.relay import Bounced, MailSendError, Relay
from othor.ontology.models import Spec

log = logging.getLogger("othor.mail")


@dataclass
class Outcome:
    status: str  # sent · wait · failed_run · no_issue · off · no_brief
    sent: int = 0
    skipped: int = 0
    bounced: int = 0
    failed: int = 0
    issue_id: uuid.UUID | None = None
    run: Run | None = field(default=None, repr=False)


def owner(s: Session) -> tuple[str, str] | None:
    """The company's owner (email, name): reply-to on briefs, the recipient of alerts."""
    row = s.execute(
        sa.select(AppUser.email, AppUser.name)
        .join(Membership, Membership.user_id == AppUser.id)
        .where(Membership.role == "owner")
        .order_by(Membership.created_at)
    ).first()
    return (str(row[0]), str(row[1] or "")) if row else None


def night_run(s: Session, run_date: dt.date) -> Run | None:
    """The nightly run for a date (the latest, if it was re-run)."""
    return s.scalars(
        sa.select(Run)
        .where(Run.kind == "nightly", Run.run_date == run_date)
        .order_by(Run.number.desc())
        .limit(1)
    ).first()


def sources_line(s: Session, company: Company, run: Run) -> list[str]:
    """'erp_db 02:03' for each source the run read, 'sales_dw not read' for a stale one (8b1)."""
    out = []
    for st in s.scalars(
        sa.select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.ordinal)
    ):
        if not st.name.startswith("read:"):
            continue
        name = st.name.split(":", 1)[1]
        if st.status == "done" and st.ended_at:
            out.append(f"{name} {fmt.time_hm(local_now(company, st.ended_at).time())}")
        else:
            out.append(f"{name} not read")
    return out


def brief_weather(
    settings: Settings | None, run: Run, brief_id: uuid.UUID
) -> dict[str, Any] | None:
    """The brief's own weather and outlook as its run stored them (v0.12 part 1c); None for a
    run sealed before it."""
    from othor.briefs import weather
    from othor.engine.store import RunFiles

    if settings is None:
        return None
    got = weather.stored_brief(RunFiles(settings.runs_dir(run.company_id), run.id), brief_id)
    return None if got is None else dict(got["weather"])


def brief_mail(
    s: Session,
    company: Company,
    brief: Brief,
    issue: BriefIssue,
    settings: Settings | None = None,
) -> BriefMail:
    run = s.scalars(sa.select(Run).where(Run.id == issue.run_id)).one()
    spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one()
    version = s.scalars(
        sa.select(BriefVersion).where(
            BriefVersion.brief_id == brief.id, BriefVersion.number == issue.brief_version
        )
    ).one_or_none()
    who = owner(s)
    return BriefMail(
        company_name=company.name,
        slug=company.slug,
        timezone=company.timezone,
        brief_name=brief.name,
        date=issue.date,
        body=dict(issue.body),
        words=shown(issue.body, issue.prose),
        email_format=str((version.body if version else {}).get("email_format") or "simple"),
        run_number=run.number,
        spec_version=int(spec.compiled.get("ontology_version") or 0) or None,
        sources=sources_line(s, company, run),
        owner_email=who[0] if who else None,
        weather=brief_weather(settings, run, brief.id),
        currency=str((spec.compiled.get("company") or {}).get("currency") or "INR"),
        logo=company_logo(settings, company),
    )


def company_logo(settings: Settings | None, company: Company) -> bytes | None:
    """The company's logo as it is now (M15.7), or None."""
    from othor.core import logo

    return logo.read(settings, company.id, company.logo_sha) if settings else None


def send_issue(
    s: Session,
    settings: Settings,
    key: InstanceKey,
    relay: Relay,
    company: Company,
    issue: BriefIssue,
    recipient: Recipient,
    mail: BriefMail,
    *,
    kind: str,
    ref: str,
    now: dt.datetime,
) -> Delivery:
    """One email of an issue to one recipient, with fresh Open and Unsubscribe links. Commits
    the row before sending (so a crash leaves a `queued` row, sent again on retry) and after."""
    row = s.scalars(sa.select(Delivery).where(Delivery.ref == ref)).first()
    if row is None:
        row = Delivery(
            kind=kind, brief_issue_id=issue.id, recipient_id=recipient.id, ref=ref, created_at=now
        )
        s.add(row)
    open_tok, open_row = links.issue(
        s,
        key,
        company.id,
        recipient_id=recipient.id,
        brief_issue_id=issue.id,
        purpose=OPEN,
        now=now,
    )
    unsub_tok, _ = links.issue(
        s,
        key,
        company.id,
        recipient_id=recipient.id,
        brief_issue_id=issue.id,
        purpose=UNSUBSCRIBE,
        now=now,
    )
    row.status, row.link_token_id, row.error = "queued", open_row.id, None
    s.commit()
    msg = brief_email(
        mail,
        to=recipient.email,
        open_url=settings.instance_url(company.slug, f"/l/{open_tok}"),
        unsubscribe_url=settings.instance_url(company.slug, f"/u/{unsub_tok}"),
        settings=settings,
    )
    try:
        row.provider_id = relay.send(msg)
        row.status, row.sent_at = "sent", now
    except Bounced as e:
        row.status, row.error = "bounced", str(e)[:300]
        mark_bounced(s, recipient, now, reason=str(e))
    except MailSendError as e:
        row.status, row.error = "failed", str(e)[:300]
    record_event(
        s,
        actor=SYSTEM_ACTOR,
        action="brief.delivered" if kind == "brief" else "brief.link_sent",
        object_type="brief_issue",
        object_id=issue.id,
        after={"recipient": recipient.id, "status": row.status},
        at=now,
    )
    s.commit()
    return row


def mark_bounced(s: Session, recipient: Recipient, now: dt.datetime, *, reason: str) -> None:
    """A bounce: the recipient gets nothing until the owner adds them back (Delivery tab)."""
    if recipient.status == "bounced":
        return
    before = recipient.status
    recipient.status, recipient.bounced_at = "bounced", now
    record_event(
        s,
        actor=SYSTEM_ACTOR,
        action="recipient.bounced",
        object_type="recipient",
        object_id=recipient.id,
        before={"status": before},
        after={"status": "bounced", "brief": recipient.brief_id, "reason": reason[:120]},
        at=now,
    )


def deliver_brief(
    s: Session,
    settings: Settings,
    key: InstanceKey,
    relay: Relay,
    company: Company,
    brief_id: uuid.UUID,
    run_date: dt.date,
    now: dt.datetime,
) -> Outcome:
    if not company.email_delivery:
        return Outcome("off")
    brief = s.scalars(sa.select(Brief).where(Brief.id == brief_id)).one_or_none()
    if brief is None or brief.status != "active" or brief.deleted_at is not None:
        return Outcome("no_brief")
    run = night_run(s, run_date)
    if run is None or run.status == "running":
        return Outcome("wait", run=run)
    if run.status == "failed":
        return Outcome("failed_run", run=run)
    issue = s.scalars(
        sa.select(BriefIssue).where(BriefIssue.brief_id == brief.id, BriefIssue.run_id == run.id)
    ).one_or_none()
    if issue is None:  # the brief was made after the run
        return Outcome("no_issue", run=run)
    return send_to_recipients(s, settings, key, relay, company, brief, issue, run, now)


def deliver_update(
    s: Session,
    settings: Settings,
    key: InstanceKey,
    relay: Relay,
    company: Company,
    issue_id: uuid.UUID,
    now: dt.datetime,
) -> Outcome:
    """Run again now's "Send the updated brief" (v0.11): the issue a rerun made, to the brief's
    recipients, only when the owner asks. The same email and links as the 06:00 one."""
    if not company.email_delivery:
        return Outcome("off")
    issue = s.scalars(sa.select(BriefIssue).where(BriefIssue.id == issue_id)).one_or_none()
    if issue is None:
        return Outcome("no_issue")
    brief = s.scalars(sa.select(Brief).where(Brief.id == issue.brief_id)).one_or_none()
    if brief is None or brief.status != "active" or brief.deleted_at is not None:
        return Outcome("no_brief")
    run = s.scalars(sa.select(Run).where(Run.id == issue.run_id)).one()
    return send_to_recipients(s, settings, key, relay, company, brief, issue, run, now)


def send_to_recipients(
    s: Session,
    settings: Settings,
    key: InstanceKey,
    relay: Relay,
    company: Company,
    brief: Brief,
    issue: BriefIssue,
    run: Run,
    now: dt.datetime,
) -> Outcome:
    """One issue to every active recipient of its brief; one already sent to is skipped."""
    out = Outcome("sent", issue_id=issue.id, run=run)
    mail = brief_mail(s, company, brief, issue, settings)
    recipients = s.scalars(
        sa.select(Recipient)
        .where(Recipient.brief_id == brief.id, Recipient.status == "active")
        .order_by(Recipient.created_at, Recipient.email)
    ).all()
    for r in recipients:
        ref = f"brief:{issue.id}:{r.id}"
        done = s.scalars(sa.select(Delivery.status).where(Delivery.ref == ref)).first()
        if done in ("sent", "bounced"):
            out.skipped += 1
            continue
        row = send_issue(
            s, settings, key, relay, company, issue, r, mail, kind="brief", ref=ref, now=now
        )
        if row.status == "sent":
            out.sent += 1
        elif row.status == "bounced":
            out.bounced += 1
        else:
            out.failed += 1
    log.info(
        "brief delivered",
        extra={"brief": str(brief.id), "sent": out.sent, "failed": out.failed},
    )
    return out
