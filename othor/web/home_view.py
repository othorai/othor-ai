# SPDX-License-Identifier: Apache-2.0
"""Home (3a, 3aa, 3b, 10k; M10a): what the page shows, computed from the last run and the
Ontology. Nothing is copied from a board.

- Banners first: a source the last run couldn't read (10k), and recipients who unsubscribed or
  bounced in the last 7 days (10f "owner told").
- One card per active brief (today's issue) and the Sweep card, whose counts read the same
  overlay as Sweep and the brief ("counts agree").
- Proposals (open, not joins: those stay on the source's pane) and questions (one at a time).
- All caught up (3b): the Ontology versions written from Home lately.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.briefs import service as briefs
from othor.briefs.models import BriefIssue, Recipient
from othor.core import features, fmt
from othor.core.config import Settings
from othor.core.localtime import local_now
from othor.core.models import Company, Event
from othor.engine import sweep_text
from othor.engine.models import DETECTING_KINDS, Proposal, Run, SeriesMeta
from othor.home import answers
from othor.ontology import service
from othor.ontology.models import OntologyRecord, Question
from othor.ontology.questions import NEEDS_EXPLAIN, NEEDS_QUESTIONS
from othor.web import sweep_view as sv
from othor.web import weather_view

KIND_LABEL = {
    "suppression": "Suppression",
    "goal": "Goal",
    "definition_change": "Definition change",
    "breakdowns": "Breakdowns",
    "areas": "Areas",
    "duplicate_metric": "Same number twice",
}
CONFIDENCE = {"high": "High", "medium": "Medium", "low": "Low"}


def kind_label(kind: str, default: str | None = None) -> str:
    """A proposal's tag: the core's, or the plug-in kind's (Phase 0 §2)."""
    from othor.engine.proposals import PROPOSALS

    if kind in KIND_LABEL:
        return KIND_LABEL[kind]
    if kind in PROPOSALS:
        return PROPOSALS[kind].label
    return default if default is not None else kind


NOTICE_DAYS = 7


@dataclass
class Origin:
    label: str  # the question's tag: Morning Brief · Sweep · Metric gap
    upper: str  # the chat's kicker: MORNING BRIEF


def latest_run(s: Session) -> Run | None:
    return s.scalars(
        sa.select(Run)
        .where(Run.status.in_(("done", "partial")), Run.kind.in_(DETECTING_KINDS))
        .order_by(Run.number.desc())
        .limit(1)
    ).first()


# --- banners --------------------------------------------------------------------------------


def stale_banners(s: Session, run: Run | None, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """10k: each source the last run couldn't read, what it affects and since when."""
    from othor.connectors.models import Source

    if run is None or run.status != "partial":
        return []
    names = list((run.summary or {}).get("stale_sources") or [])
    if not names:
        return []
    metas = s.scalars(
        sa.select(SeriesMeta).where(SeriesMeta.stale.is_(True), SeriesMeta.last_run_id == run.id)
    ).all()
    issues = s.scalars(sa.select(BriefIssue).where(BriefIssue.run_id == run.id)).all()
    sources = {x.name: x for x in s.scalars(sa.select(Source)).all()}
    metrics = spec.get("metrics", {})
    out = []
    for name in names:
        keys = sorted(
            {
                m.metric
                for m in metas
                if name in (metrics.get(m.metric, {}).get("sources") or [])
                or name in str(metrics.get(m.metric, {}).get("source_label", ""))
            },
            key=lambda k: spec.get("order", []).index(k) if k in spec.get("order", []) else 999,
        )
        last = [m.last_good_at for m in metas if m.metric in keys and m.last_good_at]
        since = min(last) if last else None
        shown = [str(metrics.get(k, {}).get("name") or k) for k in keys]
        in_briefs = [i for i in issues if set(i.body.get("stale") or []) & set(keys)]
        src = sources.get(name)
        parts = []
        if shown:
            nums = _join(shown[:3]) + (f" and {len(shown) - 3} more" if len(shown) > 3 else "")
            are = "is" if len(shown) == 1 else "are"
            when = f" from {fmt.date_short(since)}" if since else " from an earlier day"
            parts.append(f"{nums} {are}{when}")
            if in_briefs:
                n = len(in_briefs)
                parts[-1] += f", in {fmt.num(n)} brief{'s' if n != 1 else ''}"
        from othor.web.sources_view import KIND_NAME

        kind = KIND_NAME.get(src.kind, "") if src else ""
        lead = f"{kind} · {name}" if kind else name
        if src is not None and src.status == "disconnected":
            lead += " disconnected"
            why = src.disconnect_reason or ""
            if src.disconnected_at:
                why += f" at {fmt.clock(src.disconnected_at)}" if why else ""
            if why:
                parts.insert(0, why[:1].upper() + why[1:])
        else:
            lead += " wasn’t read last night"
        out.append(
            {
                "lead": lead,
                "text": (". ".join(parts) + ".")
                if parts
                else "Its numbers keep their last values.",
                "href": f"/sources?s={src.id}&panel=reconnect" if src else "/sources",
                "action": "Reconnect →",
            }
        )
    return out


def _join(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def recipient_notices(s: Session, now: dt.datetime) -> list[dict[str, Any]]:
    """Unsubscribes and bounces in the last week, until the owner dismisses them."""
    since = now - dt.timedelta(days=NOTICE_DAYS)
    evs = s.scalars(
        sa.select(Event)
        .where(
            Event.action.in_(("recipient.unsubscribed", "recipient.bounced")),
            Event.at >= since,
        )
        .order_by(Event.at.desc())
    ).all()
    dismissed = {
        e.object_id
        for e in s.scalars(sa.select(Event).where(Event.action == "home.notice_dismissed"))
    }
    out = []
    for e in evs:
        if str(e.id) in dismissed:
            continue
        r = s.scalars(sa.select(Recipient).where(Recipient.id == _uuid(e.object_id))).first()
        if r is None or r.status not in ("unsubscribed", "bounced"):
            continue  # undone or added back since
        b = briefs.get(s, r.brief_id)
        who = r.name or r.email
        what = "unsubscribed from" if e.action == "recipient.unsubscribed" else "bounced on"
        out.append(
            {
                "id": str(e.id),
                "text": f"{who} {what} {b.name if b else 'a brief'} · "
                f"{fmt.date_short(e.at.date())}",
                "href": f"/brief/{r.brief_id}/edit?tab=delivery",
            }
        )
    return out


def _uuid(v: Any) -> Any:
    import uuid

    try:
        return uuid.UUID(str(v))
    except ValueError:
        return None


# --- cards ----------------------------------------------------------------------------------


LATE_SEND_MINUTES = 30


def _today_mail(
    s: Session, brief_id: Any, company: Company, now: dt.datetime | None
) -> dict[str, int]:
    """This brief's emails today (the company's day), by status, whichever issue they carried."""
    from othor.briefs.models import BriefIssue
    from othor.mail.models import Delivery

    out = {"sent": 0, "failed": 0, "bounced": 0}
    if now is None:
        return out
    local = local_now(company, now)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(dt.UTC)
    rows = s.execute(
        sa.select(Delivery.status, sa.func.count())
        .join(BriefIssue, BriefIssue.id == Delivery.brief_issue_id)
        .where(
            Delivery.kind == "brief",
            BriefIssue.brief_id == brief_id,
            Delivery.created_at >= start,
        )
        .group_by(Delivery.status)
    ).all()
    for status, n in rows:
        if status in out:
            out[status] = int(n)
    return out


def brief_cards(
    s: Session, run: Run | None, company: Company, now: dt.datetime | None = None
) -> list[dict[str, Any]]:
    from othor.mail.models import Delivery

    out = []
    leads: dict[str, str] = {}  # a lead item's fingerprint -> the first card it leads
    for b in briefs.active(s):
        v = briefs.current(s, b)
        body = v.body if v else {}
        from othor.web.brief_view import card_issue, next_line

        issue = card_issue(s, b, body, run)
        n = int(
            s.scalar(
                sa.select(sa.func.count())
                .select_from(Recipient)
                .where(Recipient.brief_id == b.id, Recipient.status == "active")
            )
            or 0
        )
        sent = (
            int(
                s.scalar(
                    sa.select(sa.func.count())
                    .select_from(Delivery)
                    .where(
                        Delivery.kind == "brief",
                        Delivery.brief_issue_id == issue.id,
                        Delivery.status == "sent",
                    )
                )
                or 0
            )
            if issue
            else 0
        )
        time = body.get("time", "06:00")
        if issue is None:
            title, meta = "", "No issue yet"
        else:
            items = len(issue.body.get("items") or [])
            head = (
                "Quiet day"
                if issue.quiet
                else f"{fmt.num(items)} finding{'s' if items != 1 else ''}"
            )
            k = sent or n
            to = f"to {fmt.num(k)} recipient{'s' if k != 1 else ''}"
            meta = f"{head} · {'sent' if sent else 'goes out at'} {fmt.clock(time)} {to}"
            # a Weekly or Monthly brief's last (a same-day issue after a rerun keeps "sent")
            if run is not None and issue.run_id != run.id and issue.date != run.run_date:
                meta = f"{head} · {next_line(body, run)}"
            if issue.status == "rerun" and run is not None:  # Run again now (v0.11)
                from othor.web.again_view import updated_line

                meta = f"{head} · {updated_line(s, issue, run)}"
                if sent:
                    meta += f" · sent {to}"
            from othor.briefs.words import card_title

            title = card_title(issue.body, issue.title)
        # amber dot: a stale tile or a breached watch in this brief's scope
        flag = bool(issue and (issue.body.get("stale") or issue.body.get("watching")))
        from othor.web.brief_view import lead_chart

        body_shown = issue.body if issue else None
        # Same lead as another card (owner, 21 Nov 2026): a brief whose top item already leads an
        # earlier card says so and shows its own next item; the brief itself keeps its order
        items = (issue.body.get("items") or []) if issue else []
        lead_fp = items[0].get("fingerprint") if items and not issue.quiet else None
        if lead_fp and lead_fp in leads and len(items) > 1:
            body_shown = {**issue.body, "items": items[1:]}
            title = str(items[1].get("title") or title)
            meta = f"Same lead as {leads[lead_fp]} · {meta}"
        elif lead_fp:
            leads.setdefault(lead_fp, b.name)
        chart = lead_chart(body_shown, quiet=issue.quiet) if issue else None
        # for the morning checklist: due tonight (a Weekly or Monthly brief between its issues
        # isn't), made, sent, or not yet due
        here = issue is not None and run is not None and issue.run_id == run.id
        clock_now = local_now(company, now).time() if now is not None else None
        today = _today_mail(s, b.id, company, now)
        due_at = dt.datetime.combine(dt.date(2000, 1, 1), dt.time.fromisoformat(time))
        out.append(
            {
                "due_today": issue is None or here or issue.status == "rerun",
                "made": issue is not None,
                "sent": sent,
                "recipients": n,
                "time": time,
                "bounced": today["bounced"],
                "due_passed": clock_now is None or clock_now >= dt.time.fromisoformat(time),
                # System Health's Email delivery (owner, 17 Nov 2026): today's emails of this
                # brief, from whichever run made them; "not sent yet" only 30 min after its time
                "sent_today": today["sent"],
                "failed": today["failed"],
                "late": clock_now is None
                or clock_now >= (due_at + dt.timedelta(minutes=LATE_SEND_MINUTES)).time(),
                "new": int((issue.body.get("since") or {}).get("new") or 0) if issue else 0,
                "id": b.id,
                "name": b.name,
                "title": title,
                "meta": meta,
                "flag": flag,
                "chart": chart,
                "count": len(issue.body.get("items") or []) if issue else 0,
                "quiet": bool(issue is None or issue.quiet),
            }
        )
    return out


BRIEF_CARDS_MAX = 6  # two rows of three (owner, 11 Nov 2026)


def brief_row(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Home's briefs (design B2): those with things that need you first (most first), quiet ones
    after, in their own order otherwise; at most two rows. With more than six, five cards and a
    sixth that groups the rest and opens the Morning Brief."""
    ordered = sorted(cards, key=lambda c: (c.get("quiet", False), -int(c.get("count") or 0)))
    if len(ordered) <= BRIEF_CARDS_MAX:
        return {"cards": ordered, "rest": [], "more": None}
    shown, rest = ordered[: BRIEF_CARDS_MAX - 1], ordered[BRIEF_CARDS_MAX - 1 :]
    need = sum(1 for c in rest if int(c.get("count") or 0))
    return {
        "cards": shown,
        "rest": rest,
        "more": {"n": len(rest), "need": need, "href": "/brief"},
    }


def sweep_box_rows(
    new: list[dict[str, Any]],
    old: list[dict[str, Any]],
    *,
    found: int,
    n_new: int,
    in_briefs: set[str],
) -> dict[str, Any]:
    """Home's Sweep box (design B2): the three open findings with the most money, each marked when
    it's already in a brief, and how many more Sweep holds."""
    top = sorted(new + old, key=lambda c: (-c["raw"], c["fp"]))[:3]
    rows = [{**c, "in_brief": c["fp"] in in_briefs} for c in top]
    return {
        "rows": rows,
        "found": found,
        "new": n_new,
        "top": sum((c["raw"] for c in top), Decimal(0)),
        "more": max(found - len(rows), 0),
    }


# what a check that couldn't run is waiting for: an answer on Home, or more history (Sweep's
# coverage `needs`, sweep_text.NEEDS)
ANSWERABLE = {"entity", "reconcile", "pair", "threshold", "distribution"}


def _pending_needs(s: Session) -> set[str]:
    """The needs a question still asks about (open or set aside), for the checks strip."""
    from othor.ontology.models import Question

    rows = s.scalars(
        sa.select(Question.unblocks).where(
            Question.kind == "needs", Question.status.in_(("open", "skipped"))
        )
    )
    return {str((u or {}).get("needs") or "") for u in rows} - {""}


def checks_strip(
    rv: Any,
    questions: int,
    proposals: int = 0,
    later: int = 0,
    splits: str = "",
    pending: set[str] | None = None,
) -> dict[str, Any]:
    """Home's checks strip (owner's design, 11 Nov 2026): how many of the shipped checks ran in
    the last run, and why the rest didn't, read from the run's coverage. Its state picks the
    line and the one button: all ran and quiet (Open brief), all ran and found something (Open
    Sweep), some not run: the button always leads to what updates the Ontology (owner, 12 Nov
    2026): an open "Split your numbers by …" proposal first (one Accept lets most checks run,
    v0.11), then Answer questions, else Review proposals, else Update the Ontology; Open Sweep
    only once every check ran."""
    cov = {c["detector"]: c for c in rv.doc.get("coverage", [])}
    order = {"ran": 0, "waiting": 1, "history": 2, "cannot": 3}
    kinds = []
    names: dict[str, list[tuple[str, str]]] = {k: [] for k in order}  # the info box's lists
    for name in sv.shipped():
        c = cov.get(name)
        if c is None:
            continue
        if c["status"] in ("ran", "partly"):
            kind = "ran"
        elif c.get("needs") in ANSWERABLE and (pending is None or c.get("needs") in pending):
            # 0.27.2: waiting on the owner only while its question is open or set aside; once
            # answered (a question is never asked twice) and still blocked, it can't run
            kind = "waiting"
        elif c.get("needs") == "forecast":
            kind = "history"
        else:
            kind = "cannot"
        kinds.append(kind)
        need = "" if kind == "ran" else sweep_text.NEEDS.get(c.get("needs") or "", "")
        names[kind].append((sweep_text.LABELS.get(name, name), need))
    kinds.sort(key=order.__getitem__)
    n = {k: kinds.count(k) for k in order}
    total = len(kinds)
    when = {"nightly": "last night", "dry": "in the first run", "rerun": "today"}.get(
        getattr(rv.run, "kind", ""), "in the last run"
    )
    found = int(rv.counts.get("findings", 0))
    pct = round(100 * n["ran"] / total) if total else 0
    # "Intelligence" (owner, 12 Nov 2026): the share of the shipped checks that ran; the info
    # box says so in its first line
    lead = f"At {pct}% Intelligence."
    q = f"{fmt.num(questions)} question{'s' if questions != 1 else ''}"
    if total and n["ran"] == total:
        if found:
            state, tail = "all_found", f"{fmt.num(found)} thing{'s' if found != 1 else ''} found."
            action = ("Open Sweep", "/sweep")
        else:
            state, tail, action = "all_quiet", "Nothing needs you today.", ("Open brief", "/brief")
    else:
        state = "partial"
        if splits:
            tail = f"{splits} so more checks can run."
            action = ("Review the split", "/ontology#proposals")
        elif questions and n["waiting"]:
            reach = round(100 * (n["ran"] + n["waiting"]) / total)
            answers = f"{fmt.num(questions)} quick answer{'s' if questions != 1 else ''}"
            tail = f"Reach {reach}% with {answers}."
            action = (f"Answer {q}", "/home?panel=othor&subject=questions")
        elif later and n["waiting"]:
            aside = f"{fmt.num(later)} question{'s' if later != 1 else ''}"
            tail = f"You set aside {aside}; answering turns on up to {fmt.num(n['waiting'])} more."
            action = ("Answer questions", "/home?panel=othor&subject=questions")
        elif proposals:
            p = f"{fmt.num(proposals)} proposal{'s' if proposals != 1 else ''}"
            tail = f"Review {p} that change what the checks can see."
            action = ("Review proposals", "/ontology#proposals")
        elif n["history"] and not n["waiting"]:
            h = n["history"]
            tail = f"{fmt.num(h)} more turn{'s' if h == 1 else ''} on as your history grows."
            action = ("Update the Business Model", "/ontology")
        else:
            tail, action = "See what each check needs.", ("Update the Business Model", "/ontology")
    meaning = {
        "ran": "Ran on the latest numbers. What they found is in Sweep.",
        "waiting": (
            (
                f"Turn on when you answer {q} about your data."
                if questions
                else "Turn on when you answer the questions about your data."
            )
            if n["waiting"]
            else "Nothing to answer."
        ),
        "history": (
            "Turn on by themselves as more history builds up."
            if n["history"]
            else "Nothing waiting on history."
        ),
        "cannot": (
            "Your sources don’t have what these need. If your data has it, tell Othor AI."
            if n["cannot"]
            else "Every check can run on your data."
        ),
    }
    labels = {
        "ran": "Ran",
        "waiting": "Waiting on you",
        "history": "Needs more history",
        "cannot": "Can’t run with your data",
    }
    groups = [
        {"key": k, "label": labels[k], "n": n[k], "text": meaning[k], "checks": names[k]}
        for k in order
    ]
    # the bar's parts, each with its width class (f1…f12: no inline style, all built by the CSS)
    bar = [{"key": k, "cls": f"{k} f{min(n[k], 12)}"} for k in order if n[k]]
    return {
        "state": state,
        "pct": pct,
        "lead": lead,
        "tail": tail,
        "action": action,
        "total": total,
        "segments": kinds,
        "explain": (
            f"{pct}% Intelligence means {fmt.num(n['ran'])} of the {fmt.num(total)} checks "
            f"Sweep runs every night could run on your data {when}."
        ),
        "groups": groups,
        "bar": bar,
        **n,
    }


def last_detecting(s: Session, settings: Settings, today: dt.date) -> Any:
    """The last run that ran the detectors, loaded once for the Sweep card and the checks strip."""
    runs = sv.detecting_runs(s)
    return sv.load(s, settings, runs[0], today) if runs else None


def sweep_card(s: Session, rv: Any) -> dict[str, Any] | None:
    if rv is None:
        return None
    c = rv.counts
    return {
        "tests": f"{fmt.num(rv.doc.get('tests', 0))} tests last night · "
        f"{fmt.num(len(sv.shipped()))} detectors",
        "meta": f"{fmt.num(c['findings'])} finding{'s' if c['findings'] != 1 else ''} · "
        f"{fmt.num(c['suppressed'])} suppressed",
        "fps": {f["fingerprint"] for f in rv.shown},
        # leads with the Sweep page's first card that has a chart (M11c)
        **_lead(rv, _in_briefs(s, rv)),
        "n": c["findings"],
        "count": f"{fmt.num(c['findings'])} finding{'s' if c['findings'] != 1 else ''}",
    }


def sweep_box(s: Session, rv: Any) -> dict[str, Any] | None:
    """Home's Sweep box (design B2), from the last run that ran the detectors."""
    if rv is None:
        return None
    raw = {f["fingerprint"]: Decimal(str(f.get("money") or 0)) for f in rv.shown}
    new, old = sv.finding_cards(rv)
    for c in new + old:
        c["raw"] = raw.get(c["fp"], Decimal(0))
    box = sweep_box_rows(
        new,
        old,
        found=int(rv.counts.get("findings", 0)),
        n_new=int(rv.counts.get("new", 0)),
        in_briefs=_in_briefs(s, rv),
    )
    return {**box, "run": rv.run.number}


def _in_briefs(s: Session, rv: Any) -> set[str]:
    """Findings already in today's briefs: the Sweep page lists them as one line, not as cards."""
    if not features.BRIEFS:
        return set()
    from othor.web import brief_view

    return set(brief_view.in_todays_briefs(s, rv.run)["fps"])


def _lead(rv: Any, in_briefs: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    """The Sweep page's first card that has a chart: new findings by money, then open ones, less
    those already in today's briefs (as the page shows them). If every charted finding is in a
    brief, the first charted one of all."""
    new, old = sv.finding_cards(rv)
    cards = new + old
    for pool in ([c for c in cards if c["fp"] not in in_briefs], cards):
        for c in pool:
            if c["chart"] is not None:
                return {"chart": c["chart"], "lead": c["title"]}
    return {"chart": None, "lead": ""}


# --- proposals and questions ---------------------------------------------------------------


def proposal_rows(s: Session) -> list[dict[str, Any]]:
    rows = s.scalars(
        sa.select(Proposal)
        .where(Proposal.status == "open", Proposal.kind != "join")
        .order_by(Proposal.created_at, Proposal.id)
    ).all()
    # twins (23 Nov 2026, indigodemo): two goals on the same number, year and target each made
    # an "at risk" proposal; the newest one stands for both, and a decision applies to both
    newest: dict[str, Proposal] = {}
    for p in rows:
        newest[twin_key(p)] = p
    return [proposal_row(p) for p in rows if newest[twin_key(p)] is p]


def twin_key(p: Proposal) -> str:
    """Proposals that say the same thing: a goal's head (number, year, target), else itself."""
    if p.kind == "goal":
        return "goal:" + p.text.split(" → ", 1)[0]
    return str(p.id)


def open_twins(s: Session, p: Proposal) -> list[Proposal]:
    """The other open proposals that say the same as `p` (decided together with it)."""
    if p.kind != "goal":
        return []
    rows = s.scalars(
        sa.select(Proposal).where(
            Proposal.status == "open", Proposal.kind == "goal", Proposal.id != p.id
        )
    ).all()
    return [x for x in rows if twin_key(x) == twin_key(p)]


def decided_twins(s: Session, p: Proposal) -> list[Proposal]:
    """The twins decided together with `p` (same decision, same moment): undone with it."""
    if p.kind != "goal" or p.decided_at is None:
        return []
    rows = s.scalars(
        sa.select(Proposal).where(
            Proposal.kind == "goal",
            Proposal.id != p.id,
            Proposal.status == p.status,
            Proposal.decided_at == p.decided_at,
        )
    ).all()
    return [x for x in rows if twin_key(x) == twin_key(p)]


def proposal_row(p: Proposal) -> dict[str, Any]:
    from othor.engine.proposals import PROPOSALS

    ev = p.evidence or {}
    origin = p.origin or {}
    if p.kind == "goal":
        frm = "Morning Brief · forecast"
    elif p.kind == "suppression":
        frm = "Sweep · a finding that recurs"
    elif p.kind == "breakdowns":
        frm = "Sources · your tables"
    elif p.kind == "duplicate_metric":
        frm = "Sweep · your numbers"
    elif p.kind == "areas":
        frm = "Business Model · your numbers"
    elif p.kind in PROPOSALS:
        frm = PROPOSALS[p.kind].from_line(p)
    else:
        frm = "Home · your answers"
    del origin
    return {
        "id": str(p.id),
        "kind": kind_label(p.kind),
        "text": p.text,
        "line": ev.get("line", ""),
        "from": frm,
        "confidence": CONFIDENCE.get(ev.get("confidence", ""), "Medium"),
        "status": p.status,
    }


def origin_of(q: Question, in_briefs: set[str]) -> Origin:
    sub = q.subject or {}
    if q.kind == "unmapped_plant":
        return Origin("Metric gap", "METRIC GAP")
    if sub.get("finding") and sub["finding"] in in_briefs:
        return Origin("Morning Brief", "MORNING BRIEF")
    return Origin("Sweep", "SWEEP")


def todays_brief_fps(s: Session, run: Run | None) -> set[str]:
    if run is None:
        return set()
    fps: set[str] = set()
    for i in s.scalars(sa.select(BriefIssue).where(BriefIssue.run_id == run.id)):
        fps |= {it["fingerprint"] for it in i.body.get("items") or []}
    return fps


def question_rows(qs: list[Question], in_briefs: set[str]) -> list[dict[str, Any]]:
    return [{"id": str(q.id), "text": q.text, "origin": origin_of(q, in_briefs).label} for q in qs]


# --- the page --------------------------------------------------------------------------------


def _todo(s: Session, today: dt.date) -> Any:
    """What to do today (Morning Brief 2.5, #110): the actions across today's briefs."""
    from othor.web import todo_view

    return todo_view.page(s, today, back="home")


def _to_forecasts(outlook: dict[str, Any] | None) -> dict[str, Any] | None:
    """#109 (owner, 22 Nov 2026): with the Forecasts page installed, Next 4 weeks' cards open it;
    without it they keep their panel on Home."""
    from othor.core import plugins

    if not outlook or not any(n.key == "forecasts" for n in plugins.nav("company")):
        return outlook
    weeks = [{**w, "href": "/forecasts"} if w.get("href") else w for w in outlook["weeks"]]
    return {**outlook, "weeks": weeks, "href": "/forecasts" if outlook.get("href") else None}


def page(
    s: Session, settings: Settings, company: Company, today: dt.date, now: dt.datetime
) -> dict[str, Any]:
    run = latest_run(s)
    spec: dict[str, Any] = {}
    if run is not None:
        from othor.ontology.models import Spec

        row = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).first()
        spec = dict(row.compiled) if row else {}
    props = proposal_rows(s)
    qs = answers.queue(s, today)
    rv = last_detecting(s, settings, today)
    cards = brief_cards(s, run, company, now)
    fps = todays_brief_fps(s, run)
    version = service.current_number(s)
    counts = []
    if props:
        counts.append(f"{fmt.num(len(props))} proposal{'s' if len(props) != 1 else ''} to review")
    if qs:
        counts.append(f"{fmt.num(len(qs))} question{'s' if len(qs) != 1 else ''}")
    if not counts:
        counts.append("Nothing to review")
    counts.append(f"Business Model v{version}")
    updated = None
    if run is not None and run.ended_at is not None:
        end = local_now(company, run.ended_at)
        day = "today" if end.date() == today else fmt.date_short(end.date())
        updated = f"Updated {day} {fmt.clock(end.time())}"
    order = answers.todays_order(s, today, _tz(company))
    sources = source_rows(s, settings, rv.run if rv is not None else None)
    checks = (
        checks_strip(
            rv,
            len(qs),
            len(props),
            len(answers.set_aside(s, today)),
            splits=next((p["text"] for p in props if p["kind"] == KIND_LABEL["breakdowns"]), ""),
            pending=_pending_needs(s),
        )
        if rv is not None
        else None
    )
    return {
        # the lights (owner, 16 Nov 2026), first under the title; the full list in a panel
        "lights": _lights(s, settings, company, run, rv, sources, checks, cards, spec, now),
        # the free trial (open sign-up, owner 16 Nov 2026): days left, amber for the last 3
        "trial": trial(company, now, settings),
        "questions_left": questions_summary(order, today),
        "decided_left": decided_summary(s),
        "run": run,
        "counts": " · ".join(counts),
        "updated": updated,
        "banners": stale_banners(s, run, spec),
        "notices": recipient_notices(s, now),
        "cards": cards,
        "briefs": brief_row(cards),
        "sweep": sweep_card(s, rv),
        "swept": sweep_box(s, rv),
        # v0.12: the company's weather, from the run the checks strip reads
        "weather": weather_view.banner(settings, rv.run if rv is not None else None),
        "outlook": _to_forecasts(
            weather_view.outlook(settings, rv.run if rv is not None else None)
        ),
        # What to do (Morning Brief 2.5, #110): today's first steps across the briefs
        "todo": _todo(s, today),
        "weather_run": rv.run if rv is not None else None,
        "checks": checks,
        "proposals": props,
        "sources": sources,
        "questions": question_rows(qs[:3], fps),
        "question_count": len(qs),
        "version": version,
    }


def _lights(
    s: Session,
    settings: Settings,
    company: Company,
    run: Run | None,
    rv: Any,
    sources: list[dict[str, Any]],
    checks: dict[str, Any] | None,
    cards: list[dict[str, Any]],
    spec: dict[str, Any],
    now: dt.datetime,
) -> dict[str, Any] | None:
    """Home's lights for the latest run (`lights_view`)."""
    if run is None:
        return None
    from othor.engine.store import RunFiles, read_json
    from othor.web import lights_view

    files = RunFiles(settings.runs_dir(run.company_id), run.id)
    outputs = read_json(files.outputs_json) or {}
    facts = lights_view.gather(s, company, run, rv, sources, checks, cards, outputs, spec, now)
    return lights_view.lights(facts)


# --- Home's Data sources table (#57, owner 14 Nov 2026) ---------------------------------------


def source_status(name: str, facts: dict[str, Any] | None) -> tuple[str, str, str]:
    """(status, dot, newest data) for one source, from the run's stored weather facts: whether
    it was read, and whether it had rows for yesterday (`weather.freshness`)."""
    if facts is None:
        return ("Not read yet", "muted", "–")
    f = (facts.get("sources_fresh") or {}).get(name)
    newest = fmt.day_month(dt.date.fromisoformat(f["newest"])) if f and f.get("newest") else "–"
    if name in (facts.get("unread") or []):
        return ("Couldn’t be read", "neg", newest)
    if f is None:
        return ("Not used yet", "muted", "–")
    if f.get("newest") is None:
        return ("No daily numbers", "muted", "–")
    if f.get("updated"):
        return ("Updated", "ok", newest)
    # owner, 23 Nov 2026: no rows yesterday on a day this source is usually quiet (a sales
    # warehouse on Sundays, a sheet updated weekly) is not a gap: no warning, as Learn › Home says.
    # Runs stored before `late` was kept say nothing about the usual days, so they still warn.
    if f.get("late") is False:
        return ("Up to date", "ok", newest)
    # #82 (owner, 18 Nov 2026): newest data older than the last read on a day it usually has rows:
    # a warning dot and the date it stopped at
    return (f"Not updated since {newest}", "warn", newest)


NEWEST_WHY = {  # #82: why a source shows no newest date, said instead of a bare dash
    "Not used yet": "No number reads it yet",
    "No daily numbers": "Its numbers carry no daily dates",
    "Not read yet": "Read for the first time tonight",
}


def source_rows(s: Session, settings: Settings, run: Any) -> list[dict[str, Any]]:
    """One row per connected source: its name and kind, its update status from the run Home
    reads, and when it was last read."""
    from othor.web import sources_view as srcv

    got = weather_view._stored(settings, run)
    facts = got[1].get("facts") if got is not None else None
    rows = []
    for src in srcv.sources(s):  # the same list as the Data sources page
        if src.status not in ("connected", "paused", "disconnected"):
            continue
        card = srcv.source_card(s, src)
        status, dot, newest = source_status(src.name, facts)
        rows.append(
            {
                "id": str(src.id),
                "name": src.name,
                "state": src.status,
                "kind": src.kind,
                "title": card.title,
                "status": status,
                "dot": dot,
                "newest": newest,
                "newest_why": NEWEST_WHY.get(status, "") if newest == "–" else "",
                "last_read": card.last_read,
            }
        )
    return rows


# --- the questions panel (3aa; v0.11, docs/plans/questions-panel.md part 1) -------------------

DONT_HAVE = "We don't have this"
CANT_DECIDE = "Othor AI can't decide this one yet."
SWEEP_COUNT = re.compile(r"\b(a|\d+) Sweep tests?\b")
# notes that ask the owner to type the answer (the box opens with them)
TYPE_NOTES = {
    "Type the dates it runs, below.",
    "Type the plant's name below.",
    "Type the plant's name only.",
    "Type your answer below.",
}


def panel_page(src: str | None = None) -> str:
    """The page the questions panel sits on: Home, or Sweep at one run when `src` is
    "sweep-<run>" (M15.2: "Answer 1 question" opens in place on Sweep). Anything else is Home,
    so the value can never send the owner elsewhere."""
    if src and re.fullmatch(r"sweep-[0-9]{1,9}", src):
        return f"/sweep?run={src.split('-', 1)[1]}"
    return "/home"


def panel_url_id(qid: Any, src: str | None = None) -> str:
    """A question's own URL in the panel (Back and Next link to it)."""
    page = panel_page(src)
    return f"{page}{'&' if '?' in page else '?'}panel=othor&subject=questions"


def panel_url(q: Question, src: str | None = None) -> str:
    return panel_url_id(q.id, src)


def why_parts(why: str) -> dict[str, str] | None:
    """The why line, with its count of Sweep tests linked to Sweep's Coverage where it says one
    ("Without it, 2 Sweep tests could not run last night")."""
    if not why:
        return None
    m = SWEEP_COUNT.search(why)
    if m is None:
        return {"pre": why, "link": "", "href": "", "post": ""}
    return {
        "pre": why[: m.start()],
        "link": m.group(0),
        "href": "/sweep#detectors",
        "post": why[m.end() :],
    }


def questions_summary(order: list[Question], today: dt.date) -> str:
    """What's left after the panel ("3 answered · 1 for later"), for Home's Questions box."""
    done = sum(1 for q in order if q.status == "answered")
    later = sum(1 for q in order if answers.set_aside_today(q, today))
    parts = []
    if done:
        parts.append(f"{fmt.num(done)} answered")
    if later:
        parts.append(f"{fmt.num(later)} for later")
    return " · ".join(parts)


def decided_summary(s: Session) -> str:
    """Home's quiet count of decisions by Othor AI to check (v0.12): "2 decisions by Othor AI
    to check"; empty when there are none."""
    n = len(answers.decided_records(s))
    if not n:
        return ""
    return f"{fmt.num(n)} {'decision' if n == 1 else 'decisions'} by Othor AI to check"


def next_open_after(order: list[Question], q: Question, today: dt.date) -> Question | None:
    """Where Ask me later moves on to: the next question still waiting after this one, then
    from the start; None when none is left (the panel closes)."""
    ids = [x.id for x in order]
    at = ids.index(q.id) if q.id in ids else -1
    for x in order[at + 1 :] + order[: max(at, 0)]:
        if x.id != q.id and answers.is_open(x, today):
            return x
    return None


def questions_panel(
    s: Session,
    company: Company,
    today: dt.date,
    *,
    current: Question | None = None,
    follow_up: list[str] | None = None,
    note: str | None = None,
    prefill: str = "",
    src: str | None = None,
) -> dict[str, Any]:
    """One question per screen: the step line and the segments, the question with its source
    tag and why, its replies as radio cards, the answer box; a waiting draft with Apply and Edit;
    an answered question with what it did, Undo and Change answer; Back and Next (Done on the
    last). Every word and number comes from the question and its records."""
    run = latest_run(s)
    fps = todays_brief_fps(s, run)
    order = answers.todays_order(s, today, _tz(company))
    if not order:
        return {"empty": True}
    ids = [x.id for x in order]
    if current is not None and current.id in ids:
        at = ids.index(current.id)
    else:
        # #54: the first waiting question, else the first set aside (still answerable, as Home
        # says); never one already answered: with every question answered, nothing is left
        at = next((k for k, x in enumerate(order) if answers.is_open(x, today)), -1)
        if at < 0:
            at = next((k for k, x in enumerate(order) if x.status == "skipped"), -1)
        if at < 0:
            return {"empty": True}
    q = order[at]
    answered = sum(1 for x in order if x.status == "answered")
    segs = [
        " ".join(
            c for c in ("done" if x.status == "answered" else "", "now" if k == at else "") if c
        )
        for k, x in enumerate(order)
    ]
    need = str((q.subject or {}).get("needs") or "") if q.kind == "needs" else ""
    view: dict[str, Any] = {
        "id": str(q.id),
        "tag": origin_of(q, fps).upper,
        # v0.12: the owner's terms and what the check does, also on a question raised before
        # (no count of Sweep tests, #33)
        "text": NEEDS_QUESTIONS.get(need, q.text),
        "why": why_parts(NEEDS_EXPLAIN.get(need, q.why)),
        "note": None if note == CANT_DECIDE else note,
    }
    if q.status == "answered":
        rec = (
            s.scalars(
                sa.select(OntologyRecord).where(OntologyRecord.id == q.answer_record_id)
            ).first()
            if q.answer_record_id
            else None
        )
        ref = (rec.source_ref if rec is not None else None) or {}
        by_ai = ref.get("decided_by") == answers.DECIDED_BY
        view |= {
            "state": "answered",
            "answer": q.answer_text or "",
            "line": answers.record_line(rec),
            "did": answers.confirmation(q, rec),
            "version": q.answer_version,
            # Let Othor AI decide: tagged, its computed evidence, capped until confirmed
            "decided": {
                "evidence": str(ref.get("evidence") or ""),
                "cap": "Confidence capped at Medium until you confirm it on the Business Model.",
            }
            if by_ai
            else None,
        }
    elif q.draft:
        view |= {
            "state": "draft",
            "said": str(q.draft.get("text") or ""),
            "picked": bool(q.draft.get("picked")),
            "section": answers.draft_section(q),
            "lines": answers.draft_lines(s, q),
            "effect": answers.draft_effect(q),
        }
    else:
        replies = list(follow_up or [r for r in (q.quick_replies or []) if r != DONT_HAVE])
        free = q.kind != "needs_confirmation" and not follow_up
        cards = [
            {"value": answers.PICK + str(c["key"]), "label": c["label"], "sub": c["sub"]}
            for c in answers.suggestions(q)
        ]
        d = answers.decision(q)
        found = {"why": d.get("why") or "", "next": d.get("next") or "", "href": d.get("href")}
        view |= {
            "state": "ask",
            "replies": replies,
            "cards": cards,
            # Let Othor AI decide (v0.12): on the questions it can answer from the data
            "decide": q.kind == "needs" and not follow_up,
            # nothing found: say so and give the next step, no empty cards
            "nothing": found if q.kind == "needs" and not cards and found["why"] else None,
            # it couldn't decide: why, in one line, and the next step
            "cant": found if note == CANT_DECIDE and found["why"] else None,
            "free": free,
            # the box shows when there are no replies, when Othor AI asked for it typed, or
            # when Edit brought a draft's text back
            "typing": free and ((not replies and not cards) or bool(prefill) or note in TYPE_NOTES),
            "prefill": prefill,
            "dont_have": not follow_up and DONT_HAVE in (q.quick_replies or []),
            "later": q.skipped_until
            if q.status == "skipped" and q.skipped_until and q.skipped_until > today
            else None,
        }
    return {
        "empty": False,
        "at": at + 1,
        "total": len(order),
        "answered": answered,
        "segs": segs,
        "q": view,
        "back": panel_url(order[at - 1], src) if at > 0 else None,
        "next": panel_url(order[at + 1], src) if at < len(order) - 1 else None,
    }


def _tz(company: Company) -> Any:
    from zoneinfo import ZoneInfo

    return ZoneInfo(company.timezone)


def trial(company: Any, now: Any, settings: Any) -> dict[str, Any] | None:
    """A company on open sign-up's free trial: days left (counting today), and where to write."""
    ends = getattr(company, "trial_ends_at", None)
    if ends is None:
        return None
    left = max(0, -(-int((ends - now).total_seconds()) // 86400))
    from othor.core import contact

    return {
        "left": left,
        "urgent": left <= 3,
        "support": settings.support_email,
        "form": contact.available(settings),  # Contact us in the side panel (17 Nov 2026)
    }


def intelligence_share(
    s: Session, settings: Settings, today: dt.date, unlock: set[str] | None = None
) -> int | None:
    """The top bar's Intelligence share, as `checks_strip` counts it; with `unlock`, the same
    with those waiting checks counted as able to run (0.26.2: what applying answers buys; the
    night confirms it). None before the first run."""
    runs = sv.detecting_runs(s)
    rv = sv.load(s, settings, runs[0], today) if runs else None
    if rv is None:
        return None
    cov = {c["detector"]: c for c in rv.doc.get("coverage", [])}
    total = ran = 0
    for name in sv.shipped():
        c = cov.get(name)
        if c is None:
            continue
        total += 1
        if c["status"] in ("ran", "partly") or (
            unlock and name in unlock and c.get("needs") in ANSWERABLE
        ):
            ran += 1
    return round(100 * ran / total) if total else None
