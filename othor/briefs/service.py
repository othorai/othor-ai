# SPDX-License-Identifier: Apache-2.0
"""Brief definitions: the default brief, versions, recipients (M8). Every change is an event.

- The default "Leadership" brief is created by the first run that has a confirmed Ontology
  (decided 5 Nov 2026): whole company, the headline numbers, Ask today to the top item's owner,
  the question from the top goal by template, the owner as the only recipient. No model.
- A change saves a new version and applies from the next run (the run reads the active
  versions into its context file).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.briefs.assemble import default_version
from othor.briefs.models import Brief, BriefIssue, BriefVersion, Recipient
from othor.core.events import SYSTEM_ACTOR, record_event


def _owner(s: Session) -> tuple[str, str] | None:
    from othor.core.models import AppUser, Membership

    row = s.execute(
        sa.select(AppUser.email, AppUser.name)
        .join(Membership, Membership.user_id == AppUser.id)
        .where(Membership.role == "owner")
        .order_by(Membership.created_at)
    ).first()
    return (str(row[0]), str(row[1] or "")) if row else None


def ensure_default(s: Session, spec: dict[str, Any]) -> Brief | None:
    """The company's first brief, once, from its compiled spec."""
    if s.scalar(sa.select(sa.func.count()).select_from(Brief)):
        return None
    body = default_version(spec)
    b = Brief(name=body["name"], is_default=True, position=0, current_version=1)
    s.add(b)
    s.flush()
    s.add(
        BriefVersion(brief_id=b.id, number=1, body=body, created_by=SYSTEM_ACTOR, reason="default")
    )
    owner = _owner(s)
    if owner:
        s.add(
            Recipient(
                brief_id=b.id, email=owner[0], name=owner[1], role="Owner", added_by=SYSTEM_ACTOR
            )
        )
    record_event(
        s,
        actor=SYSTEM_ACTOR,
        action="brief.created",
        object_type="brief",
        object_id=b.id,
        after={"name": b.name, "version": 1, "default": True},
    )
    return b


def save_version(
    s: Session, brief: Brief, body: dict[str, Any], *, actor: str, reason: str = ""
) -> BriefVersion:
    """A new version of a brief; it applies from the next run."""
    before = current(s, brief)
    number = brief.current_version + 1
    diff = {
        k: {"before": (before.body if before else {}).get(k), "after": v}
        for k, v in body.items()
        if (before.body if before else {}).get(k) != v
    }
    v = BriefVersion(
        brief_id=brief.id, number=number, body=body, diff=diff, created_by=actor, reason=reason
    )
    s.add(v)
    brief.current_version = number
    if body.get("name"):
        brief.name = str(body["name"])
    record_event(
        s,
        actor=actor,
        action="brief.changed",
        object_type="brief",
        object_id=brief.id,
        before={"version": number - 1},
        after={"version": number, "changed": sorted(diff)},
    )
    return v


def create(s: Session, body: dict[str, Any], *, actor: str, reason: str = "") -> Brief:
    n = s.scalar(sa.select(sa.func.count()).select_from(Brief)) or 0
    b = Brief(name=str(body.get("name") or "Brief"), position=int(n), current_version=1)
    s.add(b)
    s.flush()
    s.add(BriefVersion(brief_id=b.id, number=1, body=body, created_by=actor, reason=reason))
    owner = _owner(s)
    if owner:
        s.add(Recipient(brief_id=b.id, email=owner[0], name=owner[1], role="Owner", added_by=actor))
    record_event(
        s,
        actor=actor,
        action="brief.created",
        object_type="brief",
        object_id=b.id,
        after={"name": b.name, "version": 1},
    )
    return b


def current(s: Session, brief: Brief) -> BriefVersion | None:
    return s.scalars(
        sa.select(BriefVersion).where(
            BriefVersion.brief_id == brief.id, BriefVersion.number == brief.current_version
        )
    ).one_or_none()


def active(s: Session) -> list[Brief]:
    return list(
        s.scalars(
            sa.select(Brief)
            .where(Brief.status == "active", Brief.deleted_at.is_(None))
            .order_by(Brief.position, Brief.created_at)
        )
    )


def snapshot(
    s: Session, before: dt.date | None = None, day: dt.date | None = None
) -> list[dict[str, Any]]:
    """The active briefs' current versions as run inputs (plain, sorted), each with the items
    of its latest issue (rank and score), so tonight's order is stable (decided 6 Nov 2026).
    `before`: only issues of earlier days (a Run again now ranks against yesterday's, v0.11).
    `day` (M15.7c): the run's date; a Weekly or Monthly brief is left out on other days, and on
    its day carries its period: the findings open at its previous issue."""
    from othor.briefs import schedule as sch

    out = []
    for b in active(s):
        v = current(s, b)
        if v is None:
            continue
        if day is not None and not sch.made_on(v.body, day):
            continue
        q = sa.select(BriefIssue).where(BriefIssue.brief_id == b.id)
        if before is not None:
            q = q.where(BriefIssue.date < before)
        last = s.scalars(
            q.order_by(BriefIssue.date.desc(), BriefIssue.created_at.desc()).limit(1)
        ).first()
        # 0.28.5: each item's day of its last change goes with it, so an item that changed three
        # days ago isn't counted from the day Sweep first saw it (assemble._changed_on)
        previous = [
            {"fingerprint": i["fingerprint"], "rank": i["rank"], "score": i.get("score")}
            | ({"since": i["since"]} if i.get("since") else {})
            for i in ((last.body.get("items") or []) if last else [])
        ]
        row = {"id": str(b.id), "number": v.number, "name": b.name, **v.body, "previous": previous}
        if b.is_default:  # it keeps the whole company even with picked numbers (scope.focus)
            row["default"] = True
        if day is not None and (p := sch.period(v.body, day, last.date if last else None)):
            row["period"] = {"from": p[0].isoformat(), "word": p[1], "open": _open_at(s, last)}
        out.append(row)
    return out


def _open_at(s: Session, issue: BriefIssue | None) -> dict[str, dict[str, Any]]:
    """The findings open at an issue's run: its money and what the brief's scope reads (metric,
    breakdown value, entities), so what closed since is counted in scope (M15.7c)."""
    from othor.engine.models import Finding

    if issue is None:
        return {}
    rows = s.scalars(
        sa.select(Finding)
        .where(Finding.run_id == issue.run_id, Finding.status == "open")
        .order_by(Finding.fingerprint)
    ).all()
    return {
        f.fingerprint: {
            "money": f.money or "0",
            "metric": f.metric,
            "breakdown": f.breakdown or "",
            "breakdown_value": f.breakdown_value,
            "entities": list((f.body or {}).get("entities") or []),
        }
        for f in rows
    }


def get(s: Session, brief_id: uuid.UUID) -> Brief | None:
    return s.scalars(sa.select(Brief).where(Brief.id == brief_id)).one_or_none()
