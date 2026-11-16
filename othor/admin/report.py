# SPDX-License-Identifier: Apache-2.0
"""Operator reports across every company, for the person who runs the instance.

Four reports, each a list of plain dicts (one per company, plus extras), so the CLI can print a
table or JSON:

- companies: sign-ups, owner, setup step, sources, confirmed metrics, last sign-in;
- runs: last run per company, failed runs and steps in the window, the job queue;
- delivery: emails by status, brief issues, read-only link opens, bounced recipients;
- usage: model tokens (per company and prompt), queries against the customer's database, storage.

Read-only: nothing here writes a row. It reads only Othor AI's own metadata (never a customer
source), and prints no recipient addresses, model inputs or SQL.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.ai.models import ModelCall
from othor.briefs.models import BriefIssue, Recipient
from othor.connectors.models import RunQuery, Source
from othor.core.auth import last_seen_by_company
from othor.core.config import Settings
from othor.core.models import AppUser, Company, Membership, SetupState
from othor.core.tenancy import system_scope
from othor.engine.models import Run, RunStep
from othor.jobs.models import Job
from othor.mail.models import Delivery, LinkToken
from othor.ontology.models import OntologyRecord

Row = dict[str, Any]


def _aware(t: dt.datetime | None) -> dt.datetime | None:
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def _companies(s: Session, slug: str | None) -> list[Company]:
    q = sa.select(Company).order_by(Company.created_at, Company.slug)
    if slug:
        q = q.where(Company.slug == slug)
    return list(s.scalars(q))


def _by_company(rows: Any) -> dict[uuid.UUID, Any]:
    return {r[0]: r[1] if len(r) == 2 else tuple(r[1:]) for r in rows}


# --- companies --------------------------------------------------------------------------------


def companies(s: Session, *, slug: str | None = None) -> list[Row]:
    with system_scope("admin report: companies"):
        cs = _companies(s, slug)
        owners = defaultdict(list)
        for cid, email in s.execute(
            sa.select(Membership.company_id, AppUser.email)
            .join(AppUser, AppUser.id == Membership.user_id)
            .where(Membership.role == "owner")
            .order_by(AppUser.email)
        ):
            owners[cid].append(email)
        step = _by_company(s.execute(sa.select(SetupState.company_id, SetupState.current_step)))
        src_total = _by_company(
            s.execute(
                sa.select(Source.company_id, sa.func.count())
                .where(Source.deleted_at.is_(None))
                .group_by(Source.company_id)
            )
        )
        src_connected = _by_company(
            s.execute(
                sa.select(Source.company_id, sa.func.count())
                .where(Source.deleted_at.is_(None), Source.connected_at.is_not(None))
                .where(Source.disconnected_at.is_(None))
                .group_by(Source.company_id)
            )
        )
        metrics = _by_company(
            s.execute(
                sa.select(OntologyRecord.company_id, sa.func.count())
                .where(OntologyRecord.type == "metric", OntologyRecord.status == "active")
                .group_by(OntologyRecord.company_id)
            )
        )
        seen = _by_company(last_seen_by_company(s))
    return [
        {
            "slug": c.slug,
            "name": c.name,
            "signed_up": _aware(c.created_at),
            "owner": ", ".join(owners.get(c.id, [])),
            "setup_step": step.get(c.id),
            "sources_connected": src_connected.get(c.id, 0),
            "sources": src_total.get(c.id, 0),
            "metrics_confirmed": metrics.get(c.id, 0),
            "last_sign_in": _aware(seen.get(c.id)),
            "paused": c.paused,
            "deleted": c.deleted_at is not None,
        }
        for c in cs
    ]


# --- runs -------------------------------------------------------------------------------------


def runs(s: Session, *, now: dt.datetime, days: int, slug: str | None = None) -> Row:
    since = now - dt.timedelta(days=days)
    with system_scope("admin report: runs"):
        cs = _companies(s, slug)
        ids = [c.id for c in cs]
        per: list[Row] = []
        for c in cs:
            last = s.scalars(
                sa.select(Run)
                .where(Run.company_id == c.id)
                .order_by(Run.started_at.desc(), Run.number.desc())
                .limit(1)
            ).first()
            counts = dict(
                s.execute(
                    sa.select(Run.status, sa.func.count())
                    .where(Run.company_id == c.id, Run.started_at >= since)
                    .group_by(Run.status)
                ).all()
            )
            started, ended = (
                (_aware(last.started_at), _aware(last.ended_at)) if last else (None,) * 2
            )
            per.append(
                {
                    "slug": c.slug,
                    "paused": c.paused,
                    "last_run": f"#{last.number:04d}" if last else None,
                    "last_kind": last.kind if last else None,
                    "last_run_date": last.run_date if last else None,
                    "last_status": last.status if last else None,
                    "last_started": started,
                    "last_seconds": int((ended - started).total_seconds())
                    if started and ended
                    else None,
                    "runs_in_window": sum(counts.values()),
                    "failed_in_window": counts.get("failed", 0),
                    "partial_in_window": counts.get("partial", 0),
                }
            )
        slugs = {c.id: c.slug for c in cs}
        failed_steps = [
            {
                "slug": slugs[cid],
                "run": f"#{number:04d}",
                "step": name,
                "error": error or "",
                "at": _aware(at),
            }
            for cid, number, name, error, at in s.execute(
                sa.select(Run.company_id, Run.number, RunStep.name, RunStep.error, RunStep.ended_at)
                .join(Run, Run.id == RunStep.run_id)
                .where(RunStep.status == "failed", Run.started_at >= since)
                .where(Run.company_id.in_(ids))
                .order_by(Run.started_at.desc(), RunStep.ordinal)
            )
        ]
        failed_jobs = [
            {
                "slug": slugs[cid],
                "kind": kind,
                "attempts": attempts,
                "error": error or "",
                "at": _aware(at),
            }
            for cid, kind, attempts, error, at in s.execute(
                sa.select(Job.company_id, Job.kind, Job.attempts, Job.error, Job.finished_at)
                .where(Job.status == "failed", Job.company_id.in_(ids))
                .where(sa.or_(Job.finished_at >= since, Job.finished_at.is_(None)))
                .order_by(Job.finished_at.desc())
            )
        ]
        queue_q = sa.select(Job).where(Job.status.in_(("queued", "running")))
        if slug:
            queue_q = queue_q.where(Job.company_id.in_(ids))
        queue = list(s.scalars(queue_q))
        last_done_q = sa.select(sa.func.max(Job.finished_at)).where(Job.status == "done")
        last_done = _aware(s.scalar(last_done_q))
    due = [j for j in queue if j.status == "queued" and (_aware(j.run_after) or now) <= now]
    oldest = min((j.created_at for j in due), default=None)
    oldest = _aware(oldest)
    return {
        "companies": per,
        "failed_steps": failed_steps,
        "failed_jobs": failed_jobs,
        "queue": {
            "queued": sum(1 for j in queue if j.status == "queued"),
            "due_now": len(due),
            "running": sum(1 for j in queue if j.status == "running"),
            "oldest_due_minutes": int((now - oldest).total_seconds() // 60) if oldest else None,
            "last_job_finished": last_done,
        },
    }


# --- delivery ---------------------------------------------------------------------------------


def delivery(s: Session, *, now: dt.datetime, days: int, slug: str | None = None) -> Row:
    since = now - dt.timedelta(days=days)
    with system_scope("admin report: delivery"):
        cs = _companies(s, slug)
        ids = [c.id for c in cs]
        by_status: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
        for cid, status, n in s.execute(
            sa.select(Delivery.company_id, Delivery.status, sa.func.count())
            .where(Delivery.created_at >= since, Delivery.company_id.in_(ids))
            .group_by(Delivery.company_id, Delivery.status)
        ):
            by_status[cid][status] = n
        issues: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
        for cid, status, n in s.execute(
            sa.select(BriefIssue.company_id, BriefIssue.status, sa.func.count())
            .where(BriefIssue.created_at >= since, BriefIssue.company_id.in_(ids))
            .group_by(BriefIssue.company_id, BriefIssue.status)
        ):
            issues[cid][status] = n
        opens = _by_company(
            s.execute(
                sa.select(LinkToken.company_id, sa.func.count())
                .where(LinkToken.purpose == "open", LinkToken.used_at >= since)
                .where(LinkToken.company_id.in_(ids))
                .group_by(LinkToken.company_id)
            )
        )
        bounced = _by_company(
            s.execute(
                sa.select(Recipient.company_id, sa.func.count())
                .where(Recipient.bounced_at.is_not(None), Recipient.company_id.in_(ids))
                .group_by(Recipient.company_id)
            )
        )
        slugs = {c.id: c.slug for c in cs}
        failures = [
            {
                "slug": slugs[cid],
                "kind": kind,
                "status": status,
                "error": error or "",
                "at": _aware(at),
            }
            for cid, kind, status, error, at in s.execute(
                sa.select(
                    Delivery.company_id,
                    Delivery.kind,
                    Delivery.status,
                    Delivery.error,
                    Delivery.created_at,
                )
                .where(Delivery.status.in_(("failed", "bounced")))
                .where(Delivery.created_at >= since, Delivery.company_id.in_(ids))
                .order_by(Delivery.created_at.desc())
            )
        ]
    per = []
    for c in cs:
        st = by_status.get(c.id, {})
        per.append(
            {
                "slug": c.slug,
                "email_delivery": c.email_delivery,
                "brief_time": c.brief_time,
                "issues_ready": issues.get(c.id, {}).get("ready", 0),
                "issues_failed": issues.get(c.id, {}).get("failed", 0),
                "sent": st.get("sent", 0),
                "queued": st.get("queued", 0),
                "failed": st.get("failed", 0),
                "bounced": st.get("bounced", 0),
                "link_opens": opens.get(c.id, 0),
                "bounced_recipients": bounced.get(c.id, 0),
            }
        )
    return {"companies": per, "failures": failures}


# --- usage ------------------------------------------------------------------------------------


def _dir_bytes(path: Path) -> int | None:
    """Bytes under the company's data folder; None when this host has no such folder (the
    report reads the data volume of the machine it runs on)."""
    if not path.is_dir():
        return None
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def usage(
    s: Session, *, now: dt.datetime, days: int, settings: Settings, slug: str | None = None
) -> Row:
    since = now - dt.timedelta(days=days)
    with system_scope("admin report: usage"):
        cs = _companies(s, slug)
        ids = [c.id for c in cs]
        tokens = _by_company(
            s.execute(
                sa.select(
                    ModelCall.company_id,
                    sa.func.count(),
                    sa.func.coalesce(sa.func.sum(ModelCall.tokens_in), 0),
                    sa.func.coalesce(sa.func.sum(ModelCall.tokens_out), 0),
                )
                .where(ModelCall.created_at >= since, ModelCall.company_id.in_(ids))
                .group_by(ModelCall.company_id)
            )
        )
        slugs = {c.id: c.slug for c in cs}
        prompts = [
            {
                "slug": slugs[cid],
                "prompt": f"{pid}@{ver}",
                "calls": n,
                "tokens_in": ti,
                "tokens_out": to,
                "cached": cached or 0,
            }
            for cid, pid, ver, n, ti, to, cached in s.execute(
                sa.select(
                    ModelCall.company_id,
                    ModelCall.prompt_id,
                    ModelCall.prompt_version,
                    sa.func.count(),
                    sa.func.coalesce(sa.func.sum(ModelCall.tokens_in), 0),
                    sa.func.coalesce(sa.func.sum(ModelCall.tokens_out), 0),
                    sa.func.sum(sa.case((ModelCall.cached.is_(True), 1), else_=0)),
                )
                .where(ModelCall.created_at >= since, ModelCall.company_id.in_(ids))
                .group_by(ModelCall.company_id, ModelCall.prompt_id, ModelCall.prompt_version)
                .order_by(ModelCall.company_id, ModelCall.prompt_id, ModelCall.prompt_version)
            )
        ]
        queries = _by_company(
            s.execute(
                sa.select(
                    RunQuery.company_id,
                    sa.func.count(),
                    sa.func.coalesce(sa.func.sum(RunQuery.rows), 0),
                    sa.func.coalesce(sa.func.max(RunQuery.duration_ms), 0),
                )
                .where(RunQuery.created_at >= since, RunQuery.company_id.in_(ids))
                .group_by(RunQuery.company_id)
            )
        )
    per = []
    for c in cs:
        calls, t_in, t_out = tokens.get(c.id, (0, 0, 0))
        q, rows, longest = queries.get(c.id, (0, 0, 0))
        per.append(
            {
                "slug": c.slug,
                "model_calls": calls,
                "tokens_in": int(t_in),
                "tokens_out": int(t_out),
                "queries": q,
                "rows_back": int(rows),
                "longest_query_ms": int(longest),
                "storage_bytes": _dir_bytes(settings.company_dir(c.id)),
            }
        )
    prompts.sort(key=lambda r: (r["slug"], r["prompt"]))
    return {"companies": per, "prompts": prompts}


def connector_requests(s: Session) -> list[Row]:
    """M15.4: the connectors owners asked for, most requested first (the same system named the
    same way, case and spaces aside), with how many companies asked and when last."""
    from othor.connectors.models import ConnectorRequest

    with system_scope("admin report: connector requests"):
        rows = s.scalars(sa.select(ConnectorRequest).order_by(ConnectorRequest.created_at)).all()
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = r.system.casefold()
        g = groups.setdefault(
            key, {"system": r.system, "requests": 0, "companies": set(), "last": None}
        )
        g["requests"] += 1
        g["companies"].add(r.company_id)
        g["last"] = r.created_at
    out = [
        {**g, "companies": len(g["companies"]), "last": _aware(g["last"])} for g in groups.values()
    ]
    return sorted(out, key=lambda g: (-g["requests"], -g["companies"], g["system"].casefold()))
