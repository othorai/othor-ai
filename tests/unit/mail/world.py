# SPDX-License-Identifier: Apache-2.0
"""A small delivery world on the metadata DB: the company's spec, a nightly run per date, the
Leadership brief with its issues (the real 1 Nov issue body from the sample company, saved in
tests/fixtures/mail/), and recipients. No sources, no engine: delivery reads stored rows only."""

from __future__ import annotations

import copy
import datetime as dt
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.briefs.models import Brief, BriefIssue, BriefVersion, Recipient
from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from othor.engine.models import Run, RunStep
from othor.ontology.models import Spec

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "mail"
UTC = dt.UTC


def issue_1nov() -> dict[str, Any]:
    return json.loads((FIXTURES / "issue_1nov.json").read_text())


def weather_1nov() -> dict[str, Any]:
    """The Leadership brief's own weather on 1 Nov (v0.12 part 1c), as the run stored it."""
    return json.loads((FIXTURES / "weather_1nov.json").read_text())


def quiet_from(body: dict[str, Any]) -> dict[str, Any]:
    """The 1 Nov issue as a quiet day (4d0/10q): no items, no Ask today; the run's own words for
    a quiet issue (othor.briefs.assemble)."""
    q = copy.deepcopy(body)
    q.update(
        quiet=True,
        items=[],
        ask=None,
        title="Nothing needs you today.",
        line=f"{body['tests']} tests found nothing above the ₹5.0 L floor in this brief’s scope.",
        situation="",
        since={"new": 0, "worse": 0, "better": 0, "open": 0, "resolved": 0},
        watching=body["watching"][:1],
    )
    return q


@dataclass
class World:
    sf: sessionmaker[Session]
    cid: uuid.UUID
    brief_id: uuid.UUID
    spec_id: uuid.UUID
    runs: dict[dt.date, uuid.UUID] = field(default_factory=dict)
    issues: dict[dt.date, uuid.UUID] = field(default_factory=dict)

    def company(self, s: Session) -> Company:
        return s.scalars(sa.select(Company).where(Company.id == self.cid)).one()

    def night(
        self,
        day: dt.date,
        body: dict[str, Any] | None = None,
        *,
        status: str = "done",
        with_issue: bool = True,
        fail_step: str | None = None,
    ) -> uuid.UUID:
        """A nightly run for `day` (2:00 AM IST) and, when sealed, the brief's issue."""
        with self.sf() as s, tenant_scope(self.cid):
            n = (s.scalar(sa.select(sa.func.max(Run.number))) or 0) + 1
            started = dt.datetime.combine(day - dt.timedelta(days=1), dt.time(20, 30), tzinfo=UTC)
            run = Run(
                number=n,
                kind="nightly",
                run_date=day,
                spec_id=self.spec_id,
                image_version="0.1.0",
                started_at=started,
                ended_at=None if status == "running" else started + dt.timedelta(minutes=4),
                status=status,
                summary={"sweep": {"tests": 362}, "stale_sources": []},
            )
            s.add(run)
            s.flush()
            for i, src in enumerate(("erp_db", "sales_dw")):
                failed = fail_step == f"read:{src}"
                s.add(
                    RunStep(
                        run_id=run.id,
                        name=f"read:{src}",
                        ordinal=i,
                        status="failed" if failed else "done",
                        started_at=started,
                        ended_at=started + dt.timedelta(minutes=3 + i),
                        error="OperationalError" if failed else None,
                    )
                )
            if with_issue and status in ("done", "partial"):
                b = copy.deepcopy(body or issue_1nov())
                b["date"] = day.isoformat()
                iss = BriefIssue(
                    brief_id=self.brief_id,
                    brief_version=1,
                    run_id=run.id,
                    date=day,
                    status="ready",
                    title=b["title"],
                    quiet=bool(b.get("quiet")),
                    body=b,
                    issue_hash="x" * 64,
                    prose={"source": "template"},
                )
                s.add(iss)
                s.flush()
                self.issues[day] = iss.id
            s.commit()
            self.runs[day] = run.id
            return run.id

    def recipient(
        self, email: str, name: str = "", role: str = "", brief_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        with self.sf() as s, tenant_scope(self.cid):
            r = Recipient(
                brief_id=brief_id or self.brief_id,
                email=email,
                name=name,
                role=role,
                added_by="test",
            )
            s.add(r)
            s.commit()
            return r.id

    def set_format(self, fmt: str) -> None:
        with self.sf() as s, tenant_scope(self.cid):
            v = s.scalars(
                sa.select(BriefVersion).where(BriefVersion.brief_id == self.brief_id)
            ).one()
            v.body = {**v.body, "email_format": fmt}
            s.commit()


def build(sf: sessionmaker[Session], cid: uuid.UUID, *, owner: bool = True) -> World:
    """The Leadership brief (v1), the owner as its first recipient (as the first run makes it)."""
    with sf() as s, tenant_scope(cid):
        spec = Spec(
            ontology_version=2,
            compiler_version="test",
            compiled={"ontology_version": 2, "metrics": {}},
            hash="s" * 64,
        )
        s.add(spec)
        b = Brief(name="Leadership", is_default=True, position=0, current_version=1)
        s.add(b)
        s.flush()
        s.add(
            BriefVersion(
                brief_id=b.id,
                number=1,
                body={
                    "name": "Leadership",
                    "question": (
                        "Are we on track for ₹60 Cr this year, and where do I need to step in?"
                    ),
                    "schedule": "daily",
                    "time": "06:00",
                    "email_format": "simple",
                },
                created_by="system",
            )
        )
        if owner:
            s.add(
                Recipient(
                    brief_id=b.id,
                    email="uk@indigothread.example",
                    name="Uday Kamath",
                    role="Owner",
                    added_by="system",
                )
            )
        s.commit()
        return World(sf, cid, b.id, spec.id)
