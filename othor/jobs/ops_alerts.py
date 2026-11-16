# SPDX-License-Identifier: Apache-2.0
"""Ops alerts (M11b §6): whoever runs this instance hears that a night failed or ran late.

Every minute the worker's tick looks at each company that has been running nightly: today's
nightly run (company time) failed, or isn't sealed by 05:00, an hour before the briefs go out.
New cases go to `OTHOR_OPS_EMAIL` in one email, one line per company with the run number and
the date, and nothing else: no data, no metric names, no error text (the logs and `othor admin`
have those). Each case is said once (a `platform_event`, `ops.alerted`). Separate from 8c, the
owner's own "the run failed" email. Portable: on-premise it goes to the customer's ops address.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from html import escape

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.core import fmt
from othor.core.config import Settings
from othor.core.events import record_platform_event
from othor.core.localtime import local_now
from othor.core.models import Company, PlatformEvent
from othor.core.tenancy import system_scope, tenant_scope
from othor.engine.models import Run
from othor.jobs.models import Job
from othor.ontology.models import OntologyVersion

log = logging.getLogger("othor.ops")

LATE_AFTER = dt.time(5, 0)  # company time; the briefs go at 06:00
SEALED = ("done", "partial")
ACTION = "ops.alerted"


@dataclass(frozen=True)
class Case:
    key: str
    line: str
    company_id: uuid.UUID
    kind: str  # failed | late


def _expected(sf: sessionmaker[Session]) -> list[Company]:
    """Companies the scheduler runs (not paused, confirmed) that have run a night before."""
    with sf() as s, system_scope("ops alerts: which companies run nightly"):
        companies = s.scalars(
            sa.select(Company)
            .where(Company.deleted_at.is_(None), Company.paused.is_(False))
            .order_by(Company.slug)
        ).all()
        confirmed = set(s.scalars(sa.select(OntologyVersion.company_id).distinct()).all())
        s.expunge_all()
    return [c for c in companies if c.id in confirmed]


def cases(sf: sessionmaker[Session], now: dt.datetime) -> list[Case]:
    out: list[Case] = []
    for c in _expected(sf):
        local = local_now(c, now)
        day = local.date()
        with sf() as s, tenant_scope(c.id):
            earlier = s.scalars(
                sa.select(Job.dedupe_key).where(
                    Job.dedupe_key.like("nightly:%"), Job.dedupe_key < f"nightly:{day.isoformat()}"
                )
            ).first()
            if earlier is None:
                continue  # set up today: its first night hasn't come yet
            run = s.scalars(
                sa.select(Run)
                .where(Run.kind == "nightly", Run.run_date == day)
                .order_by(Run.number.desc())
            ).first()
            number, status = (run.number, run.status) if run else (None, None)
        who = f"{c.name} ({c.slug})"
        # this company's time zone and currency for its lines only: set and left, they leaked into
        # whatever the worker's thread formatted next (found by the full test run, 14 Nov 2026)
        with fmt.display_tz(c.timezone), fmt.money_style(c.currency):
            when = fmt.date_short(day)
            if status == "failed":
                out.append(
                    Case(
                        f"ops:failed:{c.id}:{day.isoformat()}",
                        f"{who}: run #{number:04d} failed · {when}",
                        c.id,
                        "failed",
                    )
                )
            elif status not in SEALED and local.time() >= LATE_AFTER:
                cutoff = fmt.clock(LATE_AFTER.strftime("%H:%M"))
                what = f"run #{number:04d} not sealed" if number is not None else "no run"
                out.append(
                    Case(
                        f"ops:late:{c.id}:{day.isoformat()}",
                        f"{who}: {what} by {cutoff} · {when}",
                        c.id,
                        "late",
                    )
                )
    return out


def check(
    sf: sessionmaker[Session], settings: Settings, relay: object, now: dt.datetime
) -> list[str]:
    """Send what's new; return its lines. Off when `OTHOR_OPS_EMAIL` is empty. A failed send is
    logged and tried again on the next tick (nothing is marked said)."""
    if not settings.ops_email:
        return []
    found = cases(sf, now)
    if not found:
        return []
    with sf() as s:
        said = set(
            s.scalars(
                sa.select(PlatformEvent.object_id).where(
                    PlatformEvent.action == ACTION,
                    PlatformEvent.object_id.in_([c.key for c in found]),
                )
            ).all()
        )
    new = [c for c in found if c.key not in said]
    if not new:
        return []
    from othor.mail.messages import sender
    from othor.mail.relay import Message

    n = len(new)
    subject = f"Othor AI · {n} night{'s need' if n > 1 else ' needs'} a look"
    lines = [c.line for c in new]
    # othor.ai's operators have the runbook; a self-hosted install has System Health and its logs
    check = (
        "docs/runbook.md, “A night failed or ran late”"
        if settings.edition in ("hosted", "dev")
        else "System Health on Home, then the worker's log (docker compose logs worker)"
    )
    text = (
        "Nightly runs that need a look (company time):\n\n"
        + "\n".join(lines)
        + f"\n\nWhat to check: {check}.\n"
    )
    html = (
        "<p>Nightly runs that need a look (company time):</p>"
        + "".join(f"<p>{escape(line)}</p>" for line in lines)
        + f"<p>What to check: {escape(check)}.</p>"
    )
    try:
        relay.send(  # type: ignore[attr-defined]
            Message(
                to=settings.ops_email,
                subject=subject,
                html=html,
                text=text,
                sender=sender(settings, None),
            )
        )
    except Exception as e:  # logged by type only; the next tick tries again
        log.warning("ops alert not sent", extra={"error": type(e).__name__})
        return []
    with sf() as s:
        for c in new:
            record_platform_event(
                s,
                actor="system",
                action=ACTION,
                object_type="company",
                object_id=c.key,
                after={"company_id": str(c.company_id), "kind": c.kind},
                at=now,
            )
        s.commit()
    log.info("ops alert sent", extra={"cases": n})
    return lines
