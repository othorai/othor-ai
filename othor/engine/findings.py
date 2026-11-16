# SPDX-License-Identifier: Apache-2.0
"""Findings across nights (docs/04 §4): fingerprints, change tags, Resolved. Pure.

- Fingerprint: detector | series | direction | key entities. A group result (the customers
  silent 90+ days, the top-N of a concentration) leaves its members out, so the same finding
  keeps its fingerprint as members come and go.
- Change tag, against the previous detecting run's findings: New; Worse or Better when the money
  at stake moved by more than 10%; Open · day N otherwise (N counts nights from the first).
- Resolved: an open finding of the previous run that doesn't pass tonight, with the reason
  ("Cleared by data" when its own test no longer fires; "Explained by your answer" when an
  answer stops testing it, an entity pause). Resolved findings stay listed for 7 days (Sweep's
  Resolved tab), then drop out.
- Not Resolved (decided 5 Nov 2026): one now covered by a confirmed known pattern is
  `covered` (Suppressed, under its pattern, with the nights it was open); one absorbed into
  another finding is `merged` (under that finding's "Also seen by"). Neither is counted.

Titles are templates filled with computed values only (non-negotiable 1); M6b words them.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from typing import Any

from othor.engine.detect import state
from othor.engine.detectors.base import Result

GROUP = {"missing_records": "count", "concentration": "top_n"}
MOVED = Decimal("0.10")
RESOLVED_DAYS = 7


def fingerprint(r: Result) -> str:
    group = r.detector in GROUP and (r.detector != "missing_records" or r.state_hint == "measured")
    ents = [] if group else sorted(r.entities)
    parts = [r.detector, *r.series, r.direction, *ents]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _identity(detector: str, series: list[str], entities: list[str], state: str) -> str:
    """A fingerprint without the direction."""
    group = detector in GROUP and (detector != "missing_records" or state == "measured")
    return "|".join([detector, *series, *([] if group else sorted(entities))])


def pattern(r: Result) -> str:
    """What a mute holds: the finding's pattern in any direction."""
    ident = _identity(r.detector, r.series, r.entities, r.state_hint)
    return hashlib.sha256(ident.encode()).hexdigest()[:32]


def pattern_of(f: dict[str, Any]) -> str:
    ident = _identity(f["detector"], f["series"], f["entities"], f.get("state") or "")
    return hashlib.sha256(ident.encode()).hexdigest()[:32]


HELD = ("snoozed", "muted")


def build(
    results: list[Result],
    previous: list[dict[str, Any]],
    run_date: dt.date,
) -> list[dict[str, Any]]:
    prev = {f["fingerprint"]: f for f in previous}
    tonight: dict[str, dict[str, Any]] = {}
    for r in results:
        held = _hold(r.reason)
        if not r.passed and held is None:
            continue
        fp = fingerprint(r)
        st, conf = state(r)
        live = ("open", *HELD)
        was = prev.get(fp, {})
        before = was if was.get("status") in live else None
        # resolved within the Resolved tab's week (e.g. one night under the floor) or covered by
        # a known pattern whose window ended, and firing again: it comes back with its history,
        # never "New" (the 100-night check, 13 Nov 2026)
        back = was if was.get("status") in ("resolved", "covered") else None
        kept = before or back
        first = kept["first_seen"] if kept else {"date": run_date.isoformat()}
        days = (run_date - dt.date.fromisoformat(first["date"])).days + 1
        tag = CAME_BACK if back is not None else _tag(before, r, days)
        tonight[fp] = {
            "fingerprint": fp,
            "status": "open",
            "detector": r.detector,
            "version": r.version,
            "family": r.family,
            "metric": r.metric,
            "breakdown": r.breakdown,
            "breakdown_value": r.breakdown_value,
            "series": r.series,
            "entities": sorted(r.entities),
            "direction": r.direction,
            "state": st,
            "confidence": conf,
            "money": r.money,
            "money_basis": r.money_basis,
            "p_value": r.p_value,
            "q_value": r.q_value,
            "window": [r.window[0].isoformat(), r.window[1].isoformat()],
            "reconstructed_only": r.reconstructed_only,
            "slots": r.slots,
            "evidence": r.evidence[:50],
            "reasons": r.reasons,
            "first_seen": first,
            "days_open": days,
            "change": tag,
        }
        if held is not None:  # the owner's snooze or mute: Suppressed, not Resolved
            tonight[fp]["status"] = held[0]
            if held[1]:
                tonight[fp]["snoozed_until"] = held[1]
    by_fp = {fingerprint(r): r for r in results}
    # tonight's result for an earlier finding whatever its direction (one that doesn't fire
    # has none): the reason it ended comes from it
    by_id = {_identity(r.detector, r.series, r.entities, r.state_hint): r for r in results}
    for fp, f in prev.items():
        if fp not in by_fp:
            got = by_id.get(
                _identity(f["detector"], f["series"], f["entities"], f.get("state") or "")
            )
            if got is not None:
                by_fp[fp] = got
    for fp, f in sorted(prev.items()):
        if fp in tonight:
            continue
        into = _absorbed(f, results) if by_fp.get(fp) is None else None
        left = _left(f, by_fp.get(fp), run_date, into)
        if left is not None:
            tonight[fp] = left
            continue
        if f.get("status") == "open":
            days = (run_date - dt.date.fromisoformat(f["first_seen"]["date"])).days
            tonight[fp] = {
                **f,
                "days_open": days,
                "status": "resolved",
                "change": "Resolved",
                "resolved": {"date": run_date.isoformat(), "reason": resolution(by_fp.get(fp))},
            }
        elif f.get("status") == "resolved":
            when = dt.date.fromisoformat(f["resolved"]["date"])
            if (run_date - when).days < RESOLVED_DAYS:
                tonight[fp] = {**f, "change": "Resolved"}
    return [tonight[k] for k in sorted(tonight, key=lambda k: _order(tonight[k]))]


def _left(
    f: dict[str, Any], r: Result | None, run_date: dt.date, into: str | None = None
) -> dict[str, Any] | None:
    """An earlier finding that left Findings without being resolved (decided 5 Nov 2026):
    covered by a confirmed known pattern (Suppressed, listed once under the pattern, with the
    nights it was open) or merged into another finding (under that finding's "Also seen by").
    Carried while tonight's result still says so; never counted, never Resolved."""
    if f.get("status") not in ("open", "covered", "merged") or (r is None and not into):
        return None
    why = (r.reason or "") if r is not None else ""
    first = dt.date.fromisoformat(f["first_seen"]["date"])
    if f.get("status") == "open":
        last = first + dt.timedelta(days=max(int(f.get("days_open") or 1), 1) - 1)
        span: dict[str, Any] = {"open_from": first.isoformat(), "open_to": last.isoformat()}
    else:
        span = {k: (f.get(f["status"]) or {}).get(k) for k in ("open_from", "open_to")}
    if why.startswith("suppressed by "):
        since = (f.get("covered") or {}).get("date") or run_date.isoformat()
        return {
            **f,
            "status": "covered",
            "change": "Covered",
            "covered": {"date": since, "by": why.removeprefix("suppressed by "), **span},
        }
    if why.startswith("also seen as ") or into:
        since = (f.get("merged") or {}).get("date") or run_date.isoformat()
        home = {"fingerprint": into} if into else {"as": why.removeprefix("also seen as ")}
        return {
            **f,
            "status": "merged",
            "change": "Merged",
            "merged": {"date": since, **home, **span},
        }
    return None


def _hold(reason: str | None) -> tuple[str, str | None] | None:
    if reason == "muted":
        return ("muted", None)
    if reason and reason.startswith("snoozed until "):
        return ("snoozed", reason.removeprefix("snoozed until "))
    return None


def _absorbed(f: dict[str, Any], results: list[Result]) -> str | None:
    """The group finding of the same detector whose entities now include this finding's (a
    customer silent long enough to join the 90-day group): its fingerprint, for the link."""
    mine = set(f.get("entities") or [])
    if not mine:
        return None
    for r in results:
        if r.passed and r.detector == f.get("detector") and mine < set(r.entities):
            return fingerprint(r)
    return None


def resolution(r: Result | None) -> str:
    """Why an open finding was resolved: its own test stopped firing (cleared by data), an
    answer stopped testing it, it fell under the floor, or it isn't tested any more. (Covered
    and merged findings aren't resolved: `_left`.)"""
    if r is None:
        return "No longer tested"
    why = r.reason or ""
    if why.startswith("explained by your answer"):
        return "Explained by your answer" + why.removeprefix("explained by your answer")
    if why == "below materiality":
        return "Below the materiality floor"
    return "Cleared by data" + (f" · {why}" if why and why != "no signal" else "")


CAME_BACK = "Came back"


def _tag(before: dict[str, Any] | None, r: Result, days: int) -> str:
    if before is None:
        return "New"
    a, b = Decimal(before.get("money") or 0), Decimal(r.money or 0)
    if a and b > a * (1 + MOVED):
        return "Worse"
    if a and b < a * (1 - MOVED):
        return "Better"
    return f"Open · day {days}"


def _order(f: dict[str, Any]) -> tuple[Any, ...]:
    return (f["status"] != "open", -Decimal(f.get("money") or 0), f["fingerprint"])


def counts(
    findings: list[dict[str, Any]], watches: list[dict[str, Any]], suppressed: int
) -> dict[str, int]:
    """Sweep's tab counts (5a): Findings · Watching · Suppressed · Resolved. Suppressed is the
    known patterns' count plus the owner's snoozes and mutes."""
    open_ = [f for f in findings if f["status"] == "open"]
    held = sum(1 for f in findings if f["status"] in HELD)
    return {
        "findings": len(open_),
        "new": sum(1 for f in open_ if f["change"] == "New"),
        "needs_confirmation": sum(1 for f in open_ if f["state"] == "needs_confirmation"),
        "watching": len(watches),
        "suppressed": suppressed + held,
        "suppressed_by_pattern": suppressed,
        "resolved": sum(1 for f in findings if f["status"] == "resolved"),
    }


def overlay(
    findings: list[dict[str, Any]],
    snoozes: list[dict[str, Any]],
    mutes: list[dict[str, Any]],
    today: dt.date,
) -> list[dict[str, Any]]:
    """What the pages show (answered 5 Nov 2026): a run's findings with the snoozes and mutes
    active now. Every count reads this, so Sweep, Home and the brief agree within the day. The
    stored run is never rewritten; the engine applies the same holds from the next run."""
    snoozed = {
        s["fingerprint"]: s["until"]
        for s in snoozes
        if today <= dt.date.fromisoformat(str(s["until"]))
    }
    muted = {m["pattern"] for m in mutes}
    out = []
    for f in findings:
        if f["status"] not in ("open", *HELD):
            out.append(f)
            continue
        g = {k: v for k, v in f.items() if k != "snoozed_until"}
        if f["fingerprint"] in snoozed:
            g["status"], g["snoozed_until"] = "snoozed", str(snoozed[f["fingerprint"]])
        elif pattern_of(f) in muted:
            g["status"] = "muted"
        else:
            g["status"] = "open"
        out.append(g if g != f else f)
    return out


def view_counts(
    shown: list[dict[str, Any]], watches: list[dict[str, Any]], stored: dict[str, int]
) -> dict[str, int]:
    """The tab counts of the overlay (5a), from the run's stored counts."""
    return counts(shown, watches, stored.get("suppressed_by_pattern", stored.get("suppressed", 0)))
