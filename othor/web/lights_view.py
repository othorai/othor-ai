# SPDX-License-Identifier: Apache-2.0
"""Home's lights (owner, 16 Nov 2026): what an owner checks at the start of the day, as a car's
dashboard shows it: one light per part, in the order the data flows (the sources, last night's
run, what Othor AI found, the briefs, what's next), each its own icon, coloured by its state.
The row is on Home; the full list opens in a panel on the right.

Every light is counted from what the run recorded (its steps, outputs, events and deliveries); no
model. States: ok (green), note (grey: expected, nothing to do), warn (amber), bad (red),
info (no colour: a fact, not a check).

`lights(facts)` is pure (the tests feed it facts); `gather()` reads the facts for a run."""

from __future__ import annotations

import datetime as dt
import itertools
import statistics
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core import fmt

PROBLEM = ("warn", "bad")
GROUPS = (
    ("data", "Your data"),
    ("run", "Last night’s run"),
    ("found", "What Othor AI found"),
    ("briefs", "Your briefs"),
    ("next", "Next"),
    ("licence", "Your licence"),
)
# a missing column or table, by the driver's code (PostgreSQL SQLSTATE, MySQL number): the run
# keeps the code, never the message (it can quote the SQL)
MISSING_COLUMN = {"42703", "1054"}
MISSING_TABLE = {"42P01", "1146"}
VOLUME_HISTORY = 5  # nights of history before a day's new rows are judged
VOLUME_LOW, VOLUME_HIGH = 0.5, 3.0
LATE_MINUTES = 45
SLOW_TIMES = 2.0


def _plural(n: int, word: str) -> str:
    return f"{fmt.num(n)} {word}{'' if n == 1 else 's'}"


def _of(k: int, n: int, word: str) -> str:
    return f"{fmt.num(k)} of {fmt.num(n)} {word}{'' if n == 1 else 's'}"


def _names(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def _light(
    key: str,
    label: str,
    icon: str,
    group: str,
    state: str,
    line: str,
    details: list[str] | None = None,
    href: str = "",
    link: str = "",
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "icon": icon,
        "group": group,
        "state": state,
        "line": line,
        "details": details or [],
        "href": href,
        "link": link,
    }


# --- the lights, in the order the data flows ---------------------------------------------------


def _connection(srcs: list[dict[str, Any]]) -> dict[str, Any]:
    off = [x for x in srcs if x.get("state") in ("paused", "disconnected")]
    failed = [x for x in srcs if x.get("read_reason") and x not in off]
    on = [x for x in srcs if x not in off]
    details = [f"{x['title']}: {x['read_reason']}" for x in failed] + [
        f"{x['title']}: {x['state']}" for x in off
    ]
    if failed:
        state, line = "bad", f"{_of(len(on) - len(failed), len(on), 'source')} answered"
    elif off:
        state, line = "note", f"{_of(len(on), len(srcs), 'source')} connected"
    else:
        state, line = "ok", f"All {_plural(len(srcs), 'source')} answered"
    return _light(
        "connection", "Connection", "plug", "data", state, line, details, "/sources", "Data sources"
    )


def _columns(srcs: list[dict[str, Any]]) -> dict[str, Any]:
    cols = [x["title"] for x in srcs if x.get("query_code") in MISSING_COLUMN]
    tabs = [x["title"] for x in srcs if x.get("query_code") in MISSING_TABLE]
    details = [f"{t}: a column the numbers use is missing" for t in cols] + [
        f"{t}: a table the numbers use is missing" for t in tabs
    ]
    if details:
        n = len(cols) + len(tabs)
        line = f"{_plural(n, 'source')} changed shape"
        return _light(
            "columns",
            "Columns",
            "columns",
            "data",
            "bad",
            line,
            details,
            "/sources",
            "Data sources",
        )
    return _light(
        "columns", "Columns", "columns", "data", "ok", "Every column the numbers use is there"
    )


def _fresh(srcs: list[dict[str, Any]]) -> dict[str, Any]:
    daily = [
        x for x in srcs if x["status"] not in ("Not used yet", "Not read yet", "No daily numbers")
    ]
    if not daily:
        return _light("fresh", "Fresh", "clock", "data", "note", "No source has daily numbers yet")
    stale = [x for x in daily if x["status"] != "Updated"]
    late = [x for x in stale if x.get("dot") in ("warn", "neg")]
    line = f"{_of(len(daily) - len(stale), len(daily), 'source')} updated yesterday"
    details = [
        f"{x['title']}: newest data {x.get('newest') or '–'}"
        + ("" if x in late else " (it usually doesn’t update that day)")
        for x in stale
    ]
    state = "ok" if not stale else ("warn" if late else "note")
    return _light(
        "fresh", "Fresh", "clock", "data", state, line, details, "/sources", "Data sources"
    )


def _volume(srcs: list[dict[str, Any]]) -> dict[str, Any]:
    judged = [
        x for x in srcs if x.get("added") is not None and (x.get("history") or 0) >= VOLUME_HISTORY
    ]
    odd = []
    for x in judged:
        usual = x.get("usual") or 0
        if usual <= 0:
            continue
        ratio = x["added"] / usual
        if ratio < VOLUME_LOW:
            odd.append(f"{x['title']}: {fmt.pct(1 - ratio)} fewer new rows than usual")
        elif ratio > VOLUME_HIGH:
            odd.append(f"{x['title']}: {fmt.num(ratio, decimals=1)}× the usual new rows")
    if odd:
        return _light(
            "volume",
            "Volume",
            "volume",
            "data",
            "warn",
            f"{_plural(len(odd), 'source')} with an unusual day",
            odd,
            "/sources",
            "Data sources",
        )
    learning = [x for x in srcs if x.get("added") is not None and x not in judged]
    if not judged:
        return _light(
            "volume",
            "Volume",
            "volume",
            "data",
            "note",
            "Learning what a usual day looks like",
            [f"Each source needs {fmt.num(VOLUME_HISTORY)} nights of history"],
        )
    details = [f"{x['title']}: still learning what’s usual" for x in learning]
    return _light("volume", "Volume", "volume", "data", "ok", "New rows look usual", details)


def _run(run: dict[str, Any]) -> dict[str, Any]:
    when = run.get("ended") or ""
    again = run.get("kind") == "rerun"
    name = f"Run #{run['number']:04d}" + (" (run again)" if again else "")
    if run["status"] == "failed":
        return _light(
            "run",
            "Run",
            "run",
            "run",
            "bad",
            f"{name} failed",
            href=f"/brief/archive?failed={run['number']}",
            link="Why it failed",
        )
    details = []
    if run.get("late"):
        details.append(f"It started at {run['started']}, later than usual")
    if run.get("slow"):
        details.append(f"It took {run['took']}, more than twice as long as usual")
    if run["status"] == "partial":
        details.append("Some sources were kept at their last good day")
    state = "warn" if (run.get("late") or run.get("slow") or run["status"] == "partial") else "ok"
    return _light("run", "Run", "run", "run", state, f"{name} finished at {when}", details)


def _numbers(n: dict[str, Any]) -> dict[str, Any]:
    total, stale = int(n.get("total") or 0), list(n.get("stale") or [])
    if stale:
        return _light(
            "numbers",
            "Numbers",
            "numbers",
            "run",
            "bad",
            f"{_of(total - len(stale), total, 'number')} computed fresh",
            [f"Kept at the last good day: {_names(stale)}"],
            "/ontology",
            "Ontology",
        )
    return _light(
        "numbers", "Numbers", "numbers", "run", "ok", f"All {_plural(total, 'number')} computed"
    )


def _checks(c: dict[str, Any] | None) -> dict[str, Any]:
    if not c or not c.get("total"):
        return _light("checks", "Checks", "sweep", "found", "note", "No checks ran yet")
    ran, total = int(c["ran"]), int(c["total"])
    details = []
    if c.get("waiting"):
        w = int(c["waiting"])
        details.append(f"{_plural(w, 'check')} {'waits' if w == 1 else 'wait'} on your answers")
    if c.get("history"):
        h = int(c["history"])
        details.append(f"{_plural(h, 'check')} {'turns' if h == 1 else 'turn'} on as history grows")
    if c.get("cannot"):
        details.append(f"{_plural(int(c['cannot']), 'check')} can’t run with your data")
    href, link = (
        ("/home?panel=questions", "Answer questions") if c.get("waiting") else ("/sweep", "Sweep")
    )
    return _light(
        "checks",
        "Checks",
        "sweep",
        "found",
        "ok" if ran == total else "note",
        f"{_of(ran, total, 'check')} ran",
        details,
        href,
        link,
    )


def _findings(f: dict[str, Any]) -> dict[str, Any]:
    bits = [
        f"{fmt.num(f[k])} {w}"
        for k, w in (("new", "new"), ("worse", "worse"), ("came_back", "came back"))
        if f.get(k)
    ]
    line = " · ".join(bits) if bits else "Nothing new tonight"
    details = [f"{_plural(int(f.get('open') or 0), 'finding')} open in all"]
    return _light(
        "findings", "Findings", "findings", "found", "info", line, details, "/sweep", "Sweep"
    )


def _forecasts(f: dict[str, Any]) -> dict[str, Any]:
    total, made = int(f.get("total") or 0), int(f.get("made") or 0)
    if not total:
        return _light(
            "forecasts", "Forecasts", "forecast", "found", "note", "No headline numbers to forecast"
        )
    unsure = list(f.get("unsure") or [])
    details = [f"Too uncertain to call: {_names(unsure)}"] if unsure else []
    return _light(
        "forecasts",
        "Forecasts",
        "forecast",
        "found",
        "ok" if made == total else "note",
        f"{_of(made, total, 'number')} forecast",
        details,
        "/brief",
        "What’s ahead",
    )


def _checked(replayed: bool | None) -> dict[str, Any]:
    if replayed is None:
        return _light(
            "checked",
            "Checked",
            "checked",
            "found",
            "note",
            "Double-check runs after the morning’s briefs",
        )
    if replayed:
        return _light(
            "checked",
            "Checked",
            "checked",
            "found",
            "ok",
            "Re-running gives the same numbers",
        )
    return _light(
        "checked",
        "Checked",
        "checked",
        "found",
        "bad",
        "Re-running gave different numbers: we’re looking into it",
    )


AI_WHY = {
    "unavailable": "Othor AI was unavailable, so the brief was written from templates",
    "guard": "Othor AI’s words didn’t match the numbers, so templates were used",
}


def _ai(a: dict[str, Any]) -> dict[str, Any]:
    """Tonight's briefs as written: by Othor AI, from templates (and why), on a quiet day with
    nothing to write, or not written up yet (the rewrite runs after the run)."""
    model, quiet = int(a.get("model") or 0), int(a.get("quiet") or 0)
    waiting, fell = int(a.get("waiting") or 0), int(a.get("template") or 0)
    total = model + quiet + waiting + fell
    if not total:
        return _light("ai", "Othor AI", "ai", "briefs", "note", "No brief written yet")
    details = []
    if fell:
        details.append(AI_WHY[a.get("why") or "unavailable"])
    if waiting:
        details.append(f"{_plural(waiting, 'brief')} not written up yet")
    if quiet:
        details.append(f"{_plural(quiet, 'brief')} on a quiet day: nothing to write up")
    if model == total - quiet and model:
        return _light(
            "ai",
            "Othor AI",
            "ai",
            "briefs",
            "ok",
            f"{_of(model, model, 'brief')} written by Othor AI",
            details,
        )
    if quiet == total:
        return _light("ai", "Othor AI", "ai", "briefs", "ok", "Quiet day: nothing to write up")
    return _light(
        "ai",
        "Othor AI",
        "ai",
        "briefs",
        "note",
        f"{_of(model, total - quiet, 'brief')} written by Othor AI",
        details,
    )


def _briefs(cards: list[dict[str, Any]]) -> dict[str, Any]:
    due = [c for c in cards if c.get("due_today", True)]
    if not due:
        return _light(
            "briefs",
            "Briefs",
            "brief",
            "briefs",
            "note",
            "No brief due today",
            href="/brief",
            link="Morning Brief",
        )
    details, state = [], "ok"
    for c in due:
        if not c["made"]:
            details.append(f"{c['name']}: not made")
            state = "bad"
        elif c.get("bounced"):
            details.append(f"{c['name']}: {_plural(int(c['bounced']), 'email')} bounced")
            state = "bad"
        elif c["sent"]:
            k = int(c["sent"])
            details.append(f"{c['name']}: sent to {fmt.num(k)} {'person' if k == 1 else 'people'}")
        elif not c.get("recipients"):
            details.append(f"{c['name']}: made · no recipients yet")
        elif c["due_passed"]:
            details.append(f"{c['name']}: made but not sent yet")
            state = "warn" if state == "ok" else state
        else:
            details.append(f"{c['name']}: made · goes out at {fmt.clock(c.get('time') or '06:00')}")
    made = sum(1 for c in due if c["made"])
    sent = sum(1 for c in due if c["sent"])
    line = (
        f"{_of(made, len(due), 'brief')} made"
        if made < len(due)
        else (
            f"{_of(sent, len(due), 'brief')} sent"
            if sent
            else f"{_of(made, len(due), 'brief')} made"
        )
    )
    return _light(
        "briefs", "Briefs", "brief", "briefs", state, line, details, "/brief", "Morning Brief"
    )


def _next(n: dict[str, Any]) -> dict[str, Any]:
    return _light(
        "next",
        "Next",
        "next",
        "next",
        "info",
        f"Next run {n['run']}",
        [f"Briefs go out at {n['brief']}"],
    )


def _licence(lic: dict[str, Any]) -> dict[str, Any]:
    """The enterprise licence (M20.3): shown when the install has enterprise features or runs
    several companies. Expiry never locks anyone out; after the grace, no new companies."""
    state, href, link = lic["state"], "/settings#licence", "Licence"
    used = lic.get("companies")
    limit = (lic.get("limits") or {}).get("companies")
    use = f" · {fmt.num(used)} of {fmt.num(limit)} companies" if used is not None and limit else ""
    if state == "valid":
        until = (
            f"until {fmt.day_month_year(lic['expires'])}" if lic.get("expires") else "no end date"
        )
        line = f"{lic['edition'].capitalize()} · {until}{use}"
        return _light(
            "licence", "Licence", "lock", "licence", "ok", line, [lic["customer"]], href, link
        )
    if state == "grace":
        line = f"Expired · {fmt.num(lic['grace_left'])} days to renew"
        return _light(
            "licence",
            "Licence",
            "lock",
            "licence",
            "warn",
            line,
            ["Enterprise features keep working until then"],
            href,
            link,
        )
    if state == "expired":
        return _light(
            "licence",
            "Licence",
            "lock",
            "licence",
            "bad",
            "Expired · no new companies",
            ["Existing companies keep working; renew to add more"],
            href,
            link,
        )
    if state == "invalid":
        return _light(
            "licence",
            "Licence",
            "lock",
            "licence",
            "bad",
            "The licence key isn't valid",
            [lic.get("reason") or ""],
            href,
            link,
        )
    return _light(
        "licence",
        "Licence",
        "lock",
        "licence",
        "note",
        "No licence · enterprise features off",
        href=href,
        link=link,
    )


def lights(facts: dict[str, Any]) -> dict[str, Any]:
    """The 13 lights, in order, with a summary line for Home and the panel's groups."""
    run, srcs = facts["run"], facts.get("sources") or []
    if run["status"] == "failed":
        row = [
            _connection(srcs),
            _run(run),
            _next(facts["next"]),
        ]
    else:
        row = [
            _connection(srcs),
            _columns(srcs),
            _fresh(srcs),
            _volume(srcs),
            _run(run),
            _numbers(facts.get("numbers") or {}),
            _checks(facts.get("checks")),
            _findings(facts.get("findings") or {}),
            _forecasts(facts.get("forecasts") or {}),
            _checked(facts.get("replayed")),
            _ai(facts.get("ai") or {}),
            _briefs(facts.get("briefs") or []),
            _next(facts["next"]),
        ]
    if facts.get("licence"):
        row.append(_licence(facts["licence"]))
    problems = [x for x in row if x["state"] in PROBLEM]
    # Home's row says no run number (design B2, 11 Nov 2026); the panel's subtitle does
    when = f" · finished at {run['ended']}" if run.get("ended") else ""
    if problems:
        k = len(problems)
        summary = f"{fmt.num(k)} thing{'s need' if k != 1 else ' needs'} you"
    else:
        summary = "All clear"
    subtitle = f"{summary} · run #{run['number']:04d}" + when
    summary += when
    groups = [
        {"key": g, "title": t, "lights": [x for x in row if x["group"] == g]}
        for g, t in GROUPS
        if any(x["group"] == g for x in row)
    ]
    return {
        "ok": not problems,
        "summary": summary,
        "subtitle": subtitle,
        "lights": row,
        "groups": groups,
        "bad": any(x["state"] == "bad" for x in problems),
    }


# --- the facts, from what the run recorded -------------------------------------------------------


def _hm(t: dt.datetime | None, company: Any) -> str:
    from othor.core.localtime import local_now

    return fmt.clock(local_now(company, t).time()) if t is not None else ""


def _minutes(a: dt.datetime, b: dt.datetime) -> float:
    return (b - a).total_seconds() / 60


def _took(m: float) -> str:
    return f"{fmt.num(round(m))} min" if m < 90 else f"{fmt.num(m / 60, decimals=1)} h"


def gather(
    s: Session,
    company: Any,
    run: Any,
    rv: Any,
    sources: list[dict[str, Any]],
    checks: dict[str, Any] | None,
    cards: list[dict[str, Any]],
    outputs: dict[str, Any],
    spec: dict[str, Any],
    now: dt.datetime,
) -> dict[str, Any]:
    from othor.briefs.models import BriefIssue
    from othor.core.events import SYSTEM_ACTOR  # noqa: F401
    from othor.core.localtime import local_now
    from othor.core.models import Event
    from othor.engine.models import Run, RunStep

    steps = {x.name: x for x in s.scalars(sa.select(RunStep).where(RunStep.run_id == run.id))}
    nights = list(
        s.scalars(
            sa.select(Run)
            .where(
                Run.kind == "nightly", Run.status.in_(("done", "partial")), Run.number < run.number
            )
            .order_by(Run.number.desc())
            .limit(15)
        )
    )
    # rows each source had at each earlier night's read: the day's new rows are the difference
    past: dict[str, list[int]] = {}
    for r in reversed(nights):
        for st in s.scalars(
            sa.select(RunStep).where(RunStep.run_id == r.id, RunStep.name.like("read:%"))
        ):
            if st.status == "done" and "rows" in (st.detail or {}):
                past.setdefault(st.name.split(":", 1)[1], []).append(int(st.detail["rows"]))
    srcs = []
    for x in sources:
        name = x.get("name") or ""
        rd, mt = steps.get(f"read:{name}"), steps.get(f"materialise:{name}")
        added = usual = None
        hist = past.get(name, [])
        if rd is not None and rd.status == "done" and "rows" in (rd.detail or {}) and hist:
            added = int(rd.detail["rows"]) - hist[-1]
            diffs = [b - a for a, b in itertools.pairwise(hist) if b - a > 0]
            usual = statistics.median(diffs) if diffs else None
        srcs.append(
            {
                **x,
                "read_reason": (rd.detail or {}).get("reason")
                if rd is not None and rd.status == "failed"
                else None,
                "query_code": (mt.detail or {}).get("code")
                if mt is not None and mt.status == "failed"
                else None,
                "added": added,
                "usual": usual,
                "history": len(hist),
            }
        )
    # the run: late against the company's run time, slow against the usual nightly run
    start, end = run.started_at, run.ended_at
    late = slow = False
    took = ""
    if run.kind == "nightly" and start is not None:
        local = local_now(company, start)
        hh, mm = (int(p) for p in str(company.run_time or "02:00").split(":"))
        due = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        late = _minutes(due, local) > LATE_MINUTES
    if start is not None and end is not None:
        mins = _minutes(start, end)
        took = _took(mins)
        usual = [_minutes(r.started_at, r.ended_at) for r in nights if r.ended_at is not None]
        slow = (
            len(usual) >= VOLUME_HISTORY
            and mins > SLOW_TIMES * statistics.median(usual)
            and mins > 5
        )
    # numbers: the active ones, and those kept at their last good day tonight
    names = {k: str(m.get("name") or k) for k, m in (spec.get("metrics") or {}).items()}
    active = [k for k, m in (spec.get("metrics") or {}).items() if m.get("status") == "active"]
    stale = sorted(
        names.get(k, k) for k in (outputs.get("coverage") or {}).get("stale_metrics", [])
    )
    # findings: tonight's change tags on open findings
    tags = [
        str(f.get("change") or "")
        for f in (rv.stored if rv is not None else [])
        if f.get("status") == "open"
    ]
    findings = {
        "new": sum(1 for t in tags if t == "New"),
        "worse": sum(1 for t in tags if t == "Worse"),
        "came_back": sum(1 for t in tags if t.startswith("Came back")),
        "open": len(tags),
    }
    # forecasts and words: tonight's brief issues
    issues = list(s.scalars(sa.select(BriefIssue).where(BriefIssue.run_id == run.id)))
    ahead: dict[str, bool] = {}
    for i in issues:
        for r in i.body.get("ahead") or []:
            ahead[r.get("name") or r["metric"]] = ahead.get(
                r.get("name") or r["metric"], False
            ) or bool(r.get("says"))
    prose = [i.prose or {} for i in issues]
    model = sum(1 for p in prose if p.get("source") == "model")
    quiet = sum(1 for p in prose if p.get("source") != "model" and p.get("guard") == "not-needed")
    waiting = sum(1 for p in prose if p.get("source") != "model" and not p.get("guard"))
    fell = [
        str(p["guard"])
        for p in prose
        if p.get("source") != "model" and p.get("guard") not in (None, "", "not-needed")
    ]
    why = "guard" if any(g.startswith("failed") for g in fell) else "unavailable"
    replays = list(
        s.scalars(
            sa.select(Event)
            .where(Event.action == "run.replayed", Event.object_id == str(run.id))
            .order_by(Event.created_at.desc())
        )
    )
    local = local_now(company, now)
    run_hm = str(company.run_time or "02:00")
    lic = _licence_facts(s, now)
    tonight = local.time() < dt.time.fromisoformat(run_hm)
    return {
        "run": {
            "number": run.number,
            "status": run.status,
            "kind": run.kind,
            "ended": _hm(end, company),
            "started": _hm(start, company),
            "late": late,
            "slow": slow,
            "took": took,
        },
        "sources": srcs,
        "numbers": {"total": len(active), "stale": stale},
        "checks": checks,
        "findings": findings,
        "forecasts": {
            "total": len(ahead),
            "made": sum(1 for v in ahead.values() if v),
            "unsure": sorted(k for k, v in ahead.items() if not v),
        },
        "replayed": (replays[0].after or {}).get("ok") is True if replays else None,
        "ai": {
            "model": model,
            "quiet": quiet,
            "waiting": waiting,
            "template": len(fell),
            "why": why,
        },
        "briefs": cards,
        "licence": lic,
        "next": {
            "run": f"{'today' if tonight else 'tonight'} at "
            + fmt.clock(dt.time.fromisoformat(run_hm)),
            "brief": fmt.clock(dt.time.fromisoformat(str(company.brief_time or "06:00"))),
        },
    }


def _licence_facts(s: Session, now: dt.datetime) -> dict[str, Any] | None:
    """The licence's state for the Licence light: only when the install has enterprise features
    (the othor_ee plug-in) or runs several companies (M20.3)."""
    from othor.core import licence, plugins
    from othor.core.config import get_settings
    from othor.core.models import Company

    loaded = {p.name for p in plugins.loaded()}
    settings = plugins.settings() or get_settings()
    if "ee" not in loaded and settings.tenancy != "multi":
        return None
    st = licence.status(settings, now.date())
    n = s.scalar(
        sa.select(sa.func.count()).select_from(Company).where(Company.deleted_at.is_(None))
    )
    return {
        "state": st.state,
        "reason": st.reason,
        "customer": st.customer,
        "edition": st.edition or "enterprise",
        "expires": st.expires,
        "grace_left": st.grace_left,
        "limits": st.limits,
        "companies": int(n or 0),
    }
