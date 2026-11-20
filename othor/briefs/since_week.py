# SPDX-License-Identifier: Apache-2.0
"""0.28.5 · "Since last week" inside each brief (owner, 19 Nov 2026; plan 20 Nov 2026): what this
brief reported over the week (or a Weekly or Monthly brief's period), what got fixed and how, what
is still open and whether it got better or worse, the open items night by night, and at most one
suggestion. Pure: built at assembly time from tonight's findings and the stored nights
(`brief_history`, a run input), so the issue replays byte for byte. No model; every word is
code-built from computed values."""

from __future__ import annotations

import datetime as dt
import itertools
from decimal import Decimal, InvalidOperation
from typing import Any

from othor.briefs import scope
from othor.charts.spec import Annotation, ChartSpec, Period, Series, Unit, YAxis
from othor.core import fmt

WINDOW_DAYS = 7  # a daily brief looks back a week
CHART_NIGHTS = 30  # the chart's nights, tonight included
UNCHANGED_DAYS = 7  # the suggestion: an item unchanged this long
MOVED = Decimal("0.10")  # better or worse: as Sweep's tags (findings.MOVED)
OPEN = "open"


def _money(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def _title(f: dict[str, Any]) -> str:
    return str((f.get("text") or {}).get("title") or f.get("title") or "")


def _first(f: dict[str, Any]) -> dt.date | None:
    d = (f.get("first_seen") or {}).get("date") if isinstance(f.get("first_seen"), dict) else None
    d = d or f.get("first_seen")
    return dt.date.fromisoformat(str(d)) if d else None


def how(status: str, note: str | None, date: str | None, detector: str | None) -> str:
    """Why an item left the open list, in plain words (findings.resolution and _left)."""
    on = f" on {fmt.day_month(dt.date.fromisoformat(date))}" if date else ""
    note = note or ""
    if status == "snoozed":
        if note:
            return f"You set it aside until {fmt.day_month(dt.date.fromisoformat(note))}"
        return "You set it aside"
    if status == "muted":
        return "You muted it"
    if status == "covered":
        return f"Became a known pattern{on}" + (f": {note}" if note else "")
    if status == "merged":
        return f"Folded into another finding{on}"
    if note.startswith("Explained by your answer"):
        return f"Explained by your answer{on}"
    if note.startswith("Below the materiality floor"):
        return f"Became too small to matter{on}"
    if note.startswith("No longer tested"):
        return f"No longer checked from{on.removeprefix(' on')}" if on else "No longer checked"
    if detector == "cross_source":
        return f"The two sources agree again{on}"
    return f"Back to normal{on}"


def _left(f: dict[str, Any]) -> tuple[str | None, str | None]:
    """Tonight's finding that isn't open: its note (reason, pattern, until) and its date."""
    st = f.get("status")
    if st == "resolved":
        r = f.get("resolved") or {}
        return r.get("reason"), r.get("date")
    if st == "covered":
        c = f.get("covered") or {}
        return c.get("by"), c.get("date")
    if st == "merged":
        return None, (f.get("merged") or {}).get("date")
    if st == "snoozed":
        return f.get("snoozed_until"), None
    return None, None


def build(
    spec: dict[str, Any],
    version: dict[str, Any],
    run_date: dt.date,
    findings: list[dict[str, Any]],
    history: dict[str, Any] | None,
    shown: list[str],
) -> dict[str, Any] | None:
    """The section for one brief, or None when there's nothing to say (no item reported, fixed
    or open in its window). `shown`: tonight's top items, which "Still open" leaves out."""
    sc = version.get("scope") or {}
    period = version.get("period")
    start = (
        dt.date.fromisoformat(period["from"])
        if period and period.get("from")
        else run_date - dt.timedelta(days=WINDOW_DAYS - 1)  # the 7 days ending today
    )
    word = str((period or {}).get("word") or "last week")
    hist = history or {}
    meta: dict[str, dict[str, Any]] = hist.get("meta") or {}
    nights = [n for n in hist.get("nights") or [] if n["date"] < run_date.isoformat()]

    def inside(f: dict[str, Any]) -> bool:
        return scope.finding_in(spec, sc, f)

    now = {f["fingerprint"]: f for f in findings if inside(f)}
    known = {fp: m for fp, m in meta.items() if inside(m)}
    # each fingerprint's rows by night (status, money, change, note, date)
    rows: dict[str, list[tuple[str, list[Any]]]] = {}
    for n in nights:
        for r in n["rows"]:
            if r[0] in now or r[0] in known:
                rows.setdefault(r[0], []).append((n["date"], r))

    def first_seen(fp: str) -> dt.date | None:
        f = now.get(fp)
        return _first(f) if f else _first(known.get(fp) or {})

    # reported in the window: first seen on or after its start
    reported = sorted(
        fp for fp in set(now) | set(known) if (d := first_seen(fp)) is not None and d >= start
    )
    # fixed: open on some night of the window (or tonight's resolved), not open now
    fixed: list[dict[str, Any]] = []
    for fp in sorted(set(now) | set(rows)):
        f = now.get(fp)
        was_open = any(d >= start.isoformat() and r[1] == OPEN for d, r in rows.get(fp, []))
        if f is not None:
            if f.get("status") == OPEN:
                continue
            note, date = _left(f)
            st, det, title = str(f["status"]), f.get("detector"), _title(f)
            if st == "resolved" and date and date >= start.isoformat():
                was_open = True
        else:
            last = rows[fp][-1][1]
            st, note, date = str(last[1]), last[4], last[5]
            m = known.get(fp) or {}
            det, title = m.get("detector"), _title(m)
            if st == OPEN:  # gone without a word: left the list (no longer tested)
                st, note, date = "resolved", "No longer tested", run_date.isoformat()
        if not was_open:
            continue
        fixed.append(
            {"fingerprint": fp, "title": title, "how": how(st, note, date, det), "date": date or ""}
        )
    fixed.sort(key=lambda x: (x["date"] or "", x["title"]), reverse=True)  # newest first
    # what's open: as a brief counts it (a contradiction that needs confirmation is a Home
    # question, never a brief item), so the line, the list and the chart agree
    open_now = [
        f
        for f in now.values()
        if f.get("status") == OPEN and f.get("state") != "needs_confirmation"
    ]
    still: list[dict[str, Any]] = []
    for f in open_now:
        fp = f["fingerprint"]
        if fp in shown:
            continue
        a, b = _money((known.get(fp) or {}).get("first_money")), _money(f.get("money"))
        trend = "about the same"
        if a and b is not None and b > a * (1 + MOVED):
            trend = "worse"
        elif a and b is not None and b < a * (1 - MOVED):
            trend = "better"
        still.append(
            {
                "fingerprint": fp,
                "title": _title(f),
                "days": int(f.get("days_open") or 1),
                "trend": trend,
            }
        )
    still.sort(key=lambda x: (-x["days"], x["title"]))
    longest = max(
        open_now, key=lambda f: (int(f.get("days_open") or 1), f["fingerprint"]), default=None
    )
    if not reported and not fixed and not open_now:
        return None
    # owner, 20 Nov 2026: the line and the list agree; the open items already shown at the top
    # are named in the count ("10 still open (5 in today's items)")
    in_items = sum(1 for f in open_now if f["fingerprint"] in shown)
    counts = {
        "reported": len(reported),
        "fixed": len(fixed),
        "open": len(open_now),
        "in_items": in_items,
    }
    head = f"Last {fmt.num(WINDOW_DAYS)} days" if not period else f"Since {word}"
    line = (
        f"{head}: {fmt.num(counts['reported'])} reported · {fmt.num(counts['fixed'])} fixed · "
        f"{fmt.num(counts['open'])} still open"
        + (f" ({fmt.num(in_items)} in today’s items)" if in_items else "")
    )
    out: dict[str, Any] = {
        "word": word,
        "from": start.isoformat(),
        "counts": counts,
        "line": line,
        "fixed": fixed,
        "open": still,
    }
    if longest is not None:
        out["longest"] = {
            "fingerprint": longest["fingerprint"],
            "title": _title(longest),
            "days": int(longest.get("days_open") or 1),
        }
        out["line"] += f" · longest open: {_title(longest)}, day {fmt.num(out['longest']['days'])}"
    chart = _chart(version, run_date, nights, now, known)
    if chart is not None:
        out["chart"] = chart.to_json()
    tip = _suggest(open_now, rows, run_date)
    if tip is not None:
        out["suggest"] = tip
    return out


def _chart(
    version: dict[str, Any],
    run_date: dt.date,
    nights: list[dict[str, Any]],
    now: dict[str, dict[str, Any]],
    known: dict[str, dict[str, Any]],
) -> ChartSpec | None:
    """Open items night by night over the last 30 nights, a dot on each night something closed."""
    first = (run_date - dt.timedelta(days=CHART_NIGHTS - 1)).isoformat()

    def counted(fp: str) -> bool:  # in scope, and a brief item's kind (not a Home question)
        f = now.get(fp) or known.get(fp)
        return f is not None and f.get("state") != "needs_confirmation"

    days: list[tuple[str, set[str]]] = [
        (n["date"], {r[0] for r in n["rows"] if r[1] == OPEN and counted(r[0])})
        for n in nights
        if n["date"] >= first
    ]
    days.append(
        (
            run_date.isoformat(),
            {fp for fp, f in now.items() if f.get("status") == OPEN and counted(fp)},
        )
    )
    if len(days) < 2:
        return None
    pts = tuple((d, float(len(s))) for d, s in days)
    marks = []
    for (_, before), (d, after) in itertools.pairwise(days):
        closed = len(before - after)
        if closed:
            marks.append(
                Annotation(
                    "point",
                    at=d,
                    value=float(len(after)),
                    role="positive",
                    label=f"{fmt.num(closed)} fixed",
                )
            )
    total = sum(len(before - after) for (_, before), (_, after) in itertools.pairwise(days))
    return ChartSpec(
        id=f"week:{version.get('id', '')}",
        kind="step",
        title="Open items, night by night",
        unit=Unit("count"),
        period=Period("day", days[0][0], days[-1][0]),
        y=YAxis(zero_based=True),
        series=(Series("open", "Open items", "neutral", pts),),
        annotations=tuple(marks),
        provenance={"brief": str(version.get("id", "")), "source": "brief_history"},
        alt_text=(
            f"Open items each night from {fmt.day_month(dt.date.fromisoformat(days[0][0]))} to "
            f"{fmt.day_month(run_date)}: {fmt.num(int(pts[-1][1]))} open now, "
            f"{fmt.num(total)} fixed along the way."
        ),
    )


def _suggest(
    open_now: list[dict[str, Any]], rows: dict[str, list[tuple[str, list[Any]]]], run_date: dt.date
) -> dict[str, Any] | None:
    """At most one: the longest-open item that hasn't changed for a week, set aside for a week
    (the existing Snooze 7 days). Words by code; the owner applies it."""
    best = None
    for f in open_now:
        if f.get("state") == "needs_confirmation":
            continue
        if not str(f.get("change") or "").startswith("Open"):
            continue  # it moved tonight
        moved = [
            d for d, r in rows.get(f["fingerprint"], []) if not str(r[3] or "").startswith("Open")
        ]
        since = dt.date.fromisoformat(moved[-1]) if moved else _first(f)
        if since is None or (run_date - since).days < UNCHANGED_DAYS:
            continue
        key = (int(f.get("days_open") or 1), f["fingerprint"])
        if best is None or key > best[0]:
            best = (key, f, (run_date - since).days)
    if best is None:
        return None
    _, f, quiet = best
    days = int(f.get("days_open") or 1)
    return {
        "fingerprint": f["fingerprint"],
        "action": "snooze",
        "text": (
            f"Open {fmt.num(days)} days, unchanged for {fmt.num(quiet)}: {_title(f)}. "
            "Set it aside for a week?"
        ),
    }
