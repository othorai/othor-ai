# SPDX-License-Identifier: Apache-2.0
"""Summary 1.1 (owner, 21 Nov 2026): the summary card at the top of Summary, and what the lines
mark. Open core: reads only the run's stored outputs, imports nothing from the Enterprise
Edition, and calls no model; every sentence is a template, every figure in it a stored value
through the one formatter.

- The lead change: the biggest move among the area rows' lead numbers in the view, against its
  comparison (a tiny start reads "from X to Y", summary_view.tiny_base).
- Between the numbers: Sweep's stored pair results, named only when its test passed (after the
  false-discovery check; a snoozed or muted one still passed) and strong. Strong is the
  detector's own bar: a lead/lag result's `strong` (rank correlation 0.6 or more at the best
  lag); a broken relationship's correlation before the break 0.6 or more (the detector's own
  precondition for a test). No correlation is computed here. "Has led", never "caused".
- When lines shifted: Sweep's passed changepoint results whose week falls in the period, on the
  total (or the split's value); their lines mark the same weeks.
- Same numbers twice: two active numbers whose stored weekly totals are the same in each of
  their last 6 to 12 common weeks, not all zero (`proposals.same_numbers`, the rule Home's
  proposal uses, exact equality). The draft is that Home proposal (`duplicate_metric`): the
  card opens it in the Othor AI panel, where Apply keeps one; nothing changes until then
  (non-negotiable 6). One the owner declined (kept both) is not flagged again.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from othor.briefs import values
from othor.briefs.tiles import _when
from othor.core import fmt
from othor.web import summary_view as sv

TOGETHER_R = Decimal("0.6")  # a broken pair is named when it moved together this well before
SHIFTS_SHOWN = 3
HELD = ("snoozed", "muted")
# the period each window covers, for "this period's" shifts (a week's slack at the start)
SPAN_DAYS = {"7d": 7, "14d": 14, "4w": 28, "13w": 91, "12m": 366}


def _passed(r: Mapping[str, Any]) -> bool:
    return bool(r.get("passed")) or str(r.get("reason") or "").startswith(HELD)


def _name(spec: Mapping[str, Any], m: str) -> str:
    return str(((spec.get("metrics") or {}).get(m) or {}).get("name") or m)


def _dec(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v)) if v is not None else None
    except ArithmeticError:
        return None


def _q(r: Mapping[str, Any]) -> str:
    q = _dec(r.get("q_value"))
    if q is None:
        return ""
    return "q under 0.001" if q < Decimal("0.001") else f"q {fmt.num(q, decimals=3)}"


def window(view: sv.View, end: dt.date) -> tuple[dt.date, dt.date]:
    """The dates the view's period covers, ending at the run's date."""
    return end - dt.timedelta(days=SPAN_DAYS[view.window] + 7), end


# --- what the card reads ------------------------------------------------------------------------


def shifts(
    results: Iterable[Mapping[str, Any]],
    spec: Mapping[str, Any],
    *,
    start: dt.date,
    end: dt.date,
    pair: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    """The weeks lines shifted in the period: Sweep's passed changepoint tests, newest first, one
    per number (its latest)."""
    metrics = spec.get("metrics") or {}
    b, bv = pair if pair else ("", None)
    best: dict[str, dict[str, Any]] = {}
    for r in results:
        if r.get("detector") != "changepoint" or not _passed(r):
            continue
        m = str(r.get("metric") or "")
        if m not in metrics or (r.get("breakdown") or "") != b or r.get("breakdown_value") != bv:
            continue
        slots = r.get("slots") or {}
        raw = slots.get("change_week") or slots.get("change_date")
        if not raw:
            continue
        week = dt.date.fromisoformat(str(raw)[:10])
        if not (start <= week <= end):
            continue
        unit = (metrics.get(m) or {}).get("unit")
        up = r.get("direction") == "up"
        before, after = _dec(slots.get("before")), _dec(slots.get("after"))
        text = f"{_name(spec, m)} stepped {'up' if up else 'down'} in the {_when('week', week)}"
        if before is not None and after is not None:
            text += f", from {values.text(unit, before)} to {values.text(unit, after)}"
        item = {"metric": m, "week": week, "direction": "up" if up else "down", "text": text + "."}
        if m not in best or week > best[m]["week"]:
            best[m] = item
    return sorted(best.values(), key=lambda x: (-x["week"].toordinal(), x["metric"]))


def shift_dates(items: Iterable[Mapping[str, Any]]) -> dict[str, tuple[dt.date, ...]]:
    out: dict[str, tuple[dt.date, ...]] = {}
    for x in items:
        out[x["metric"]] = (*out.get(x["metric"], ()), x["week"])
    return out


def pairs(results: Iterable[Mapping[str, Any]], spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Numbers that moved together (and stopped), and which leads which: Sweep's stored pair
    results that passed and are strong. Moved together first, then leads; by name."""
    together, leads = [], []
    for r in results:
        if not _passed(r) or not r.get("eligible", True):
            continue
        slots = r.get("slots") or {}
        if r.get("detector") == "broken_relationship":
            ents = list(r.get("entities") or [])
            r1, r2 = _dec(slots.get("r_before")), _dec(slots.get("r_after"))
            if len(ents) != 2 or r1 is None or r1 < TOGETHER_R or not slots.get("break"):
                continue
            a, b = ents
            brk = dt.date.fromisoformat(str(slots["break"])[:10])
            text = (
                f"{_name(spec, a)} and {_name(spec, b)} moved together until the "
                f"{_when('week', brk)}."
            )
            ch = _dec(slots.get("ratio_change"))
            if ch:
                way = "higher" if ch > 0 else "lower"
                text += (
                    f" Since then {_name(spec, a)} runs {sv.pct_text(abs(ch)).lstrip('+')} {way}"
                    f" against {_name(spec, b)}."
                )
            info = (
                "A broken relationship: the weekly values' rank correlation was "
                f"{fmt.num(r1, decimals=2)} over {slots.get('weeks_before')} weeks before the "
                f"break and {fmt.num(r2 or 0, decimals=2)} over {slots.get('weeks_after')} after; "
                "the weeks after don't fit the fit before (Chow test), "
                f"{_q(r)} after the false-discovery check."
            )
            together.append({"kind": "together", "metrics": (a, b), "text": text, "info": info})
        elif r.get("detector") == "lead_lag":
            leader, follower = slots.get("leader"), slots.get("follower")
            lag, rr = slots.get("lag_weeks"), _dec(slots.get("r"))
            if not r.get("strong") or not leader or not follower or not lag:
                continue
            weeks = f"{fmt.num(int(lag))} week{'s' if int(lag) != 1 else ''}"
            text = f"{_name(spec, leader)} has led {_name(spec, follower)} by {weeks}."
            info = (
                f"Lead and lag: rank correlation {fmt.num(rr or 0, decimals=2)} at a lag of "
                f"{weeks}, tested against shuffled weeks (permutations), {_q(r)} after the "
                "false-discovery check. A lead is not a cause."
            )
            leads.append(
                {"kind": "leads", "metrics": (leader, follower), "text": text, "info": info}
            )
    key = lambda p: p["text"]  # noqa: E731
    return sorted(together, key=key) + sorted(leads, key=key)


def look_alikes(
    spec: Mapping[str, Any],
    points: Iterable[Any],
    decided: Mapping[str, tuple[str, str]],
) -> list[dict[str, Any]]:
    """Pairs of numbers with the same weekly totals (the rule Home's duplicate proposal uses),
    each with its proposal's id when there is one open. `decided`: fingerprint → (id, status)."""
    from othor.engine.proposals import same_numbers

    out = []
    for keep, drop, n, grain in same_numbers(dict(spec), list(points)):
        if grain != "week":
            continue
        fp = "duplicate:" + ":".join(sorted((keep, drop)))
        pid, status = decided.get(fp, (None, "open"))
        if status != "open":
            continue  # kept both, or one already set aside
        out.append(
            {
                "keep": keep,
                "drop": drop,
                "text": f"{_name(spec, drop)} gives the same numbers as {_name(spec, keep)}, "
                f"every week of the last {fmt.num(n)}.",
                "proposal": pid,
            }
        )
    return out


# --- the lead change ----------------------------------------------------------------------------


def _in(g: str, period: dt.date) -> str:
    return (
        "on " + _when(g, period)
        if g == "day"
        else ("in " if g == "month" else "in the ") + _when(g, period)
    )


def _move(r: Mapping[str, Any]) -> tuple[int, Decimal] | None:
    """(rank group, size) of a row's change: 0 a percent (or points for a share), or a rebound
    from an unusual period before (said as one, never as a percent); 1 "from X to Y" (a tiny
    start or more than double); None with nothing to compare or no change."""
    v, c = r.get("value"), r.get("comparison")
    if v is None or c is None or v == c:
        return None
    if r.get("unusual"):
        return (0, abs(v - c) / abs(c)) if c else (1, abs(v - c))
    if sv.tiny_base(c, v):
        return (1, abs(v - c))
    if r.get("unit") not in ("percent", "ratio") and sv.from_to(c, v):
        return (1, abs((v - c) / c))
    return (0, abs((v - c) / c))


def _phrase(r: Mapping[str, Any], *, short: bool) -> str:
    v, c, unit, name = r["value"], r["comparison"], r.get("unit"), r["name"]
    up = v > c
    if r.get("unusual"):
        return f"{name} {_back_words(r, 1)} after an unusually {r['unusual']} {r['grain']}"
    if unit in ("percent", "ratio"):
        pts = fmt.num(abs(v - c) * 100, decimals=1) + " pts"
        return (
            f"{name} {'+' if up else fmt.MINUS}{pts}"
            if short
            else (f"{name} {'rose' if up else 'fell'} {pts}")
        )
    if sv.from_to(c, v):
        return f"{name} went from {values.text(unit, c)} to {values.text(unit, v)}"
    ratio = (v - c) / abs(c)
    if short:
        return f"{name} {sv.pct_text(ratio)}"
    return f"{name} {'rose' if up else 'fell'} {sv.pct_text(abs(ratio)).lstrip('+')}"


def _back_words(r: Mapping[str, Any], n: int) -> str:
    """A rebound in plain words: back near the usual level, or a swing past it."""
    if r.get("back_near"):
        return "is back to its usual level" if n == 1 else "are back to their usual level"
    way = "bounced back" if r.get("unusual") == "low" else "dropped back"
    return way


def lead(rows: list[Mapping[str, Any]], view: sv.View) -> tuple[str, str | None]:
    """The period's lead change in one sentence, and the next two moves in a second line. A
    rebound from an unusual period before leads only when nothing else moved, and says so."""
    moved = []
    for i, r in enumerate(rows):
        m = _move(r)
        if m is not None and r.get("direction") != "flat":
            moved.append((m[0], -m[1], i, r))
    moved.sort(key=lambda x: (x[0], x[1], x[2]))
    if not moved:
        comparable = [r for r in rows if r.get("comparison") is not None]
        if comparable:
            r = comparable[0]
            return (
                f"Your lead numbers held steady {_in(r['grain'], r['period'])}, "
                f"against {_against(r['grain'], view)}.",
                None,
            )
        return (
            "Your numbers are building history: the changes show after a few more nights.",
            None,
        )
    top = moved[0][3]
    if top.get("unusual") and top.get("back_near"):
        text = (
            f"{top['name']} is back to its usual level ({top['value_text']} "
            f"{_in(top['grain'], top['period'])}) after an unusually {top['unusual']} "
            f"{top['grain']}."
        )
    elif top.get("unusual"):
        text = (
            f"{top['name']} {_back_words(top, 1)} to {top['value_text']} "
            f"{_in(top['grain'], top['period'])} after an unusually {top['unusual']} "
            f"{top['grain']} ({values.text(top.get('unit'), top['comparison'])})."
        )
    else:
        text = (
            f"{_phrase(top, short=False)} {_in(top['grain'], top['period'])}, against "
            f"{_against(top['grain'], view)}: the biggest move among your lead numbers."
        )
    rest = moved[1:3]  # the next two moves
    nxt = [_phrase(x[3], short=True) for x in rest if not x[3].get("unusual")]
    more = ("Also moving: " + ", ".join(nxt) + ".") if nxt else ""
    # the rebounds, said once each way: "A and B are back to their usual level after an
    # unusually low week."
    backs: dict[tuple[str, str, bool], list[Mapping[str, Any]]] = {}
    for x in rest:
        if x[3].get("unusual"):
            r = x[3]
            backs.setdefault((r["unusual"], r["grain"], bool(r.get("back_near"))), []).append(r)
    for (side, g, _near), rs in backs.items():
        names = [r["name"] for r in rs]
        who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        more += (" " if more else "") + (
            f"{who} {_back_words(rs[0], len(rs))} after an unusually {side} {g}."
        )
    return text, more or None


def _against(g: str, view: sv.View) -> str:
    return f"the same {g} last year" if view.basis == "year" else f"the {g} before"


# --- the card -----------------------------------------------------------------------------------

INFO_SHIFTS = (
    "Where a line's usual level moved and stayed: the changepoint test on the weekly values (the "
    "break found by PELT, then a Welch test of the weeks after against the weeks before), after "
    "the false-discovery check. The line marks the same week."
)
INFO_SAME = (
    "Two numbers with exactly the same total in each of their last 6 to 12 weeks, not all zero. "
    "Merging keeps one and sets the other aside."
)


def card(
    rows: list[Mapping[str, Any]],
    view: sv.View,
    *,
    shifts: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    alikes: list[dict[str, Any]],
) -> dict[str, Any]:
    """The card: kicker, the lead change, the next moves, up to three columns (only those with
    something to say), the foot."""
    lead_text, more = lead(rows, view)
    cols: list[dict[str, Any]] = []
    if pairs:
        cols.append(
            {
                "label": "Between the numbers",
                "lines": [p["text"] for p in pairs],
                "info": " ".join(p["info"] for p in pairs),
            }
        )
    if shifts:
        shown = [s["text"] for s in shifts[:SHIFTS_SHOWN]]
        if len(shifts) > SHIFTS_SHOWN:
            shown.append(f"And {fmt.num(len(shifts) - SHIFTS_SHOWN)} more, marked on their lines.")
        cols.append({"label": "When lines shifted", "lines": shown, "info": INFO_SHIFTS})
    if alikes:
        cols.append(
            {"label": "Same numbers twice", "lines": [], "alikes": alikes, "info": INFO_SAME}
        )
    return {
        "kicker": f"At a glance · {sv.WINDOWS[view.window][2]}",
        "lead": lead_text,
        "text": more,
        "cols": cols,
        "foot": [],  # the page title says which run already
    }
