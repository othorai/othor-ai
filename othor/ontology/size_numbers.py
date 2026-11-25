# SPDX-License-Identifier: Apache-2.0
"""Numbers suggested for the company's size and goals (#76, docs/plans/next-1126.md §3, steps
3–8).

After the night (its questions step), from the run's `size` (`othor.ontology.size`), the scanned
catalog (names and kinds only), the spec and the goals' states (`goal_link`):

1. **What can be built** (code). Each of the band's numbers the company doesn't have yet is
   `now` (built from its own numbers and breakdowns), `table` (a scanned table named for it, with
   a number and a date, can carry it) or `source` (none can: shown as needing a source, never
   drafted against a missing table).
2. **Ranked** (code): serving a goal that is off track first (`goal_link.rank`), then any goal,
   then the band's order; at most three proposals and one that needs a source.
3. **Proposed** as "New number" rows of the Business Model's proposals: one computed figure each
   (inserted, never a model's), the catalogue's reason in plain words (Othor AI may reword it
   after the night: `othor.home.size_ai`), and the goal it serves. A fingerprint per number, band,
   what it needs and the goal it serves: Not now (declined) keeps it away until one of them
   changes; an open one not picked tonight is set aside (`replaced`) and comes back when picked.
4. **Accept** writes the metric record (a formula over the company's numbers) as a new Business
   Model version; it applies from tonight's run (rule 6).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from othor.core import fmt
from othor.core.events import SYSTEM_ACTOR, record_event
from othor.ontology import catalogue as cat_mod
from othor.ontology import goal_link
from othor.ontology.catalogue import REVENUE, Catalogue, Entry

KIND = "size_number"
LABEL = "New number"
FROM = "Othor AI · for your size"
TOP = 3  # proposals a night (owner, 25 Nov 2026)
NEEDS_TOP = 1  # and one that needs a source
REPLACED = "replaced"
NUMERIC = ("int", "decimal", "float")
TEMPORAL = ("date", "timestamp")
KEYISH = re.compile(r"(^|_)(id|code|key|no|number)$")


@dataclass
class Candidate:
    entry: Entry
    status: str  # now · table · source
    table: str = ""  # "erp_db · stock_ledger" for a table one
    figure: dict[str, Any] | None = None
    goal: goal_link.GoalState | None = None
    missing: list[str] = field(default_factory=list)  # parts not found (source)

    @property
    def key(self) -> str:
        return self.entry.key


# --- 1. what can be built ------------------------------------------------------------------


def has(e: Entry, spec: Mapping[str, Any]) -> bool:
    """The company already has it (any number by that key or name, live or waiting)."""
    for k, m in (spec.get("metrics") or {}).items():
        if m.get("status") == "declined":
            continue
        if k == e.key or k.lower() in e.same or str(m.get("name") or "").lower() in e.same:
            return True
    return False


def _table_for(e: Entry, catalog: Any) -> str:
    """A scanned table named for the number, in use, with rows, a number and enough dates; its
    label, or "". Names and column kinds only: no row is read."""
    words = [str(w).lower() for w in e.build.get("table") or []]
    dates = int(e.build.get("dates") or 1)
    hits = []
    for (source, _schema, name), t in sorted((getattr(catalog, "tables", None) or {}).items()):
        if not any(w in name.lower() for w in words):
            continue
        if t.use in ("ignored", "excluded") or not t.row_count:
            continue
        cols = [c for c in t.columns.values() if not c.personal]
        nums = [c for c in cols if c.kind in NUMERIC and not KEYISH.search(c.name.lower())]
        when = [c for c in cols if c.kind in TEMPORAL]
        if nums and len(when) >= dates:
            hits.append(f"{source} · {name}")
    return hits[0] if hits else ""


def check(
    e: Entry,
    spec: Mapping[str, Any],
    parts: Mapping[str, str],
    catalog: Any,
    figures: Mapping[str, Any],
) -> Candidate | None:
    """None when the company has it; else how it can be built."""
    if has(e, spec):
        return None
    if e.kind == "table":
        table = _table_for(e, catalog)
        return Candidate(e, "table" if table else "source", table)
    missing = [p for p in e.parts() if p not in parts]
    fig = figures.get(e.key)
    if not missing and fig is not None:
        return Candidate(e, "now", figure=dict(fig))
    return Candidate(e, "source", missing=missing or list(e.parts()))


def candidates(
    spec: Mapping[str, Any], size: Mapping[str, Any], catalog: Any, cat: Catalogue | None = None
) -> list[Candidate]:
    c = cat or cat_mod.load()
    band = c.band(str(size["band"]))
    if band is None:
        return []
    parts = size.get("parts") or {}
    figures = size.get("figures") or {}
    out = []
    for key in band.numbers:
        got = check(c.numbers[key], spec, parts, catalog, figures)
        if got is not None:
            out.append(got)
    return out


# --- 2. ranked --------------------------------------------------------------------------------


def link_goals(
    cands: Sequence[Candidate], parts: Mapping[str, str], goals: Sequence[goal_link.GoalState]
) -> None:
    for c in cands:
        uses = [parts[p] for p in c.entry.parts() if p in parts]
        c.goal = goal_link.served(goals, uses, c.entry.area)


def pick(cands: Sequence[Candidate], skip: set[str] = frozenset()) -> list[Candidate]:  # type: ignore[assignment]
    """At most TOP that can be built (now or from a table), then one that needs a source."""
    ok = [c for c in cands if c.key not in skip]
    built = goal_link.rank([c for c in ok if c.status != "source"], lambda c: c.goal)[:TOP]
    needs = goal_link.rank([c for c in ok if c.status == "source"], lambda c: c.goal)[:NEEDS_TOP]
    return [*built, *needs]


# --- 3. words: the figure, the reason, the goal ------------------------------------------------


def _unit_text(unit: str, v: Any) -> str:
    from othor.briefs import values

    return values.text(unit, Decimal(str(v)))


def figure_parts(c: Candidate, spec: Mapping[str, Any], parts: Mapping[str, str]) -> list[str]:
    """The figure sentence as [before, figure, after]: the figure is computed, the words are the
    catalogue's, so the page can set the figure in bold."""
    f = c.figure or {}
    if c.status != "now" or not f:
        return []
    rev = parts.get(REVENUE, "")
    name = str((spec.get("metrics") or {}).get(rev, {}).get("name") or "net revenue")
    fill = {
        "revenue": fmt.lower_first(name),
        "count": ("more than " if f.get("more") else "") + fmt.num(int(f.get("count") or 0)),
        "month": fmt.month_year(dt.date.fromisoformat(f["month"])) if f.get("month") else "",
        "low": str(f.get("low") or ""),
    }
    template = c.entry.figure
    before, _, after = template.partition("{value}")
    text = [before.format(**fill), _unit_text(c.entry.unit, f["value"]), after.format(**fill)]
    text[0] = text[0][:1].upper() + text[0][1:] if text[0] else text[0]
    return text


def goal_clause(g: goal_link.GoalState | None) -> str:
    if g is None:
        return ""
    return f"It serves your goal {g.name} ({g.plain})"


def compose(ev: Mapping[str, Any]) -> str:
    """The row's line, as text: figure sentence, reason, goal."""
    fig = "".join(ev.get("figure_parts") or [])
    bits = [fig, str(ev.get("reason") or ""), str(ev.get("goal_line") or "")]
    if ev.get("status") == "table":
        bits[0] = f"Found in your tables: {ev.get('table')}"
    if ev.get("status") == "source":
        bits[0] = (
            f"Companies your size usually watch it. None of your sources has {ev.get('needs')}"
        )
    return ". ".join(b.rstrip(". ") for b in bits if b)


# --- 4. proposals --------------------------------------------------------------------------------


def fingerprint(c: Candidate, band: str) -> str:
    """What Not now waits on: the number, the band, what it needs and the goal it serves (and
    whether that goal is behind). The figure moving night to night doesn't bring it back."""
    g = c.goal
    basis = f"{band}|{c.status}|{c.table}|{g.key if g else ''}|{g.behind if g else ''}"
    return f"size:{c.key[:32]}:{hashlib.sha256(basis.encode()).hexdigest()[:16]}"


def _text(c: Candidate) -> str:
    name = c.entry.name
    if c.status == "now":
        return f"Add “{name}”"
    if c.status == "table":
        return f"Add “{name}” from your tables"
    return f"“{name}”: {fmt.lower_first(c.entry.home)}"


def evidence(
    c: Candidate,
    rank: int,
    size: Mapping[str, Any],
    spec: Mapping[str, Any],
    cat: Catalogue,
    run_date: dt.date,
) -> dict[str, Any]:
    parts = size.get("parts") or {}
    band = cat.band(str(size["band"]))
    needs = c.entry.needs or " and ".join(
        fmt.lower_first(str(cat.parts.get(p, {}).get("names", (p,))[0])) for p in c.missing
    )
    ev: dict[str, Any] = {
        "key": c.key,
        "name": c.entry.name,
        "home": c.entry.home,
        "area": c.entry.area,
        "unit": c.entry.unit,
        "level": c.entry.level,
        "status": c.status,
        "table": c.table,
        "needs": needs,
        "rank": rank,
        "band": str(size["band"]),
        "band_label": band.label if band else "",
        "size": {
            k: size.get(k)
            for k in ("metric", "months", "through", "revenue", "currency", "crore", "catalogue")
        },
        "parts": {p: parts[p] for p in c.entry.parts() if p in parts},
        "build": c.entry.build,
        "figure": c.figure,
        "figure_parts": figure_parts(c, spec, parts),
        "goal": (
            {"key": c.goal.key, "name": c.goal.name, "state": c.goal.state} if c.goal else None
        ),
        "goal_line": goal_clause(c.goal),
        "reason": c.entry.reason,
        "reason_by": "template",
        "run_date": run_date.isoformat(),
        "confidence": "high" if c.status == "now" and c.goal and c.goal.behind else "medium",
    }
    ev["line"] = compose(ev)
    return ev


def propose(
    s: Any,
    run: Any,
    spec: Mapping[str, Any],
    size: Mapping[str, Any] | None,
    catalog: Any,
    goals: Sequence[goal_link.GoalState],
    cat: Catalogue | None = None,
) -> list[Any]:
    """Tonight's suggestions as proposals: new ones created, open ones refreshed (their figure),
    open ones not picked tonight set aside. Returns the proposals created or reopened."""
    from othor.engine.models import Proposal

    c = cat or cat_mod.load()
    rows = s.scalars(sa.select(Proposal).where(Proposal.kind == KIND)).all()
    if not size:
        for p in rows:
            if p.status == "open":
                p.status = REPLACED
        return []
    band = str(size["band"])
    cands = candidates(spec, size, catalog, c)
    link_goals(cands, size.get("parts") or {}, goals)
    by_fp = {p.fingerprint: p for p in rows}
    decided = {
        c_.key
        for c_ in cands
        if (p := by_fp.get(fingerprint(c_, band))) and p.status in ("accepted", "declined")
    }
    picked = pick(cands, decided)
    keep: set[str] = set()
    made = []
    for i, cand in enumerate(picked):
        fp = fingerprint(cand, band)
        keep.add(fp)
        ev = evidence(cand, i, size, spec, c, run.run_date)
        p = by_fp.get(fp)
        if p is not None and p.status == "open":
            old = p.evidence or {}
            if old.get("reason_by") == "othor_ai":  # Othor AI's words stay until it changes
                ev["reason"], ev["reason_by"] = old.get("reason"), "othor_ai"
                ev["prompt"] = old.get("prompt")
                ev["line"] = compose(ev)
            if old.get("worded"):
                ev["worded"] = True
            p.evidence, p.text, p.run_id = ev, _text(cand), run.id
            continue
        if p is None:
            p = Proposal(
                kind=KIND,
                text=_text(cand),
                evidence=ev,
                origin={"rule": "size_numbers", "band": band, "number": cand.key},
                fingerprint=fp,
                status="open",
                run_id=run.id,
            )
            s.add(p)
            s.flush()
            action = "proposal.created"
        else:  # set aside on an earlier night, picked again
            p.status, p.evidence, p.text, p.run_id = "open", ev, _text(cand), run.id
            action = "proposal.reopened"
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action=action,
            object_type="proposal",
            object_id=p.id,
            after={"kind": KIND, "fingerprint": fp, "number": cand.key, "status": cand.status},
        )
        made.append(p)
    for p in rows:
        if p.status == "open" and p.fingerprint not in keep:
            p.status = REPLACED
    return made


# --- 5. Accept: the metric record ----------------------------------------------------------------


def _m(key: str, missing: str = "not_determinable") -> dict[str, Any]:
    return {"metric": key, "missing": missing}


def record(
    ev: Mapping[str, Any], spec_metrics: Mapping[str, Any], taken: set[str]
) -> tuple[str, dict[str, Any]] | None:
    """The metric record an accepted suggestion drafts: a formula over the company's own numbers
    (None when it isn't built from them)."""
    if ev.get("status") != "now":
        return None
    b = dict(ev.get("build") or {})
    parts = dict(ev.get("parts") or {})
    body: dict[str, Any] = {
        "name": ev["name"],
        "unit": ev["unit"],
        "grain": "month",
        "area": ev.get("area"),
        "description": "Suggested by Othor AI for your size",
    }
    if "top" in b:
        of = parts.get(str(b["of"]))
        if of is None:
            return None
        body["formula"] = {
            "fn": "top_share",
            "args": [_m(of), {"lit": str(b["by"])}, {"lit": int(b["top"])}],
        }
        body["description"] = (
            f"The largest {int(b['top'])} {b['by']}s’ share of the total, each month"
        )
    else:
        take = parts.get(str(b.get("take")))
        if take is None:
            return None
        top: dict[str, Any] = _m(take)
        if b.get("less"):
            less = parts.get(str(b["less"]))
            if less is None:
                return None
            top = {"op": "-", "args": [top, _m(less, "zero")]}
        if b.get("over"):
            over = parts.get(str(b["over"]))
            if over is None:
                return None
            body["formula"] = {"op": "/", "args": [top, _m(over)]}
        else:
            body["formula"] = top
        by = (ev.get("figure") or {}).get("by")
        if by:
            body["breakdowns"] = [by]
    key = str(ev["key"])
    n = 2
    while key in taken or key in spec_metrics:
        key = f"{ev['key']}_{n}"
        n += 1
    return key, body


def changes(s: Any, p: Any) -> list[Any]:
    """The Business Model change an accepted suggestion writes (`proposals._changes`)."""
    from othor.ontology import service

    taken = {r.key for r in service.current(s, ["metric"])}
    got = record(p.evidence or {}, {}, taken)
    if got is None:
        return []
    key, body = got
    return [service.put("metric", key, body, {"proposal": str(p.id)})]


# --- 6. where they show ---------------------------------------------------------------------


def open_rows(s: Any) -> list[Any]:
    from othor.engine.models import Proposal

    rows = s.scalars(
        sa.select(Proposal).where(Proposal.kind == KIND, Proposal.status == "open")
    ).all()
    return sorted(rows, key=lambda p: (int((p.evidence or {}).get("rank", 99)), str(p.id)))


def lead_line(s: Any) -> list[str]:
    """Above the proposals: the size, computed, and what Othor AI suggests from it, as
    [before, figure, after] (the figure in bold); [] without a suggestion."""
    rows = open_rows(s)
    if not rows:
        return []
    ev = rows[0].evidence or {}
    sz = ev.get("size") or {}
    if not sz.get("revenue"):
        return []
    through = dt.date.fromisoformat(str(sz["through"]))
    months = int(sz.get("months") or 12)
    with fmt.money_style(str(sz.get("currency") or "INR")):
        rev = fmt.money(Decimal(str(sz["revenue"])))
    after = ""
    if sz.get("currency", "INR") != "INR":
        with fmt.money_style("INR"):
            after = f", about {fmt.money(Decimal(str(sz['crore'])) * Decimal('1e7'))} a year"
    span = "12 months" if months == 12 else f"{fmt.num(months)} months"
    return [
        f"Net revenue in the {span} to {fmt.month_year(through)}: ",
        rev,
        f"{after}. Othor AI suggests the numbers that matter most to a {ev.get('band_label')} "
        "company, first the ones that serve your goals.",
    ]


def row_extra(p: Any) -> dict[str, Any]:
    """What a size suggestion's row adds to `home_view.proposal_row`."""
    ev = p.evidence or {}
    fp = ev.get("figure_parts") or []
    rest = ". ".join(
        b.rstrip(". ") for b in (str(ev.get("reason") or ""), str(ev.get("goal_line") or "")) if b
    )
    out: dict[str, Any] = {
        "size": ev.get("status"),
        "line": ev.get("line", ""),
        "line_b": [fp[0], fp[1], fp[2] + (". " + rest if rest else "")] if len(fp) == 3 else None,
        "say": f"Add the number {ev.get('name')} from {ev.get('table')}" if ev.get("table") else "",
    }
    return out


def home_item(s: Any) -> dict[str, Any] | None:
    """The top suggestion that can be accepted now, for Home's Suggested by Othor AI."""
    for p in open_rows(s):
        ev = p.evidence or {}
        if ev.get("status") != "now":
            continue
        fp = ev.get("figure_parts") or []
        return {
            "id": str(p.id),
            "title": ev.get("home") or p.text,
            "figure": fp[1] if len(fp) == 3 else "",
            "line": ev.get("line", ""),
            "line_b": row_extra(p)["line_b"],
            "goal": ev.get("goal_line", ""),
        }
    return None


def level_rows(s: Any) -> dict[str, list[dict[str, Any]]]:
    """Open suggestions by the Business Model checklist item they help (owner, 25 Nov 2026)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for p in open_rows(s):
        ev = p.evidence or {}
        if ev.get("status") == "source":
            continue
        out.setdefault(str(ev.get("level") or "numbers"), []).append(
            {"id": str(p.id), "text": p.text, "line": ev.get("line", ""), **row_extra(p)}
        )
    return out
