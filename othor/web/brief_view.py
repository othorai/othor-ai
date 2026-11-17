# SPDX-License-Identifier: Apache-2.0
"""The Morning Brief's pages (4d, 4d0, 4d1, 4h, 10j, 10l): one stored issue, as the run made it.

Every number on the page comes from the issue the run wrote (`brief_issue.body`); the page never
recomputes one. The words are the template's, or the model's rewrite when it passed the
numbers guard. "N more in Sweep" reads the same overlay as Sweep, so the counts agree within the
day. A snoozed item stays in the issue it was made in, marked; a sent brief isn't changed.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.briefs import service
from othor.briefs.models import Brief, BriefIssue
from othor.briefs.words import shown as shown_words
from othor.charts.spec import ChartSpec
from othor.core import fmt
from othor.core.config import Settings
from othor.engine.finding_charts import load as load_chart
from othor.engine.ledger import step_label
from othor.engine.models import DETECTING_KINDS, Run, RunStep
from othor.jobs.models import Job
from othor.ontology.models import Spec
from othor.web import sweep_view as sv


@dataclass
class BriefView:
    brief: Brief
    briefs: list[Brief]
    issue: BriefIssue | None
    run: Run | None
    body: dict[str, Any]
    words: dict[str, str]
    previous: dt.date | None  # the issue before this one (‹ Yesterday)
    failed: dict[str, Any] | None  # a failed run in place of today's issue (4h)
    more_in_sweep: int
    spec_version: int | None
    # the day's newest issue: a Run again now makes a newer one, the 06:00 one stays (v0.11)
    latest_of_day: bool = True


def _chart(raw: str | None) -> ChartSpec | None:
    return load_chart(raw) if raw else None


def issues(s: Session, brief: Brief) -> list[BriefIssue]:
    return list(
        s.scalars(
            sa.select(BriefIssue)
            .where(BriefIssue.brief_id == brief.id)
            .order_by(BriefIssue.date.desc(), BriefIssue.created_at.desc())
        )
    )


def card_issue(
    s: Session, brief: Brief, body: dict[str, Any], run: Run | None
) -> BriefIssue | None:
    """The issue a brief's card shows: tonight's, or for a Weekly or Monthly brief on a night it
    isn't made, its latest (M15.7c)."""
    from othor.briefs.schedule import PERIODIC, kind

    if run is None:
        return None
    got = s.scalars(
        sa.select(BriefIssue).where(BriefIssue.brief_id == brief.id, BriefIssue.run_id == run.id)
    ).first()
    if got is None and kind(body) in PERIODIC:
        got = s.scalars(
            sa.select(BriefIssue)
            .where(BriefIssue.brief_id == brief.id, BriefIssue.date <= run.run_date)
            .order_by(BriefIssue.date.desc(), BriefIssue.created_at.desc())
            .limit(1)
        ).first()
    return got


def next_line(body: dict[str, Any], run: Run) -> str:
    """'Weekly · Mondays · next Mon 23 Nov' for a card showing an earlier issue (M15.7c)."""
    from othor.briefs.schedule import label, next_on

    nxt = next_on(body, run.run_date + dt.timedelta(days=1))
    return f"{label(body)} · next {fmt.date_short(nxt)}"


def _failed_after(s: Session, issue: BriefIssue | None) -> dict[str, Any] | None:
    """The latest nightly run, if it failed after the shown issue was made (no brief today)."""
    last = s.scalars(
        sa.select(Run).where(Run.kind == "nightly").order_by(Run.number.desc()).limit(1)
    ).first()
    if last is None or last.status != "failed":
        return None
    if issue is not None and issue.date >= last.run_date:
        return None
    step = s.scalars(
        sa.select(RunStep)
        .where(RunStep.run_id == last.id, RunStep.status == "failed")
        .order_by(RunStep.ordinal)
    ).first()
    return {
        "run": last.number,
        "date": last.run_date,
        "step": step_label(step.name) if step else "",
        "error": (step.error or "") if step else "",
    }


def load(
    s: Session,
    settings: Settings,
    brief_id: uuid.UUID | None,
    date: dt.date | None,
    today: dt.date,
    issue_id: uuid.UUID | None = None,
) -> BriefView | None:
    """A brief and one of its issues: the latest, the one for `date`, or exactly `issue_id`
    (a signed link names its issue, never a date)."""
    briefs = service.active(s)
    if not briefs:
        return None
    brief = briefs[0] if brief_id is None else next((b for b in briefs if b.id == brief_id), None)
    if brief is None:
        return None
    all_ = issues(s, brief)
    if issue_id is not None:
        issue = next((i for i in all_ if i.id == issue_id), None)
        if issue is None:
            return None
        date = issue.date
    else:
        issue = all_[0] if date is None else next((i for i in all_ if i.date == date), None)
    if date is not None and issue is None:
        return None
    failed = _failed_after(s, issue) if date is None else None
    previous = None
    latest_of_day = True
    if issue is not None:
        previous = next((i.date for i in all_ if i.date < issue.date), None)
        latest_of_day = next(i for i in all_ if i.date == issue.date).id == issue.id
    run = s.scalars(sa.select(Run).where(Run.id == issue.run_id)).one() if issue else None
    body = dict(issue.body) if issue else {}
    more = int(body.get("more_in_sweep") or 0)
    version = None
    if run is not None:
        rv = sv.load(s, settings, run, today)
        if rv is not None:  # the overlay: snoozes and mutes the owner set today
            open_ = {f["fingerprint"] for f in rv.shown if f["status"] == "open"}
            mine = {i["fingerprint"] for i in body.get("items") or []}
            more = max(rv.counts["findings"] - len(mine & open_), 0)
        spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one()
        version = int(spec.compiled.get("ontology_version") or 0) or None
    return BriefView(
        brief=brief,
        briefs=briefs,
        issue=issue,
        run=run,
        body=body,
        words=shown_words(body, issue.prose if issue else None),
        previous=previous,
        failed=failed,
        more_in_sweep=more,
        spec_version=version,
        latest_of_day=latest_of_day,
    )


def _drawable(c: ChartSpec | None) -> bool:
    """A chart worth a card: bars or categories, or a line with at least two points. A new
    company's one-month spark is a single dot (#59, 14 Nov 2026)."""
    if c is None:
        return False
    if c.categories:
        return True
    return any(len(sr.points) >= 2 for sr in c.series)


def lead_chart(body: dict[str, Any] | None, *, quiet: bool = False) -> ChartSpec | None:
    """A card's visual: the top-ranked item's chart, else the first headline tile's spark; one
    with too few points to draw a line is skipped. A quiet issue (or one with no items) has no
    chart, so every quiet card shows the Quiet day mark (#64, owner 14 Nov 2026)."""
    body = body or {}
    if quiet or not body.get("items"):
        return None
    for i in body.get("items") or []:
        if i.get("chart") and _drawable(c := _chart(i["chart"])):
            return c
    for t in body.get("tiles") or []:
        if t.get("chart") and _drawable(c := _chart(t["chart"])):
            return c
    return None


def tiles(bv: BriefView) -> list[dict[str, Any]]:
    return [shown_tile(t) for t in bv.body.get("tiles") or []]


# the tile colour rule (owner, 11 Nov 2026; M15.3): text and sparkline by how far outside its band
EMPHASIS_ROLE = {"clear": "negative", "edge": "edge", "in": "muted"}


def shown_tile(t: dict[str, Any]) -> dict[str, Any]:
    """A stored tile as shown: red only for a clear move, dark with a ring just outside its band,
    grey in band (`tiles.emphasis`); the stored body is unchanged."""
    from dataclasses import replace

    from othor.briefs.tiles import emphasis

    e = emphasis(t)
    spark = _chart(t.get("chart"))
    role = EMPHASIS_ROLE.get(e, t.get("role", "neutral"))
    if e and spark is not None and spark.series:
        line = {"clear": "negative", "edge": "edge"}.get(e, "neutral")
        spark = replace(spark, series=(replace(spark.series[0], role=line), *spark.series[1:]))
    return {**t, "role": role, "emphasis": e, "spark": spark}


def items(bv: BriefView) -> list[dict[str, Any]]:
    out = []
    for i in bv.body.get("items") or []:
        out.append(
            {**i, "chart_spec": _chart(i.get("chart")), "change_tag": sv._change(i["change"])}
        )
    return out


def since_line(bv: BriefView) -> str:
    """'2 new · 1 worse · 1 open' over the items shown (4d)."""
    tags = [i["change"] for i in bv.body.get("items") or []]
    parts = []
    for word, test in (
        ("new", lambda t: t == "New"),
        ("worse", lambda t: t == "Worse"),
        ("better", lambda t: t == "Better"),
        ("open", lambda t: t.startswith("Open")),
        ("came back", lambda t: t == "Came back"),
    ):
        n = sum(1 for t in tags if test(t))
        if n:
            parts.append(f"{n} {word}")
    return " · ".join(parts)


def early(bv: BriefView) -> list[dict[str, Any]]:
    """Tiles still building history (10j)."""
    return [t for t in bv.body.get("tiles") or [] if t["comparison"]["kind"] == "history"]


def stale_banner(s: Session, bv: BriefView) -> dict[str, str] | None:
    """10l: which numbers in this issue are from a source the run couldn't read, that source, and
    since when. Drawn from the issue's own stale list, not its tiles: a stale number needn't be a
    headline tile. Names and dates come from the spec and the series' last good day."""
    keys = list(bv.body.get("stale") or [])
    if not keys or bv.run is None:
        return None
    from othor.connectors.models import Source
    from othor.engine.models import SeriesMeta

    row = s.scalars(sa.select(Spec).where(Spec.id == bv.run.spec_id)).first()
    spec = dict(row.compiled) if row else {}
    metrics = spec.get("metrics", {})
    names = [str(metrics.get(k, {}).get("name") or k) for k in keys]
    srcs = sorted({x for k in keys for x in (metrics.get(k, {}).get("sources") or [])})
    last = [
        m.last_good_at
        for m in s.scalars(sa.select(SeriesMeta).where(SeriesMeta.metric.in_(keys)))
        if m.last_good_at
    ]
    since = max(last) if last else None
    are = "is" if len(names) == 1 else "are"
    shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    lead = f"{shown} {are} from {fmt.date_short(since)}" if since else f"{shown} {are} stale"
    href = "/sources"
    if len(srcs) == 1:
        src = s.scalars(sa.select(Source).where(Source.name == srcs[0])).first()
        if src is not None:
            href = f"/sources?panel=reconnect&s={src.id}"
    return {"lead": lead, "source": ", ".join(srcs), "href": href}


def sources_line(s: Session, bv: BriefView) -> list[dict[str, Any]]:
    """Each source the run read and when (4d1's info line), from the run's steps."""
    if bv.run is None:
        return []
    from othor.core.localtime import local_now
    from othor.core.models import Company

    company = s.scalars(sa.select(Company).where(Company.id == bv.run.company_id)).one()
    out = []
    for st in s.scalars(
        sa.select(RunStep).where(RunStep.run_id == bv.run.id).order_by(RunStep.ordinal)
    ):
        if not st.name.startswith("read:"):
            continue
        name = st.name.split(":", 1)[1]
        ok = st.status == "done"
        when = local_now(company, st.ended_at) if st.ended_at else None
        out.append({"name": name, "ok": ok, "at": when})
    return out


# --- the briefs page (4a, 4b) -------------------------------------------------------------------


GRAIN_WORD = {"day": "daily", "week": "weekly", "month": "monthly"}


def scope_text(spec: dict[str, Any], body: dict[str, Any]) -> str:
    sc = body.get("scope") or {}
    names = spec.get("metrics", {})
    metrics = sc.get("metrics")
    areas = sc.get("areas")
    if metrics:
        out = ", ".join(str(names.get(m, {}).get("name") or m) for m in metrics[:3]) + (
            f" and {len(metrics) - 3} more" if len(metrics) > 3 else ""
        )
    elif areas is not None:  # 4d4: the areas this brief's findings come from
        out = (
            ", ".join(areas[:3]) + (f" and {len(areas) - 3} more" if len(areas) > 3 else "")
        ) or "No area"
    else:
        out = "Whole company"
    left = [v for vals in (sc.get("exclude") or {}).values() for v in vals]
    if left:
        out += " · " + ", ".join(left) + " left out"
    return out


def listing(s: Session, settings: Settings) -> dict[str, Any]:
    from othor.briefs.models import Recipient

    run = s.scalars(
        sa.select(Run)
        .where(Run.status.in_(("done", "partial")), Run.kind.in_(DETECTING_KINDS))
        .order_by(Run.number.desc())
        .limit(1)
    ).first()
    spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one() if run else None
    compiled = dict(spec.compiled) if spec else {}
    tests = 0
    if run is not None:
        from othor.engine.store import RunFiles, read_json

        doc = read_json(RunFiles(settings.runs_dir(run.company_id), run.id).outputs_json) or {}
        tests = int((doc.get("sweep") or {}).get("tests") or 0)
    cards = []
    for b in service.active(s):
        v = service.current(s, b)
        body = v.body if v else {}
        issue = card_issue(s, b, body, run)
        n = s.scalar(
            sa.select(sa.func.count())
            .select_from(Recipient)
            .where(Recipient.brief_id == b.id, Recipient.status == "active")
        )
        ask = ((issue.body.get("ask") or {}) if issue else {}).get("to")
        cards.append(
            {
                "id": b.id,
                "name": b.name,
                "default": b.is_default,
                "title": issue.title if issue else "",
                "item_count": len(issue.body.get("items") or []) if issue else 0,
                "quiet": bool(issue and issue.quiet),
                "scope": scope_text(compiled, body),
                "recipients": int(n or 0),
                "ask": ask
                or (body.get("ask_owner") if body.get("ask_owner") != "top_item" else ""),
                "time": body.get("time", "06:00"),
                "ready": issue is not None,
                "chart": lead_chart(issue.body, quiet=issue.quiet) if issue else None,
            }
        )
    return {
        "run": run,
        "tests": tests,
        "cards": cards,
        "first": bool(run and run.number <= 2),
        "sent_at": first_sent(s, run) if run else None,
    }


def first_sent(s: Session, run: Run) -> Any:
    """When this run's briefs went out: the first brief email sent (M15.3, "Sent today 06:00")."""
    from othor.mail.models import Delivery

    return s.scalar(
        sa.select(sa.func.min(Delivery.sent_at))
        .join(BriefIssue, BriefIssue.id == Delivery.brief_issue_id)
        .where(BriefIssue.run_id == run.id, Delivery.kind == "brief", Delivery.status == "sent")
    )


def held(s: Session, today: dt.date) -> dict[str, str]:
    """{fingerprint: "Snoozed until 20 Nov" | "Muted"}: the owner's holds, shown on a brief's
    cards (the stored brief keeps its items; M15.3)."""
    from othor.engine import holds

    snoozes, mutes = holds.active(s, today)
    out = {m["fingerprint"]: "Muted" for m in mutes if m.get("fingerprint")}
    for x in snoozes:
        out[x["fingerprint"]] = f"Snoozed until {fmt.day_month(dt.date.fromisoformat(x['until']))}"
    return out


def recipient_count(s: Session, brief_id: Any) -> int:
    from othor.briefs.models import Recipient

    return int(
        s.scalar(
            sa.select(sa.func.count())
            .select_from(Recipient)
            .where(Recipient.brief_id == brief_id, Recipient.status == "active")
        )
        or 0
    )


def watch_since(s: Session, body: dict[str, Any]) -> dict[str, Any]:
    """{watch id: the day it first breached} for the brief's Watching line (M15.3), read from the
    watch itself so the stored brief stays as it is."""
    from othor.engine.models import Watch

    ids = []
    for w in body.get("watching") or []:
        try:
            ids.append(uuid.UUID(str(w.get("id"))))
        except ValueError:
            continue
    if not ids:
        return {}
    rows = s.execute(sa.select(Watch.id, Watch.breached_since).where(Watch.id.in_(ids))).all()
    return {str(i): d for i, d in rows if d is not None}


def sent_to(s: Session, issue_id: Any) -> tuple[Any, int]:
    """(first sent time, recipients sent) for one issue: "sent 06:00 to 3" (M15.3)."""
    from othor.mail.models import Delivery

    row = s.execute(
        sa.select(sa.func.min(Delivery.sent_at), sa.func.count()).where(
            Delivery.brief_issue_id == issue_id,
            Delivery.kind == "brief",
            Delivery.status == "sent",
        )
    ).one()
    return row[0], int(row[1] or 0)


# --- the archive (4e, 4e1) ----------------------------------------------------------------------


def months(s: Session) -> list[dt.date]:
    got = sorted({i.date.replace(day=1) for i in s.scalars(sa.select(BriefIssue))}, reverse=True)
    return got


def archive(
    s: Session, brief_id: uuid.UUID | None, month: dt.date | None, today: dt.date
) -> dict[str, Any]:
    """Every issue by date, with the failed nights in place (4e); month counts."""
    from othor.core.models import Event

    briefs = {b.id: b for b in service.active(s)}
    q = sa.select(BriefIssue).order_by(BriefIssue.date.desc(), BriefIssue.created_at.desc())
    if brief_id is not None:
        q = q.where(BriefIssue.brief_id == brief_id)
    rows: list[dict[str, Any]] = []
    # one row per brief and day (owner, 16 Nov 2026): the latest issue; the day's earlier runs
    # (06:00 and each Run again now) open from its row in place
    day_row: dict[tuple[uuid.UUID, dt.date], dict[str, Any]] = {}
    runs = {r.id: r for r in s.scalars(sa.select(Run))}
    for i in s.scalars(q):
        if month and i.date.replace(day=1) != month:
            continue
        if i.brief_id not in briefs:
            continue
        r = runs.get(i.run_id)
        if (key := (i.brief_id, i.date)) in day_row:
            day_row[key]["earlier"].append(
                {"run": r.number if r else None, "issue": i.id, "at": fmt.clock(i.created_at)}
            )
            continue
        # Run again now's issue (v0.11)
        rerun = i.status == "rerun"
        day_row[key] = row = {
            "date": i.date,
            "brief": briefs[i.brief_id].name,
            "brief_id": i.brief_id,
            "title": i.title,
            "item_count": len(i.body.get("items") or []),
            "tests": int(i.body.get("tests") or 0),
            "run": r.number if r else None,
            "stale": bool(i.body.get("stale")),
            "rerun": rerun,
            "issue": None,
            "earlier": [],
            "failed": False,
        }
        rows.append(row)
    for r in runs.values():
        if r.kind != "nightly" or r.status != "failed":
            continue
        if month and r.run_date.replace(day=1) != month:
            continue
        rows.append(
            {
                "date": r.run_date,
                "brief": "",
                "title": "",
                "item_count": 0,
                "tests": 0,
                "run": r.number,
                "failed": True,
                "rerun": False,
                "stale": False,
            }
        )
    rows.sort(key=lambda x: (x["date"], not x["failed"], x["run"] or 0), reverse=True)
    window = [r for r in runs.values() if r.kind == "nightly" and (today - r.run_date).days < 31]
    ok = [r for r in window if r.status in ("done", "partial")]
    replays = [
        e
        for e in s.scalars(sa.select(Event).where(Event.action == "run.replayed"))
        if e.object_id in {str(r.id) for r in window}
    ]
    identical = {e.object_id for e in replays if (e.after or {}).get("ok")}
    issues = [r for r in rows if not r["failed"]]
    return {
        "rows": rows,
        "stats": {
            "briefs": len(issues),
            "brief_kinds": len({r["brief"] for r in issues}),
            "runs_ok": len(ok),
            "runs": len(window),
            "promoted": sum(r["item_count"] for r in issues),
            "replayed": len({e.object_id for e in replays}),
            "identical": len(identical),
        },
        "briefs": list(briefs.values()),
    }


def failed_run(s: Session, number: int) -> dict[str, Any] | None:
    """Why a run failed (4e1): its steps, the first failure and what it stopped."""
    r = s.scalars(sa.select(Run).where(Run.number == number)).first()
    if r is None or r.status != "failed":
        return None
    steps = list(
        s.scalars(sa.select(RunStep).where(RunStep.run_id == r.id).order_by(RunStep.ordinal))
    )
    first = next((st for st in steps if st.status == "failed"), None)
    latest = s.scalar(sa.select(sa.func.max(Run.number)))
    job = s.scalars(sa.select(Job).where(Job.id == r.job_id)).first() if r.job_id else None
    return {
        "number": r.number,
        # Re-run now (8c, M11a): only the latest run; a later run is chained after this one
        "can_rerun": r.number == latest and r.kind == "nightly",
        "rerunning": job is not None and job.status in ("queued", "running"),
        "date": r.run_date,
        "briefs": [b.name for b in service.active(s)],
        "step": step_label(first.name) if first else "",
        "error": (first.error or "") if first else "",
        "steps": [{"name": st.name, "status": st.status} for st in steps],
        "phases": _phases(s, r),
        "facts": _run_facts(s, r, first),
    }


def _run_facts(s: Session, r: Run, first: RunStep | None) -> list[tuple[str, str]]:
    """The failed run's facts (4e1): when it started and stopped, at which step, spec, image."""
    from othor.core.localtime import local_now
    from othor.core.models import Company

    company = s.scalars(sa.select(Company).where(Company.id == r.company_id)).one()
    spec = s.scalars(sa.select(Spec).where(Spec.id == r.spec_id)).first()

    def at(t: dt.datetime | None) -> str:
        return fmt.time_hm(local_now(company, t).time(), seconds=True) if t else ""

    ended = r.ended_at or (first.ended_at if first else None)
    stop = " · ".join(
        x for x in (at(ended), f"while {step_label(first.name)}" if first else "") if x
    )
    rid = str(r.id)
    return [
        ("Run id", f"{rid[:4]}…{rid[-2:]}"),
        ("Started", at(r.started_at)),
        ("Stopped", stop),
        ("Spec", f"v{spec.ontology_version}" if spec else ""),
        ("Image", str(r.image_version or "")),
    ]


def _phases(s: Session, run: Run) -> list[Any]:
    from othor.web.run_view import phases

    return phases(s, run)


# --- Edit brief (4d2–4d6) and Add a headline number (4d5) ---------------------------------------


@dataclass
class EditView:
    brief: Brief
    version: int
    changed: str  # "v3 · last changed 28 Oct by Uday"
    draft: dict[str, Any]
    tab: str
    options: dict[str, list[str]]
    owners: list[dict[str, Any]]
    goals: list[dict[str, Any]]
    headline: list[dict[str, Any]]
    totals: list[dict[str, Any]]
    effect: str
    preview_title: str
    dirty: bool
    reply: str
    families: list[dict[str, Any]]
    q: str
    recipients: list[Any]
    floor: str
    on_options: list[tuple[str, str]] = dc_field(default_factory=list)  # M15.7c: Weekly/Monthly
    areas: list[dict[str, Any]] = dc_field(default_factory=list)  # 4d4: Findings come from


def _last_run(s: Session) -> Run | None:
    return s.scalars(
        sa.select(Run)
        .where(Run.status.in_(("done", "partial")), Run.kind.in_(DETECTING_KINDS))
        .order_by(Run.number.desc())
        .limit(1)
    ).first()


def run_parts(s: Session, settings: Settings) -> tuple[Run, dict[str, Any], Any] | None:
    """The last sealed run, its compiled spec and its files (previews read them)."""
    from othor.engine.store import RunFiles

    run = _last_run(s)
    if run is None:
        return None
    spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one()
    return run, dict(spec.compiled), RunFiles(settings.runs_dir(run.company_id), run.id)


def families(spec: dict[str, Any], q: str, headline: list[str]) -> list[dict[str, Any]]:
    """Metrics grouped by family, filtered by the search (4d5): name, key or family."""
    groups: dict[str, list[dict[str, Any]]] = {}
    ql = q.strip().lower()
    for k, m in sorted(spec.get("metrics", {}).items(), key=lambda kv: str(kv[1].get("name"))):
        name = str(m.get("name") or k)
        fam = str(m.get("family") or k)
        if ql and ql not in name.lower() and ql not in k and ql not in fam:
            continue
        groups.setdefault(fam, []).append(
            {
                "key": k,
                "name": name,
                "grain": m.get("grain") or "month",
                "active": m.get("status") == "active",
                "added": k in headline,
                "source": str(m.get("source_label") or "").split(" · ")[-1],
            }
        )
    names = spec.get("metrics", {})
    return [
        {
            "family": str((names.get(f) or {}).get("name") or f.replace("_", " ").capitalize()),
            "metrics": ms,
        }
        for f, ms in sorted(groups.items())
    ]


def edit_view(
    s: Session,
    settings: Settings,
    brief: Brief,
    draft: dict[str, Any] | None,
    tab: str,
    *,
    reply: str = "",
    q: str = "",
    today: dt.date,
) -> EditView | None:
    from othor.briefs import edit, step
    from othor.briefs.models import BriefVersion, Recipient
    from othor.briefs.schedule import on_options as schedule_on_options
    from othor.briefs.tiles import tile
    from othor.engine.store import read_json
    from othor.engine.sweep_text import money
    from othor.web import run_cache

    parts = run_parts(s, settings)
    v = service.current(s, brief)
    if parts is None or v is None:
        return None
    run, spec, files = parts
    by_metric = run_cache.by_metric(files.series)  # each tile reads its own metric (M15.1)
    options = run_cache.scope_options(spec, files.series)
    saved = edit.clean(dict(v.body), spec, options)
    body = edit.clean(draft if draft is not None else saved, spec, options)
    context = read_json(files.inputs / "context.json") or {}
    record = (context.get("forecasts") or {}).get("of_record") or []
    goals_now = (read_json(files.outputs / "forecasts.json") or {}).get("goals") or []
    rows = []
    for m in body["headline"]:
        t = tile(spec, m, list(by_metric.get(m, ())), record, goals_now, run.run_date)
        meta = spec["metrics"].get(m) or {}
        rows.append(
            {
                "key": m,
                "name": meta.get("name") or m,
                "how": f"{GRAIN_WORD.get(meta.get('grain') or 'month', 'monthly')} · "
                + (t["comparison"]["text"].split(" · ")[-1] if t else "no value yet"),
                "today": t["value"] if t else "—",
            }
        )
    dirty = body != saved
    effect, title_now = "", ""
    if dirty:
        before = step.preview(
            files, spec, run.run_date, {**saved, "id": str(brief.id), "number": v.number}
        )
        after = step.preview(
            files, spec, run.run_date, {**body, "id": str(brief.id), "number": v.number + 1}
        )
        effect = edit.effect(before, after)
        title_now = str(after.get("title") or "")
    by = s.scalars(
        sa.select(BriefVersion)
        .where(BriefVersion.brief_id == brief.id)
        .order_by(BriefVersion.number.desc())
    ).first()
    when = by.created_at if by else brief.created_at
    who = (by.created_by if by else "") or "Othor AI"
    who = "Othor AI" if who == "system" else who.split("@")[0].split(" ")[0]
    return EditView(
        brief=brief,
        version=v.number,
        changed=f"v{v.number} · last changed {fmt.day_month(when.date())} by {who}",
        draft=body,
        tab=tab if tab in (*edit.TABS, "add") else "question",
        options=options,
        owners=list(spec.get("owners") or []),
        goals=list(spec.get("forecast", {}).get("goals") or []),
        headline=rows,
        totals=edit.in_scope_totals(
            spec, [p for m in body["headline"] for p in by_metric.get(m, ())], body
        ),
        effect=effect,
        preview_title=title_now,
        dirty=dirty,
        reply=reply,
        families=families(spec, q, body["headline"]) if q or tab == "add" else [],
        q=q,
        recipients=list(
            s.scalars(
                sa.select(Recipient)
                .where(
                    Recipient.brief_id == brief.id,
                    Recipient.status.in_(("active", "unsubscribed", "bounced")),
                )
                .order_by(Recipient.created_at)
            )
        ),
        floor=money(spec.get("detectors", {}).get("materiality_inr", "500000")),
        on_options=schedule_on_options(body["schedule"]),
        areas=edit.area_rows(spec, body.get("scope") or {}),
    )


# --- New brief (4c, 4c1, 4c2) and suggested briefs (4b) ----------------------------------------


def numbers_text(n: int) -> str:
    """The headline numbers' count, never empty: "1 number", "6 numbers" (v0.11)."""
    return f"{fmt.num(n)} number{'' if n == 1 else 's'}"


def _owner_note(o: dict[str, Any] | None) -> str:
    if not o:
        return ""
    areas = [str(a) for a in o.get("areas") or []]
    return ", ".join(
        x for x in (str(o.get("role") or ""), "owns " + ", ".join(areas) if areas else "") if x
    )


def draft_rows(
    spec: dict[str, Any],
    body: dict[str, Any],
    options: dict[str, list[str]],
    issue: dict[str, Any],
) -> list[dict[str, str]]:
    """The draft as the New brief table reads it (v0.11, #28): Question · Scope · Numbers ·
    Ask today · Schedule · First sent to, each a value and a quieter note, and the line a
    row's Change puts in the Change anything box (empty: no Change)."""
    names = spec.get("metrics", {})
    sc = body.get("scope") or {}
    exclude = sc.get("exclude") or {}
    if sc.get("metrics"):
        scope = ", ".join(str(names.get(m, {}).get("name") or m) for m in sc["metrics"])
    elif exclude:
        kept = [
            v for b, vals in options.items() if b in exclude for v in vals if v not in exclude[b]
        ]
        scope = (", ".join(kept) + " only") if kept else "Whole company"
    else:
        scope = "Whole company"
    left = [v for vals in exclude.values() for v in vals]
    head = [str(names.get(m, {}).get("name") or m) for m in body.get("headline") or []]
    owners = {o["name"]: o for o in spec.get("owners") or []}
    ask = body.get("ask_owner") or "top_item"
    today = issue.get("ask") or {}
    if ask == "top_item":
        who = "The owner of the top item"
        note = ", ".join(x for x in (today.get("to"), today.get("role")) if x)
        note = f"today: {note}" if note else ""
    else:
        who, note = ask, _owner_note(owners.get(ask))
    from othor.briefs.schedule import label as schedule_label

    when = schedule_label(body)
    return [
        {"k": "Question", "v": body.get("question", ""), "n": "", "fill": "Ask instead: "},
        {
            "k": "Scope",
            "v": scope,
            "n": ("leaves out " + ", ".join(left)) if left else "nothing left out",
            "fill": "Only ",
        },
        {
            "k": "Numbers",
            "v": " · ".join(head) if head else "None yet",
            "n": numbers_text(len(head)),
            "fill": "Add ",
        },
        {"k": "Ask today", "v": who, "n": note, "fill": "Ask "},
        {
            "k": "Schedule",
            "v": f"{when} · {fmt.clock(str(body.get('time') or '06:00'))}",
            "n": "",
            "fill": "Send on ",
        },
        {"k": "First sent to", "v": "You", "n": "add recipients after it’s created", "fill": ""},
    ]


def change_examples(
    spec: dict[str, Any], body: dict[str, Any], options: dict[str, list[str]]
) -> list[str]:
    """Up to 3 one-line changes the draft can take, from its own lists: a breakdown value to
    keep, a number it doesn't have yet, weekdays or every day."""
    out = []
    left = (body.get("scope") or {}).get("exclude") or {}
    for b, vals in options.items():
        keep = [v for v in vals if v not in left.get(b, [])]
        if len(keep) > 1:
            out.append(f"Only {keep[0]}")
            break
    metrics = spec.get("metrics", {})
    head = set(body.get("headline") or [])
    for k in spec.get("order") or sorted(metrics):
        m = metrics.get(k, {})
        if k not in head and m.get("status") == "active":
            out.append(f"Add {m.get('name') or k}")
            break
    out.append("Weekdays only" if body.get("schedule") in (None, "daily") else "Every day")
    return out[:3]


def new_preview(
    s: Session, settings: Settings, draft: dict[str, Any], tomorrow: dt.date | None = None
) -> dict[str, Any] | None:
    """Tomorrow's brief from last night's data (4c1): the run's own function on the draft, and
    the draft as a table (v0.11)."""
    from othor.briefs import edit, step
    from othor.web import run_cache

    parts = run_parts(s, settings)
    if parts is None:
        return None
    run, spec, files = parts
    options = run_cache.scope_options(spec, files.series)
    body = edit.clean(draft, spec, options)
    issue = step.preview(files, spec, run.run_date, {**body, "id": "draft", "number": 0})
    day = tomorrow or run.run_date + dt.timedelta(days=1)
    return {
        "draft": body,
        "issue": issue,
        "kicker": f"{body['name']} · {fmt.date_short(day)}",
        "tiles": [{**t, "spark": _chart(t.get("chart"))} for t in issue.get("tiles") or []][:3],
        "top_items": [
            {**i, "n": f"{int(i.get('rank') or n):02d}"}
            for n, i in enumerate((issue.get("items") or [])[:2], start=1)
        ],
        "rows": draft_rows(spec, body, options, issue),
        "examples": change_examples(spec, body, options),
        "numbers": numbers_text(len(body["headline"])),
    }


# where a suggested brief's reasons came from (brief_suggest's `from`), as step 1's cards say it
_FROM = {
    "goals": "From your goal",
    "your message": "From your business",
    "sweep": "From last night’s Sweep",
    "ontology": "From your Business Model",
}


def ask_cards(s: Session, settings: Settings, suggested: list[Any]) -> list[dict[str, Any]]:
    """Step 1's "Suggested for <company>" (v0.11): up to 3 questions, each with where it came
    from. The suggested briefs first (their `from` and first reason); then, by rule from last
    night's spec, a goal and a comparison across a breakdown. Nothing here is a model's number."""
    from othor.briefs.assemble import default_version
    from othor.web import run_cache

    out: list[dict[str, Any]] = []
    for g in suggested[:3]:
        frm = [str(x) for x in (g.draft or {}).get("from") or []]
        lead = _FROM.get(frm[0].lower(), f"From {frm[0]}") if frm else "Suggested"
        why = (g.evidence or [""])[0]
        out.append(
            {"q": g.question, "from": " · ".join(x for x in (lead, why) if x), "id": str(g.id)}
        )
    parts = run_parts(s, settings) if len(out) < 3 else None
    if parts is None:
        return out
    _, spec, files = parts
    asked = {c["q"].lower() for c in out}
    metrics = spec.get("metrics", {})
    rules: list[dict[str, Any]] = []
    for goal in spec.get("forecast", {}).get("goals", [])[:1]:
        name = str(goal.get("name") or goal.get("key"))
        rules.append({"q": f"Are we on track for {name}?", "from": f"From your goal · {name}"})
    head = default_version(spec)["headline"]
    options = run_cache.scope_options(spec, files.series)
    for b, vals in options.items():
        if len(vals) >= 2 and head:
            label = str((spec.get("breakdowns") or {}).get(b, {}).get("name") or b).lower()
            what = str(metrics.get(head[0], {}).get("name") or head[0])
            what = what[:1].lower() + what[1:] if what[1:2].islower() else what  # not DSO
            q = (
                f"How do {vals[0]} and {vals[1]} compare on {what}?"
                if len(vals) == 2
                else f"Which {label} is behind on {what}?"
            )
            rules.append({"q": q, "from": f"From your business · {fmt.num(len(vals))} {label}s"})
            break
    for r in rules:
        if len(out) < 3 and r["q"].lower() not in asked:
            out.append({**r, "id": ""})
    return out


def suggestions(s: Session) -> list[Any]:
    from othor.briefs.models import SuggestedBrief

    return list(
        s.scalars(
            sa.select(SuggestedBrief)
            .where(SuggestedBrief.status == "shown")
            .order_by(SuggestedBrief.created_at)
            .limit(3)
        )
    )


# --- Add to a brief (10o) and "Also in today's briefs" (5a) ------------------------------------


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def add_options(s: Session, settings: Settings, f: dict[str, Any]) -> list[dict[str, Any]]:
    """Each brief: does its scope cover the finding, and where would it rank tomorrow (the
    run's own function on last night's data, with the finding added)."""
    from othor.briefs import edit, step
    from othor.briefs.scope import finding_in
    from othor.engine.findings import pattern_of
    from othor.web import run_cache

    parts = run_parts(s, settings)
    if parts is None:
        return []
    run, spec, files = parts
    options = run_cache.scope_options(spec, files.series)
    pat = pattern_of(f)
    names = spec.get("metrics", {})
    out = []
    for b in service.active(s):
        v = service.current(s, b)
        if v is None:
            continue
        body = edit.clean(dict(v.body), spec, options)
        today = s.scalars(
            sa.select(BriefIssue).where(BriefIssue.brief_id == b.id, BriefIssue.run_id == run.id)
        ).first()
        n_today = len(today.body.get("items") or []) if today else 0
        mine = {i["fingerprint"] for i in (today.body.get("items") or [])} if today else set()
        row: dict[str, Any] = {
            "id": b.id,
            "name": b.name,
            "scope": scope_text(spec, body),
            "item_count": n_today,
            "added": pat in body["pinned"],
            "in_today": f["fingerprint"] in mine,
        }
        if not finding_in(spec, body.get("scope") or {}, f):
            what = str(names.get(f["metric"], {}).get("name") or f["metric"]).lower()
            row["blocked"] = f"Doesn’t cover {what} · add the area in Edit brief first"
        else:
            after = step.preview(
                files,
                spec,
                run.run_date,
                {**body, "pinned": [*body["pinned"], pat], "id": str(b.id), "number": v.number},
            )
            fps = [i["fingerprint"] for i in after.get("items") or []]
            if f["fingerprint"] in fps:
                at = fps.index(f["fingerprint"]) + 1
                pushed = n_today >= 4 and not row["in_today"]
                row["tomorrow"] = f"Tomorrow: ranks {_ordinal(at)}" + (
                    "; item 4 moves to “more in Sweep”" if pushed else f"; {len(fps)} items"
                )
        out.append(row)
    first = next((r for r in out if not r.get("blocked") and not r["added"]), None)
    if first is not None:
        first["default"] = True
    return out


def fp_briefs(s: Session, run: Run) -> dict[str, str]:
    """{finding fingerprint: the first brief tonight that carries it} (Sweep's "In <brief>")."""
    out: dict[str, str] = {}
    for b in service.active(s):
        i = s.scalars(
            sa.select(BriefIssue).where(BriefIssue.brief_id == b.id, BriefIssue.run_id == run.id)
        ).first()
        if i is None:
            continue
        for it in i.body.get("items") or []:
            out.setdefault(it["fingerprint"], b.name)
    return out


def in_todays_briefs(s: Session, run: Run) -> dict[str, Any]:
    """Every brief's items tonight, once each (5a's "Also in today's briefs · 4")."""
    titles: dict[str, str] = {}
    first: Brief | None = None
    for b in service.active(s):
        i = s.scalars(
            sa.select(BriefIssue).where(BriefIssue.brief_id == b.id, BriefIssue.run_id == run.id)
        ).first()
        if i is None:
            continue
        first = first or b
        for it in i.body.get("items") or []:
            titles.setdefault(it["fingerprint"], it["title"])
    return {"fps": set(titles), "titles": list(titles.values()), "brief": first}
