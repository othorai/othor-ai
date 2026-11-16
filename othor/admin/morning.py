# SPDX-License-Identifier: Apache-2.0
"""The morning check (M15.9, the seven nights after a release; docs/plans/M11b.md "seven nights
watched"): for each live company, last night's run sealed, it replays byte for byte, the ledger
holds, each brief due this morning reached its recipients, and the backup is fresh. Read-only;
one PASS / FAIL / NOTE line per check. Server errors and alarms live in CloudWatch, so the check
says where to look rather than guessing. Run on the host:
`docker compose exec web othor admin morning`."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core.localtime import local_now
from othor.core.models import Company
from othor.core.tenancy import tenant_scope

BACKUP_MARKER = Path("ops") / "backup.json"  # written by deploy/aws/host/backup.sh
BACKUP_MAX_AGE = dt.timedelta(hours=26)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool | None  # None: a note, not a pass or a fail
    detail: str

    @property
    def mark(self) -> str:
        return {True: "PASS", False: "FAIL", None: "NOTE"}[self.ok]


def _aware(t: dt.datetime | None) -> dt.datetime | None:
    return None if t is None else (t if t.tzinfo else t.replace(tzinfo=dt.UTC))


def run_check(s: Session, company: Company, today: dt.date) -> tuple[Check, Any]:
    from othor.engine.models import Run

    run = s.scalars(
        sa.select(Run)
        .where(Run.kind == "nightly", Run.run_date == today)
        .order_by(Run.number.desc())
        .limit(1)
    ).first()
    if run is None:
        return Check("Run sealed", False, f"no nightly run for {today.isoformat()}"), None
    if run.status not in ("done", "partial"):
        return Check("Run sealed", False, f"run #{run.number:04d} is {run.status}"), run
    ended = _aware(run.ended_at)
    at = local_now(company, ended).strftime("%H:%M") if ended else "?"
    return Check("Run sealed", True, f"run #{run.number:04d} {run.status} at {at}"), run


def delivery_check(s: Session, company: Company, run: Any, now: dt.datetime) -> Check:
    """Each brief due this morning (its schedule, its time reached) sent to every active
    recipient, with the provider's message id; none failed or bounced."""
    from othor.briefs import service
    from othor.briefs.models import BriefIssue, Recipient
    from othor.briefs.schedule import sends_on
    from othor.mail.models import Delivery

    if not company.email_delivery:
        return Check("Briefs delivered", None, "email delivery is off for this company")
    local = local_now(company, now)
    due, waiting, problems, sent = 0, 0, [], 0
    for b in service.active(s):
        v = service.current(s, b)
        body = v.body if v else {}
        if not sends_on(body, local.date()):
            continue
        hh, mm = (int(x) for x in str(body.get("time") or company.brief_time).split(":"))
        if (local.hour, local.minute) < (hh, mm):
            waiting += 1
            continue
        people = int(
            s.scalar(
                sa.select(sa.func.count())
                .select_from(Recipient)
                .where(Recipient.brief_id == b.id, Recipient.status == "active")
            )
            or 0
        )
        if not people:
            continue
        due += 1
        issue = s.scalars(
            sa.select(BriefIssue).where(BriefIssue.brief_id == b.id, BriefIssue.run_id == run.id)
        ).first()
        if issue is None:
            problems.append(f"{b.name}: no issue")
            continue
        rows = s.scalars(
            sa.select(Delivery).where(Delivery.kind == "brief", Delivery.brief_issue_id == issue.id)
        ).all()
        good = [r for r in rows if r.status == "sent" and r.provider_id]
        bad = [r for r in rows if r.status in ("failed", "bounced")]
        sent += len(good)
        if bad:
            problems.append(f"{b.name}: {len(bad)} failed or bounced")
        elif len(good) < people:
            problems.append(f"{b.name}: {len(good)} of {people} sent")
    if problems:
        return Check("Briefs delivered", False, "; ".join(problems))
    if not due:
        note = f"{waiting} not due yet" if waiting else "none due this morning"
        return Check("Briefs delivered", None, note)
    tail = f"; {waiting} not due yet" if waiting else ""
    return Check(
        "Briefs delivered", True, f"{due} brief(s), {sent} email(s) with message ids{tail}"
    )


def backup_check(data_dir: Path, now: dt.datetime) -> Check:
    path = data_dir / BACKUP_MARKER
    if not path.exists():
        return Check("Backup", None, f"no marker at {path} (check S3 and the backup alarm)")
    try:
        doc = json.loads(path.read_text())
        at = dt.datetime.fromisoformat(str(doc["at"]).replace("Z", "+00:00"))
    except (ValueError, KeyError):
        return Check("Backup", False, f"unreadable marker at {path}")
    age = now - _aware(at)  # type: ignore[operator]
    hours = int(age.total_seconds() // 3600)
    if age > BACKUP_MAX_AGE:
        return Check("Backup", False, f"the last backup is {hours} hours old")
    return Check("Backup", True, f"{hours} hours old · {doc.get('key', '')}".rstrip(" ·"))


def company_checks(
    s: Session,
    company: Company,
    now: dt.datetime,
    *,
    replay: Callable[[Any], bool],
    ledger: Callable[[Session, Any], tuple[bool, str]],
) -> list[Check]:
    today = local_now(company, now).date()
    with tenant_scope(company.id):
        sealed, run = run_check(s, company, today)
        out = [sealed]
        if run is not None and sealed.ok:
            same = replay(run)
            out.append(Check("Replay", same, "same bytes" if same else "replay differs"))
        else:
            out.append(Check("Replay", None, "no sealed run to replay"))
        ok, why = ledger(s, company.id)
        out.append(Check("Ledger", ok, why))
        if run is not None and sealed.ok:
            out.append(delivery_check(s, company, run, now))
        else:
            out.append(Check("Briefs delivered", None, "no sealed run"))
    return out


def report(
    s: Session,
    data_dir: Path,
    now: dt.datetime,
    *,
    slug: str | None = None,
    replay: Callable[[Any], bool],
    ledger: Callable[[Session, Any], tuple[bool, str]],
) -> tuple[list[tuple[str, list[Check]]], list[Check]]:
    """(per company: its checks; the instance's checks). Paused and deleted companies are left
    out; so are companies with no confirmed Ontology yet (no nightly run to check)."""
    from othor.core.tenancy import system_scope
    from othor.ontology.models import OntologyVersion

    with system_scope("morning check: which companies run nightly"):
        q = sa.select(Company).where(Company.deleted_at.is_(None), Company.paused.is_(False))
        if slug:
            q = q.where(Company.slug == slug)
        companies = s.scalars(q.order_by(Company.slug)).all()
        live = set(s.scalars(sa.select(OntologyVersion.company_id).distinct()).all())
    rows = [
        (c.slug, company_checks(s, c, now, replay=replay, ledger=ledger))
        for c in companies
        if c.id in live
    ]
    instance = [
        backup_check(data_dir, now),
        Check("Server errors", None, "look in CloudWatch: the web log group, status 500 and up"),
        Check("Alarms", None, "look in CloudWatch: every alarm should read OK"),
    ]
    return rows, instance


def failed(rows: list[tuple[str, list[Check]]], instance: list[Check]) -> bool:
    return any(c.ok is False for _, cs in rows for c in cs) or any(c.ok is False for c in instance)
