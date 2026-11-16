# SPDX-License-Identifier: Apache-2.0
"""Owner emails (docs/09): 8c run failed / source not read / run didn't finish, 8d Ontology
waiting, 8e weekly digest. One job per alert, queued with a dedupe key (once per run, per source
per day, once for 8d, once per Sunday for 8e); the delivery row's `ref` keeps a retried job from
sending twice.

payload: {"kind": "run_failed", "run_id"} · {"kind": "source_stale", "run_id", "source",
"reason"} · {"kind": "run_late", "date"} · {"kind": "ontology_waiting"} · {"kind": "digest",
"date"}
"""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.briefs.models import BriefIssue
from othor.connectors.models import Source
from othor.core.events import SYSTEM_ACTOR, record_event
from othor.core.localtime import local_date
from othor.core.models import Company
from othor.engine.models import Finding, Run, RunStep
from othor.jobs.models import Job
from othor.jobs.runner import WorkerContext
from othor.mail import alerts
from othor.mail.delivery import owner
from othor.mail.models import Delivery
from othor.mail.relay import Bounced, MailSendError, Message


def _source_id(s: Session, name: str) -> str | None:
    sid = s.scalars(sa.select(Source.id).where(Source.name == name)).first()
    return str(sid) if sid else None


def _run_failed(
    s: Session, ctx: WorkerContext, c: Company, p: dict[str, Any], to: str
) -> Message | None:
    run = s.scalars(sa.select(Run).where(Run.id == uuid.UUID(p["run_id"]))).one()
    steps = list(
        s.scalars(sa.select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.ordinal))
    )
    bad = next((x for x in steps if x.status == "failed"), None)
    name = bad.name if bad else ""
    src = name.split(":", 1)[1] if name.startswith("read:") else ""
    reads = [x for x in steps if x.name.startswith("read:") and x is not bad]
    return alerts.run_failed(
        ctx.settings,
        c,
        alerts.FailedRun(
            number=run.number,
            run_date=run.run_date,
            ended_at=(bad.ended_at if bad and bad.ended_at else run.ended_at),
            step=name,
            error=(bad.error or "") if bad else "",
            source_id=_source_id(s, src) if src else None,
            others_ok=bool(reads) and all(x.status == "done" for x in reads),
        ),
        to=to,
        brief_time=c.brief_time,
    )


def _source_stale(
    s: Session, ctx: WorkerContext, c: Company, p: dict[str, Any], to: str
) -> Message:
    run = s.scalars(sa.select(Run).where(Run.id == uuid.UUID(p["run_id"]))).one()
    return alerts.source_stale(
        ctx.settings,
        c,
        to=to,
        source=p["source"],
        source_id=_source_id(s, p["source"]),
        reason=p.get("reason") or "",
        run_number=run.number,
        run_date=run.run_date,
        ended_at=run.ended_at,
        brief_time=c.brief_time,
    )


def _run_late(s: Session, ctx: WorkerContext, c: Company, p: dict[str, Any], to: str) -> Message:
    day = dt.date.fromisoformat(p["date"])
    run = s.scalars(
        sa.select(Run).where(Run.kind == "nightly", Run.run_date == day).order_by(Run.number.desc())
    ).first()
    return alerts.run_late(
        ctx.settings,
        c,
        to=to,
        run_number=run.number if run else None,
        run_date=day,
        now=ctx.clock(),
        brief_time=c.brief_time,
    )


def _ontology_waiting(
    s: Session, ctx: WorkerContext, c: Company, p: dict[str, Any], to: str
) -> Message | None:
    from othor.ontology.models import OntologyVersion
    from othor.setup.models import SetupDraft

    if s.scalars(sa.select(OntologyVersion.id)).first():
        return None  # confirmed in the meantime
    drafts = list(
        s.scalars(
            sa.select(SetupDraft)
            .where(SetupDraft.kind == "metric", SetupDraft.state == "needs_confirmation")
            .order_by(SetupDraft.position)
        )
    )
    if not drafts:
        return None
    rows = [
        {
            "name": str(d.body.get("name") or d.key),
            "note": "needs confirmation"
            if d.confidence != "conflict"
            else "two answers to pick from",
        }
        for d in drafts[:6]
    ]
    first = min(d.created_at for d in drafts)
    return alerts.ontology_waiting(
        ctx.settings, c, to=to, rows=rows, total=len(drafts), drafted_on=local_date(c, first)
    )


def _digest(
    s: Session, ctx: WorkerContext, c: Company, p: dict[str, Any], to: str
) -> Message | None:
    last = dt.date.fromisoformat(p["date"])
    first = last - timedelta(days=6)
    runs = list(
        s.scalars(
            sa.select(Run)
            .where(
                Run.kind == "nightly",
                Run.status.in_(("done", "partial")),
                Run.run_date >= first,
                Run.run_date <= last,
            )
            .order_by(Run.number)
        )
    )
    if not runs:
        return None
    ids = [r.id for r in runs]
    tests = sum(int(((r.summary or {}).get("sweep") or {}).get("tests") or 0) for r in runs)
    found = list(s.scalars(sa.select(Finding).where(Finding.run_id.in_(ids))))
    fired = {f.fingerprint for f in found}
    in_brief: set[str] = set()
    for i in s.scalars(sa.select(BriefIssue).where(BriefIssue.run_id.in_(ids))):
        in_brief |= {x["fingerprint"] for x in i.body.get("items") or []}
    latest = [f for f in found if f.run_id == runs[-1].id and f.status == "open"]
    open_items = [f for f in latest if f.fingerprint in in_brief]
    newest: dict[str, Finding] = {}
    for f in found:  # the latest row of each finding that never reached a brief
        if f.fingerprint not in in_brief:
            newest[f.fingerprint] = f
    outside = [
        (
            ((f.body or {}).get("text") or {}).get("title") or f.metric,
            ((f.body or {}).get("text") or {}).get("money") or f.detector.replace("_", " "),
        )
        for f in newest.values()
    ]
    return alerts.weekly_digest(
        ctx.settings,
        c,
        to=to,
        first=first,
        last=last,
        stats={
            "tests": tests,
            "fired": len(fired),
            "in_brief": len(fired & in_brief),
            "open": len(latest),
        },
        open_items=alerts.digest_rows(open_items[:6]),
        not_in_brief=outside[:6],
    )


BUILD = {
    "run_failed": _run_failed,
    "source_stale": _source_stale,
    "run_late": _run_late,
    "ontology_waiting": _ontology_waiting,
    "digest": _digest,
}


def run(ctx: WorkerContext, job: Job) -> None:
    kind = job.payload["kind"]
    ref = job.dedupe_key or f"alert:{kind}:{job.id}"
    now = ctx.clock()
    with ctx.sf() as s:
        if s.scalars(
            sa.select(Delivery.id).where(Delivery.ref == ref, Delivery.status == "sent")
        ).first():
            return
        c = s.scalars(sa.select(Company).where(Company.id == job.company_id)).one()
        who = owner(s)
        if who is None:
            return
        msg = BUILD[kind](s, ctx, c, job.payload, who[0])
        if msg is None:
            return
        row = s.scalars(sa.select(Delivery).where(Delivery.ref == ref)).first()
        if row is None:
            row = Delivery(kind="digest" if kind == "digest" else "alert", ref=ref, created_at=now)
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
            action=f"mail.{kind}",
            object_type="company",
            object_id=c.id,
            after={"status": row.status, "ref": ref},
            at=now,
        )
        s.commit()
        if row.status == "failed":
            raise MailSendError("owner alert not sent")
