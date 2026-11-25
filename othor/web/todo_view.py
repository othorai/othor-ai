# SPDX-License-Identifier: Apache-2.0
"""What to do (Morning Brief 2.5, #110; docs/plans/what-to-do.md § C, D): the rows a brief's
"What to do" section, Home's "What to do today" card and the What to do page draw. Every value
is the stored issue's (`actions`, built by `othor.briefs.actions` at the run); this only adds
what changes after the run: the owner's holds (Snoozed until 8 Nov, Muted), a watch already
following the number, and where each link goes."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from markupsafe import Markup, escape
from sqlalchemy.orm import Session

from othor.briefs import words
from othor.briefs.models import Brief, BriefIssue
from othor.core import fmt
from othor.core.tenancy import Repo
from othor.engine.models import Run

START = 3  # "Start here": the first three across the briefs
HOME = 5  # Home's card: the first five

# a figure in an action's sentence, as code wrote it (rule 1): money, a share, a count with its
# unit, a day of the month. Bold on the page (0.33.1, the owner's approved design)
_FIGURE = re.compile(
    r"(?:[₹$€£]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:Cr|L|M|K|B))?"
    r"|\d{1,2}\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b"
    r"|\d[\d,]*(?:\.\d+)?(?:%|\+)?(?:\s(?:days?|d|customers|weeks?|months?)\b)?)"
)


def bold_figures(text: str) -> Markup:
    """The sentence with its figures in bold, escaped first (a sentence never carries markup)."""
    out, last = [], 0
    for m in _FIGURE.finditer(text):
        out.append(escape(text[last : m.start()]))
        out.append(Markup("<b>{}</b>").format(m.group(0)))
        last = m.end()
    out.append(escape(text[last:]))
    return Markup("").join(out)


def watch_href(back: str, fp: str, run: int) -> str | None:
    """Watch this on the page it's pressed on (0.33.1): the page's own address with
    ?panel=watch, so the panel opens in place and Close and Start watching stay there."""
    from urllib.parse import urlencode

    from othor.web.routes.sweep import back_to

    to = back_to(back)
    if to is None:
        return None
    return to + "?" + urlencode({"panel": "watch", "fp": fp, "run": run})


def action_plan_href(brief_id: Any) -> str:
    """A brief's own Action plan, on its signed-in page (#110, owner 25 Nov 2026)."""
    return f"/brief/{brief_id}#action-plan"


def _held(s: Session, today: dt.date) -> dict[str, str]:
    from othor.web.brief_view import held

    return held(s, today)


def _units(s: Session, run: Run | None) -> dict[str, str]:
    if run is None:
        return {}
    from othor.ontology.models import Spec

    sp = Repo(s).get(Spec, run.spec_id)
    body = (sp.compiled if sp is not None else None) or {}
    return {k: str(m.get("unit") or "") for k, m in (body.get("metrics") or {}).items()}


def _watching(s: Session, a: dict[str, Any], units: dict[str, str]) -> str | None:
    from othor.engine import watches

    if a.get("kind") != "finding" or not a.get("fingerprints"):
        return None
    w = watches.covering(
        s,
        {
            "fingerprint": a["fingerprints"][0],
            "metric": a.get("metric"),
            "detector": a.get("detector"),
            "entities": (a.get("watch") or {}).get("entities") or [],
            "breakdown_value": (a.get("watch") or {}).get("breakdown_value"),
        },
    )
    if w is None:
        return None
    unit = units.get(str((w.indicator or {}).get("metric") or a.get("metric") or ""))
    return "Watching · " + watches.label(w, unit or None)


def card_watch(s: Session, run: Run | None, fps: list[str], back: str) -> dict[str, dict[str, Any]]:
    """Watch on a brief's item cards (0.33.1, the owner's decision of 22 Nov): per fingerprint,
    the label of a watch already following its number ("Watching · alert above 8 · …"), else the
    in-place Watch link where a watch can be drafted (`watches.can_draft`), from the run's stored
    findings. Nothing for a read-only page (no run)."""
    if run is None or not fps:
        return {}
    from othor.engine import watches
    from othor.engine.models import Finding

    rows = s.scalars(
        sa.select(Finding).where(Finding.run_id == run.id, Finding.fingerprint.in_(fps))
    ).all()
    units = _units(s, run)
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        f = {**(r.body or {}), "fingerprint": r.fingerprint}
        w = watches.covering(s, f)
        if w is not None:
            unit = units.get(str((w.indicator or {}).get("metric") or f.get("metric") or ""))
            out[r.fingerprint] = {"watching": "Watching · " + watches.label(w, unit or None)}
        elif watches.can_draft(f):
            out[r.fingerprint] = {"href": watch_href(back, r.fingerprint, run.number)}
    return out


def _tone(a: dict[str, Any]) -> str:
    st = str(a.get("status") or "")
    if a.get("kind") == "goal" or st.endswith("the limit"):
        return "off"
    return "new" if st.lower().startswith("new") else "open"


def rows(
    s: Session,
    shown: list[dict[str, Any]],
    *,
    today: dt.date,
    run: int | None,
    back: str,
    finding_href: str,
    units: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """One row per action, numbered. `finding_href`: the link to a finding, with "{fp}" in it;
    `back`: where a hold or a watch returns (sweep.back_to's keys); `run`: the run number the
    hold routes look the finding up in (None: no buttons, a read-only page)."""
    held = _held(s, today) if run is not None else {}
    units = units or {}
    out = []
    for n, a in enumerate(shown, start=1):
        fps = list(a.get("fingerprints") or [])
        ids = a.get("brief_ids") or {}
        why = []
        for w in a.get("why") or []:
            if w.get("fingerprint"):
                href = finding_href.replace("{fp}", str(w["fingerprint"]))
            elif w.get("goal"):
                href = "/summary"
            else:
                href = "/sweep?tab=watching"
            why.append({"title": str(w.get("title") or ""), "href": href})
        who = a.get("who") or None
        said = str(a.get("said") or a.get("text") or "")
        # the sentence already names who to ask: the row doesn't say it twice (0.33.1)
        if who and who.get("name") and str(who["name"]) in said:
            who = None
        watching = _watching(s, a, units) if run is not None else None
        can_watch = (
            run is not None
            and a.get("kind") == "finding"
            and bool((a.get("watch") or {}).get("can"))
            and watching is None
            and bool(fps)
        )
        out.append(
            {
                "n": n,
                "key": a.get("key"),
                "said": said,
                "said_html": bold_figures(said),
                "status": str(a.get("status") or ""),
                "tone": _tone(a),
                "why": why,
                "who": ", ".join(x for x in (who.get("name"), who.get("role")) if x) if who else "",
                "by": a.get("by") or "",
                "money": a.get("money") or "",
                "money_note": a.get("money_note") or "",
                "briefs": list(a.get("briefs") or []),
                # #110 (owner, 25 Nov 2026): each brief it is in, a link to that brief's own
                # Action plan (Home's card and the Action plan page; a brief's own rows have none)
                "brief_links": [
                    {"name": b, "href": action_plan_href(ids[b])}
                    for b in a.get("briefs") or []
                    if b in ids
                ],
                "fp": fps[0] if fps else None,
                "also": ",".join(fps[1:]),
                "holdable": run is not None and a.get("kind") != "goal" and bool(fps),
                "held": next((held[fp] for fp in fps if fp in held), None),
                "watching": watching,
                # Watch this in place on this page (0.33.1): the page shows the panel itself
                "watch_href": (
                    watch_href(back, fps[0], run) if can_watch and run is not None else None
                ),
                "run": run,
                "back": back,
            }
        )
    return out


def for_issue(
    s: Session,
    issue: BriefIssue | None,
    run: Run | None,
    today: dt.date,
    *,
    base: str,
    q: str,
    back: str,
    readonly: bool = False,
) -> list[dict[str, Any]]:
    """A brief's own "What to do" section: its issue's actions (at most three)."""
    if issue is None:
        return []
    shown = words.actions(issue.body, issue.prose)
    if not shown:
        return []
    return rows(
        s,
        shown,
        today=today,
        run=None if readonly or run is None else run.number,
        back=back,
        finding_href=f"{base}/finding/{{fp}}?{q}",
        units=_units(s, run),
    )


@dataclass
class Page:
    """The What to do page (and Home's card): today's actions across the briefs."""

    date: dt.date | None = None
    time: str = ""
    briefs: list[tuple[str, int]] = field(default_factory=list)  # (name, count) for the tabs
    # #110 (owner, 25 Nov 2026): each brief's name to its own Action plan
    links: dict[str, str] = field(default_factory=dict)
    start: list[dict[str, Any]] = field(default_factory=list)
    also: list[dict[str, Any]] = field(default_factory=list)
    nothing: list[str] = field(default_factory=list)
    held: dict[str, int] = field(default_factory=dict)  # {"snoozed": n, "muted": n}
    # Recently resolved (owner, 22 Nov 2026): the last 30 days' actions whose reason is gone
    resolved: list[dict[str, Any]] = field(default_factory=list)
    resolved_week: int = 0

    @property
    def all(self) -> list[dict[str, Any]]:
        return self.start + self.also

    @property
    def held_line(self) -> str:
        n, m = self.held.get("snoozed", 0), self.held.get("muted", 0)
        if n and m:
            return f"{fmt.num(n + m)} snoozed or muted"
        if n:
            return f"{fmt.num(n)} snoozed"
        return f"{fmt.num(m)} muted" if m else ""


SHOWN = ("ready", "rerun")  # a brief issue a reader sees (failed ones are not)


def _latest(s: Session) -> list[tuple[Brief, BriefIssue]]:
    """Each active brief's newest issue on the newest day any brief has one: the 06:00 issue or a
    later Run again now's, as Home and the brief cards show (22 Nov 2026: the page read the 06:00
    issue and said "Nothing needs you today" beside a rerun's three actions)."""
    briefs = list(
        s.scalars(
            sa.select(Brief)
            .where(Brief.deleted_at.is_(None), Brief.status == "active")
            .order_by(Brief.is_default.desc(), Brief.position, Brief.created_at)
        )
    )
    day = s.scalar(
        sa.select(sa.func.max(BriefIssue.date)).where(
            BriefIssue.status.in_(SHOWN), BriefIssue.brief_id.in_([b.id for b in briefs] or [None])
        )
    )
    if day is None:
        return []
    out = []
    for b in briefs:
        i = s.scalars(
            sa.select(BriefIssue)
            .where(
                BriefIssue.brief_id == b.id, BriefIssue.date == day, BriefIssue.status.in_(SHOWN)
            )
            .order_by(BriefIssue.created_at.desc())
            .limit(1)
        ).first()
        if i is not None:
            out.append((b, i))
    return out


def page(s: Session, today: dt.date, back: str = "action-plan") -> Page:
    """Today's actions across the briefs; `back`: where the buttons return ("home" on Home)."""
    from othor.engine import holds

    got = _latest(s)
    p = Page()
    snoozes, mutes = holds.active(s, today)
    p.held = {"snoozed": len(snoozes), "muted": len(mutes)}
    if not got:
        return p
    p.date = got[0][1].date
    p.time = str(got[0][1].body.get("time") or "")
    merged: dict[str, dict[str, Any]] = {}
    runs: dict[str, Run | None] = {}
    counts: dict[str, int] = {}
    nothing: list[str] = []
    for b, issue in got:
        for g in issue.body.get("goals") or []:
            if g.get("tone") == "ok":
                line = f"On track: {g['text']}."
                if line not in nothing:
                    nothing.append(line)
        note = issue.body.get("ahead_note")
        if note and note not in nothing:
            nothing.append(str(note))
        for a in words.actions(issue.body, issue.prose):
            key = a["key"] if a.get("kind") != "finding" else "|".join(sorted(a["fingerprints"]))
            if key not in merged:
                merged[key] = {**a, "briefs": [], "brief_ids": {}}
                runs[key] = Repo(s).get(Run, issue.run_id)
            if b.name not in merged[key]["briefs"]:
                merged[key]["briefs"].append(b.name)
                merged[key]["brief_ids"][b.name] = b.id
                counts[b.name] = counts.get(b.name, 0) + 1
    p.briefs = [(b.name, counts[b.name]) for b, _ in got if counts.get(b.name)]
    p.links = {b.name: action_plan_href(b.id) for b, _ in got if counts.get(b.name)}
    p.nothing = nothing
    units = _units(s, next(iter(runs.values()), None))
    all_rows = []
    for key, a in merged.items():
        run = runs[key]
        (r,) = rows(
            s,
            [a],
            today=today,
            run=run.number if run is not None else None,
            back=back,
            finding_href=f"/sweep/finding/{{fp}}?run={run.number if run else ''}",
            units=units,
        )
        all_rows.append(r)
    for i, r in enumerate(all_rows, start=1):
        r["n"] = i
    p.start, p.also = all_rows[:START], all_rows[START:]
    p.resolved = history(s, today, got)
    week = today - dt.timedelta(days=6)
    p.resolved_week = sum(1 for r in p.resolved if r["on_date"] >= week)
    return p


HISTORY_DAYS = 90  # how far back an action's first day is looked for


def _seen(
    s: Session, today: dt.date, names: dict[Any, str]
) -> tuple[dict[str, dict[str, Any]], list[dt.date]]:
    """Every action the stored issues of the last HISTORY_DAYS carried, by key: the last one
    shown, its first and last day, the findings it held, the briefs it was in."""
    issues = s.scalars(
        sa.select(BriefIssue)
        .where(
            BriefIssue.brief_id.in_(list(names)),
            BriefIssue.status.in_(SHOWN),
            BriefIssue.date >= today - dt.timedelta(days=HISTORY_DAYS),
        )
        .order_by(BriefIssue.date, BriefIssue.created_at)
    ).all()
    newest: dict[tuple[Any, dt.date], BriefIssue] = {}
    for i in issues:  # a rerun's issue replaces the 06:00 one of its day
        newest[(i.brief_id, i.date)] = i
    seen: dict[str, dict[str, Any]] = {}
    for (bid, day), i in sorted(newest.items(), key=lambda kv: (kv[0][1], str(kv[0][0]))):
        for a in words.actions(i.body, i.prose):
            key = str(a.get("key") or "")
            if not key:
                continue
            e = seen.setdefault(key, {"a": a, "first": day, "last": day, "fps": [], "briefs": []})
            e["a"], e["last"] = a, max(e["last"], day)
            for fp in a.get("fingerprints") or []:
                if fp not in e["fps"]:
                    e["fps"].append(fp)
            if names[bid] not in e["briefs"]:
                e["briefs"].append(names[bid])
    return seen, sorted({d for _, d in newest})


def _findings_now(s: Session, fps: list[str]) -> dict[str, Any]:
    """Each finding's last stored row (`resolved.FindingNow`): open, resolved, covered or merged,
    with the day and Sweep's reason."""
    from othor.briefs.resolved import FindingNow
    from othor.engine.models import Finding

    out: dict[str, Any] = {}
    if not fps:
        return out
    got = s.execute(
        sa.select(Finding, Run.number, Run.run_date)
        .join(Run, Run.id == Finding.run_id)
        .where(Finding.fingerprint.in_(fps))
        .order_by(Run.number)
    ).all()
    for f, number, run_date in got:  # the last stored row wins
        body = f.body or {}
        status = str(body.get("status") or f.status)
        res = body.get("resolved") or {}
        on = dt.date.fromisoformat(res["date"]) if res.get("date") else run_date
        out[f.fingerprint] = FindingNow(
            status, None if status == "open" else on, str(res.get("reason") or ""), number
        )
    return out


def _holds_now(s: Session, today: dt.date) -> dict[str, Any]:
    from othor.briefs.resolved import Hold
    from othor.engine.models import Mute, Snooze

    out: dict[str, Any] = {}
    for sn in s.scalars(sa.select(Snooze).where(Snooze.ended_at.is_(None), Snooze.until >= today)):
        out[sn.fingerprint] = Hold("snoozed", sn.created_at.date(), sn.until)
    for mu in s.scalars(sa.select(Mute).where(Mute.ended_at.is_(None))):
        out[mu.fingerprint] = Hold("muted", mu.created_at.date(), None)
    return out


def _watches_now(s: Session, keys: list[str]) -> dict[str, tuple[str, dt.date | None]]:
    import uuid

    from othor.engine.models import Watch

    ids = []
    for k in keys:
        try:
            ids.append(uuid.UUID(k.removeprefix("watch:")))
        except ValueError:
            continue
    if not ids:
        return {}
    return {
        str(w.id): (w.status, w.stopped_at.date() if w.stopped_at else None)
        for w in s.scalars(sa.select(Watch).where(Watch.id.in_(ids)))
    }


def history(
    s: Session, today: dt.date, got: list[tuple[Brief, BriefIssue]]
) -> list[dict[str, Any]]:
    """Recently resolved (owner, 22 Nov 2026): every action the stored issues carried, against
    today's issues and the state of its reason now (`othor.briefs.resolved`, rule 1: decided by
    code). Read from stored rows only: issues, findings, holds, today's goals, watches."""
    from othor.briefs import resolved as rs

    if not got:
        return []
    seen, days = _seen(s, today, {b.id: b.name for b, _ in got})
    today_keys: set[str] = set()
    today_fps: set[str] = set()
    goals: dict[str, tuple[str, bool]] = {}
    for _b, i in got:
        for a in i.body.get("actions") or []:
            today_keys.add(str(a.get("key") or ""))
            today_fps.update(a.get("fingerprints") or [])
        for g in i.body.get("goals") or []:
            goals[str(g.get("key"))] = (str(g.get("tone") or ""), bool(g.get("on_track")))
    items = [
        rs.Seen(
            key=k,
            kind=str(e["a"].get("kind") or ""),
            fingerprints=tuple(e["fps"]),
            said=str(e["a"].get("said") or e["a"].get("text") or ""),
            first=e["first"],
            last=e["last"],
            briefs=tuple(e["briefs"]),
            goal=next((w["goal"] for w in e["a"].get("why") or [] if w.get("goal")), None),
            watch=k.removeprefix("watch:") if k.startswith("watch:") else None,
        )
        for k, e in seen.items()
    ]
    got_resolved = rs.resolve(
        items,
        today_keys=today_keys,
        today_fps=today_fps,
        findings=_findings_now(s, sorted({fp for e in seen.values() for fp in e["fps"]})),
        holds=_holds_now(s, today),
        goals=goals,
        watches=_watches_now(s, [k for k in seen if k.startswith("watch:")]),
        days=days,
        today=today,
    )
    out = []
    for r in got_resolved:
        if r.fingerprint:
            href = f"/sweep/finding/{r.fingerprint}" + (f"?run={r.run}" if r.run else "")
        elif r.goal:
            href = "/summary"
        else:
            href = "/sweep?tab=watching"
        out.append(
            {
                "key": r.key,
                "said": r.said,
                "said_html": bold_figures(r.said),
                "reason": r.reason,
                "icon": r.icon,
                "open_for": rs.open_for(r),
                "on": fmt.day_month(r.on),
                "on_date": r.on,
                "href": href,
                "briefs": list(r.briefs),
            }
        )
    return out
