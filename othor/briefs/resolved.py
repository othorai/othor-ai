# SPDX-License-Identifier: Apache-2.0
"""Recently resolved, the Action plan's history (owner, 22 Nov 2026): the actions of the last 30
days whose reason is gone, newest first, each with when and why in plain words. Pure: it takes
what the stored brief issues said (`Seen`, one per action key across the days) and the state of
each reason now (a finding's last stored status, the owner's holds, the goals and the watches),
and decides by code (rule 1; nothing from a model). An action that only dropped out of today's
three while its reason is still there is never listed: nothing was resolved."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from othor.core import fmt

WINDOW = 30  # days


@dataclass(frozen=True)
class Seen:
    """One action across the stored issues: its key, the findings it carried (a folded action
    carries two or more), the last sentence shown, and the first and last day it was in a brief."""

    key: str
    kind: str  # finding · goal · watch
    fingerprints: tuple[str, ...]
    said: str
    first: dt.date
    last: dt.date
    briefs: tuple[str, ...]
    goal: str | None = None
    watch: str | None = None


@dataclass(frozen=True)
class FindingNow:
    """A finding's last stored row: open, resolved, covered (a known pattern) or merged."""

    status: str
    on: dt.date | None
    reason: str
    run: int | None


@dataclass(frozen=True)
class Hold:
    kind: str  # snoozed · muted
    on: dt.date
    until: dt.date | None


@dataclass(frozen=True)
class Resolved:
    key: str
    kind: str
    said: str
    first: dt.date
    on: dt.date
    reason: str
    icon: str  # check · eye-off · clock
    briefs: tuple[str, ...]
    fingerprint: str | None = None  # the finding its link opens
    run: int | None = None
    goal: str | None = None
    watch: str | None = None


def plain_reason(status: str, reason: str) -> str:
    """Sweep's stored reason (`findings.resolution`) in the owner's words."""
    if status == "covered":
        return "Matches a known pattern in your Business Model"
    if status == "merged":
        return "Now part of another finding"
    if reason.startswith("Explained by your answer"):
        return reason
    if reason == "Below the materiality floor":
        return "Now below the amount worth reporting"
    if reason == "No longer tested":
        return "No longer checked"
    return "The numbers no longer show it"


def gone_on(days: list[dt.date], last: dt.date) -> dt.date:
    """The first day an issue came out after the action's last day: the day it left."""
    return next((d for d in sorted(days) if d > last), last + dt.timedelta(days=1))


def _finding(
    s: Seen, findings: dict[str, FindingNow], holds: dict[str, Hold], days: list[dt.date]
) -> Resolved | None:
    parts: list[tuple[dt.date, str, str]] = []  # (on, reason, icon), one per finding
    run: int | None = None
    for fp in s.fingerprints:
        h = holds.get(fp)
        now = findings.get(fp)
        if h is not None:
            if h.kind == "muted":
                parts.append((h.on, "You muted it", "eye-off"))
            else:
                until = f" until {fmt.day_month(h.until)}" if h.until else ""
                parts.append((h.on, f"You snoozed it{until}", "clock"))
        elif now is None:
            parts.append((gone_on(days, s.last), "No longer checked", "check"))
        elif now.status == "open":
            return None  # its reason is still there
        else:
            parts.append(
                (now.on or gone_on(days, s.last), plain_reason(now.status, now.reason), "check")
            )
        if now is not None and now.run is not None:
            run = now.run if run is None else max(run, now.run)
    if not parts:
        return None
    on = max(p[0] for p in parts)
    reasons: list[str] = []
    for _, r, _i in sorted(parts, key=lambda p: p[0]):
        if r not in reasons:
            reasons.append(r)
    text = reasons[0] + "".join("; " + r[:1].lower() + r[1:] for r in reasons[1:])
    icons = {p[2] for p in parts}
    icon = icons.pop() if len(icons) == 1 else "check"
    return Resolved(
        s.key,
        s.kind,
        s.said,
        s.first,
        on,
        text,
        icon,
        s.briefs,
        fingerprint=s.fingerprints[0],
        run=run,
    )


def resolve(
    seen: list[Seen],
    *,
    today_keys: set[str],
    today_fps: set[str],
    findings: dict[str, FindingNow],
    holds: dict[str, Hold],
    goals: dict[str, tuple[str, bool]],
    watches: dict[str, tuple[str, dt.date | None]],
    days: list[dt.date],
    today: dt.date,
    window: int = WINDOW,
) -> list[Resolved]:
    """The resolved actions of the last `window` days, newest first. `goals`: today's goals by
    key, (tone, on track); `watches`: by id, (status, the day it was stopped or None)."""
    out: list[Resolved] = []
    for s in seen:
        if s.key in today_keys or set(s.fingerprints) & today_fps:
            continue  # still in today's plan
        r: Resolved | None = None
        if s.kind == "goal" and s.goal:
            g = goals.get(s.goal)
            if g is None:
                reason = "No longer a goal"
            elif g[0] != "off":
                reason = "Back on track" if g[1] else "No longer off track"
            else:
                continue
            r = Resolved(
                s.key,
                s.kind,
                s.said,
                s.first,
                gone_on(days, s.last),
                reason,
                "check",
                s.briefs,
                goal=s.goal,
            )
        elif s.kind == "watch" and s.watch:
            w = watches.get(s.watch)
            if w is None:
                on, reason = gone_on(days, s.last), "No longer watched"
            elif w[1] is not None:
                on, reason = w[1], "You stopped watching it"
            elif w[0] != "breached":
                on, reason = gone_on(days, s.last), "Back inside its limit"
            else:
                continue
            r = Resolved(
                s.key, s.kind, s.said, s.first, on, reason, "check", s.briefs, watch=s.watch
            )
        elif s.fingerprints:
            r = _finding(s, findings, holds, days)
        if r is not None and today - dt.timedelta(days=window) <= r.on <= today:
            out.append(r)
    return sorted(out, key=lambda r: (r.on, r.said), reverse=True)


def open_for(r: Resolved) -> str:
    """ "12 days · 9–21 Nov": how long the action was in the plan, first day to the day it left."""
    days = max((r.on - r.first).days, 1)
    span = (
        fmt.day_month(r.on)
        if r.first == r.on
        else f"{fmt.day_month(r.first)}–{fmt.day_month(r.on)}"
    )
    return f"{fmt.num(days)} day{'' if days == 1 else 's'} · {span}"
