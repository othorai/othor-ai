# SPDX-License-Identifier: Apache-2.0
"""A new brief's first issue at creation (0.29.4, owner 20 Nov 2026): "after each new brief is
created, the user would like to see actual contents in that instead of waiting till next run".

On Create, the brief's issue is assembled from the latest sealed run's own files with the same
function the run calls (`step.preview` → `step.make`), from the brief version as the run would
read it (`service.version_row`). No run is started, no source is read, no model is called. The
issue is stored for that run's date with status `created`, so:

- the run's ledger, files and replay don't change (the issue isn't in the run's `briefs.json`);
- the issue is reproducible from the same run files plus the brief version (`remake`);
- the 06:00 delivery never sends it (`deliver_brief` skips `created`); the owner can send it
  once with Send now, through the normal delivery path.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.briefs import service, step
from othor.briefs.models import Brief, BriefIssue
from othor.core.config import Settings
from othor.core.events import record_event
from othor.engine.ledger import sha
from othor.engine.models import DETECTING_KINDS, Run
from othor.engine.store import RunFiles
from othor.ontology.compiler.api import canonical
from othor.ontology.models import Spec

STATUS = "created"


def last_sealed(s: Session) -> Run | None:
    """The latest run that sealed with findings (the brief list and previews read the same)."""
    return s.scalars(
        sa.select(Run)
        .where(Run.status.in_(("done", "partial")), Run.kind.in_(DETECTING_KINDS))
        .order_by(Run.number.desc())
        .limit(1)
    ).first()


def remake(
    s: Session, settings: Settings, brief: Brief, run: Run, version: int | None = None
) -> dict[str, Any]:
    """The issue the run's files make for `brief` at `version` (its current one by default):
    what the creation issue stores, byte for byte. {} when the run's files aren't there."""
    from othor.briefs.models import BriefVersion

    files = RunFiles(settings.runs_dir(run.company_id), run.id)
    if not files.outputs_json.exists():
        return {}
    number = brief.current_version if version is None else version
    v = s.scalars(
        sa.select(BriefVersion).where(
            BriefVersion.brief_id == brief.id, BriefVersion.number == number
        )
    ).one_or_none()
    spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one_or_none()
    if v is None or spec is None:
        return {}
    # no earlier issue: a new brief has none, and a remake reads the row as it was at creation
    row = {**service.version_row(s, brief, v, day=run.run_date), "previous": []}
    if "period" in row:
        row["period"] = {**row["period"], "open": {}}
    return step.preview(files, dict(spec.compiled), run.run_date, row)


def at_creation(
    s: Session, settings: Settings, brief: Brief, *, actor: str, now: dt.datetime
) -> BriefIssue | None:
    """Store `brief`'s first issue from the latest sealed run; None before any run (the brief
    then says when its first issue comes, #96). Inside the caller's tenant scope and
    transaction; an event either way it is made."""
    run = last_sealed(s)
    if run is None:
        return None
    have = s.scalars(
        sa.select(BriefIssue).where(BriefIssue.brief_id == brief.id, BriefIssue.run_id == run.id)
    ).first()
    if have is not None:
        return have
    body = remake(s, settings, brief, run)
    if not body:
        return None
    issue = BriefIssue(
        brief_id=brief.id,
        brief_version=brief.current_version,
        run_id=run.id,
        date=run.run_date,
        status=STATUS,
        title=body["title"],
        quiet=bool(body["quiet"]),
        body=body,
        issue_hash=sha(canonical(body).encode()),
        prose={"source": "template"},
    )
    s.add(issue)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="brief.issue_made_at_creation",
        object_type="brief_issue",
        object_id=issue.id,
        after={
            "brief": str(brief.id),
            "version": brief.current_version,
            "run": str(run.id),
            "date": run.run_date.isoformat(),
            "hash": issue.issue_hash,
        },
        at=now,
    )
    return issue


def note(issue: BriefIssue | None, body: dict[str, Any], latest: bool) -> str | None:
    """ "From last night's results · first scheduled issue tomorrow 6:00 AM" on the brief while
    the issue it shows is its creation issue (and the day's newest)."""
    from othor.briefs import schedule as sch
    from othor.core import fmt

    if issue is None or issue.status != STATUS or not latest:
        return None
    nxt = sch.next_on(body, issue.date + dt.timedelta(days=1))
    day = "tomorrow" if nxt == issue.date + dt.timedelta(days=1) else fmt.date_short(nxt)
    return (
        f"From last night’s results · first scheduled issue {day} "
        f"{fmt.clock(body.get('time', '06:00'))}"
    )
