# SPDX-License-Identifier: Apache-2.0
"""Home proposals from rules (docs/04 §10), after the run's `detect`. M6b: suppression; M10a:
goal (a goal at risk) and definition change (a contradiction answered the same way twice), and
the owner's Accept, Decline and Undo for every kind.

A finding that recurs on the same calendar window in two or more years (tonight's and an earlier
year's run found it within a week of the same dates) is proposed as a known pattern. Each
proposal has a fingerprint; one that exists (open, accepted or declined) is never proposed again.
Home shows them from M10; Sweep's Suppressed tab lists the open ones. Text is a template filled
with computed values only. Inside the company's tenant scope.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from othor.core import fmt
from othor.core.events import SYSTEM_ACTOR, record_event
from othor.engine.findings import pattern_of
from othor.engine.models import Finding, Proposal, Run

NEAR_DAYS = 7


def _md(d: dt.date, year: int) -> dt.date:
    try:
        return d.replace(year=year)
    except ValueError:  # 29 Feb
        return d.replace(year=year, day=28)


def recurring(
    tonight: dict[str, Any], run_date: dt.date, earlier: list[tuple[dt.date, dict[str, Any]]]
) -> list[int]:
    """The years in which this finding's pattern was found on the same calendar window."""
    pat = pattern_of(tonight)
    start = dt.date.fromisoformat(tonight["window"][0])
    years = {start.year}  # the window's year: a run on 1 Jan reports December
    for _day, f in earlier:
        if pattern_of(f) != pat:
            continue
        then = dt.date.fromisoformat(f["window"][0])
        if then.year < start.year and abs((_md(then, start.year) - start).days) <= NEAR_DAYS:
            years.add(then.year)
    return sorted(years)


def suppression(
    s: Any, run: Run, spec: dict[str, Any], found: list[dict[str, Any]]
) -> list[Proposal]:
    rows = s.execute(
        sa.select(Run.run_date, Finding.body)
        .join(Run, Run.id == Finding.run_id)
        .where(Run.number < run.number, Finding.status.in_(("open", "snoozed", "muted")))
    ).all()
    earlier = [(d, b) for d, b in rows]
    made = []
    for f in found:
        if f["status"] != "open":
            continue
        years = recurring(f, run.run_date, earlier)
        if len(years) < 2:
            continue
        fp = f"suppression:{pattern_of(f)}"
        if s.scalars(sa.select(Proposal.id).where(Proposal.fingerprint == fp)).first():
            continue
        start, end = (dt.date.fromisoformat(x) for x in f["window"])
        name = spec.get("metrics", {}).get(f["metric"], {}).get("name") or f["metric"]
        label = (f.get("text") or {}).get("label") or f["detector"]
        span = f"{fmt.day_month(start)} – {fmt.day_month(end)}"
        p = Proposal(
            kind="suppression",
            text=(
                f"{name}: the same {label.lower()} finding around {span} in "
                f"{', '.join(str(y) for y in years)}. Mark it as a known pattern?"
            ),
            evidence={
                "years": years,
                "window": f["window"],
                "fingerprint": f["fingerprint"],
                "metric": f["metric"],
                "name": f"{name}, {label.lower()} around {span}",
                "line": (
                    f"Found on the same dates in {len(years)} years · "
                    f"{', '.join(str(y) for y in years)}"
                ),
                "confidence": "high" if len(years) >= 3 else "medium",
            },
            origin={"rule": "recurs_on_calendar_window", "finding": f["fingerprint"]},
            fingerprint=fp,
            status="open",
            run_id=run.id,
        )
        s.add(p)
        s.flush()
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="proposal.created",
            object_type="proposal",
            object_id=p.id,
            after={"kind": "suppression", "fingerprint": fp, "years": years},
        )
        made.append(p)
    return made


# --- join keys found by cross-source matching (6h, decided 5 Nov 2026) -------------------
#
# One proposal per source and entity, listing each tab with its match rate. Each tab is accepted
# or declined on its own (or all at once); an accepted tab gets its own join rule. A match by
# GSTIN joins on the column; a match by name never joins on raw names: accepting it writes one
# alias record per matched name (their name → the entity's name) and a join through them.


def _probe(spec: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((x for x in (spec.get("probes") or {}).get("matches", []) if x["key"] == key), None)


def _tab(spec: dict[str, Any], m: dict[str, Any]) -> dict[str, Any] | None:
    probe = _probe(spec, m["key"])
    if probe is None:
        return None
    by = "GSTIN" if m["grade"] == "identity" else "name"
    rule: dict[str, Any] = {
        "kind": "join",
        "text": f"{m['source']} · {m['table']} joins {m['to_table']} in {m['to_source']} by {by}",
        "table": probe["table"],
        "to": probe["to"],
        "on": [[m["column"], m["to_column"]]],
    }
    if m["grade"] == "name":
        rule["via"] = "alias"
    return {
        "key": m["key"],
        "table": m["table"],
        "grade": m["grade"],
        "share": m["share"],
        "matched": m["matched"],
        "total": m["total"],
        "ambiguous": m["ambiguous"],
        "ambiguous_count": m["ambiguous_count"],
        "rule": rule,
        "aliases": [] if m["grade"] == "identity" else m["pairs"],
        "status": "open",
    }


def tab_line(tab: dict[str, Any]) -> str:
    by = "GSTIN" if tab["grade"] == "identity" else "name"
    line = (
        f"{tab['table']} · {fmt.pct(tab['share'])} match ({fmt.num(tab['matched'])} of "
        f"{fmt.num(tab['total'])}) · matched by {by}"
    )
    if tab.get("ambiguous_count"):
        line += f" · {fmt.num(tab['ambiguous_count'])} ambiguous, left out"
    return line


def join_text(source: str, to_source: str, entity: str, tabs: int) -> str:
    return (
        f"Join the {source} to {to_source} {entity}s · {fmt.num(tabs)} "
        f"{'tab' if tabs == 1 else 'tabs'}"
    )


def join(s: Any, run: Run, spec: dict[str, Any], matches: list[dict[str, Any]]) -> list[Proposal]:
    """Strong matches, grouped per source and entity into one proposal. A tab already listed is
    never proposed twice; a new tab is added to the source's proposal (reopened if decided)."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for m in matches:
        if m.get("propose"):
            groups.setdefault((m["source"], m["entity"], m["to_source"]), []).append(m)
    made = []
    for (source, entity, to_source), ms in sorted(groups.items()):
        fp = f"join:{source}:{entity}"
        p = s.scalars(sa.select(Proposal).where(Proposal.fingerprint == fp)).first()
        tabs = [x for x in (_tab(spec, m) for m in sorted(ms, key=lambda m: m["key"])) if x]
        if p is None:
            if not tabs:
                continue
            p = Proposal(
                kind="join",
                text=join_text(source, to_source, entity, len(tabs)),
                evidence={"source": source, "entity": entity, "to_source": to_source, "tabs": tabs},
                origin={"rule": "cross_source_match", "source": source},
                fingerprint=fp,
                status="open",
                run_id=run.id,
            )
            s.add(p)
            s.flush()
            made.append(p)
            action = "proposal.created"
        else:
            known = {x["key"] for x in p.evidence.get("tabs", [])}
            new = [x for x in tabs if x["key"] not in known]
            if not new:
                continue
            all_tabs = [*p.evidence.get("tabs", []), *new]
            p.evidence = {**p.evidence, "tabs": all_tabs}
            p.text = join_text(source, to_source, entity, len(all_tabs))
            p.status, p.decided_at = "open", None
            action = "proposal.extended"
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action=action,
            object_type="proposal",
            object_id=p.id,
            after={"kind": "join", "fingerprint": fp, "tabs": [x["key"] for x in tabs]},
        )
    return made


def _slug(v: str, n: int = 40) -> str:
    return "".join(c if c.isalnum() else "_" for c in v.lower())[:n]


def decide(
    s: Any, p: Proposal, *, accept: bool, actor: str, now: Any, tab: str | None = None
) -> None:
    """Accept or decline a proposal. A join proposal is decided per tab (`_decide_join`); every
    other kind as a whole (`_decide_one`)."""
    if p.kind == "join":
        _decide_join(s, p, accept=accept, actor=actor, now=now, tab=tab)
    else:
        _decide_one(s, p, accept=accept, actor=actor, now=now)


def _decide_join(
    s: Any, p: Proposal, *, accept: bool, actor: str, now: Any, tab: str | None = None
) -> None:
    """Accept or decline one tab (or every open tab). An accepted tab writes its join rule (and,
    for a match by name, its alias records) as one new Ontology version; until then no metric
    uses it. A declined tab is never proposed again."""
    import hashlib

    from othor.ontology import service

    tabs = [dict(x) for x in p.evidence.get("tabs", [])]
    chosen = [x for x in tabs if x["status"] == "open" and (tab is None or x["key"] == tab)]
    if not chosen:
        return
    if accept:
        changes = []
        for x in chosen:
            key = "join_" + _slug(x["key"], 50)
            changes.append(service.put("rule", key, x["rule"], {"proposal": str(p.id)}))
            for term, means in x.get("aliases") or []:
                h = hashlib.sha256(f"{p.evidence['source']}|{term}".encode()).hexdigest()[:12]
                body = {
                    "term": term,
                    "means": means,
                    "entity": p.evidence.get("entity"),
                    "source": p.evidence["source"],
                }
                changes.append(service.put("alias", f"alias_{h}", body, {"proposal": str(p.id)}))
        service.apply(s, changes, actor=actor, reason=f"Accepted: {p.text}")
    for x in tabs:
        if x in chosen or any(c["key"] == x["key"] for c in chosen):
            x["status"] = "accepted" if accept else "declined"
    p.evidence = {**p.evidence, "tabs": tabs}
    if all(x["status"] != "open" for x in tabs):
        p.status = "accepted" if any(x["status"] == "accepted" for x in tabs) else "declined"
        p.decided_at = now
    record_event(
        s,
        actor=actor,
        action="proposal.accepted" if accept else "proposal.declined",
        object_type="proposal",
        object_id=p.id,
        before={"status": "open"},
        after={"status": p.status, "kind": p.kind, "tabs": [c["key"] for c in chosen]},
    )
    p.decided_by = actor


# --- M10a: goal and definition proposals; Accept, Decline and Undo on Home -----------------


def _fy(by: dt.date) -> str:
    return f"FY{by.year % 100:02d}"


def _aggregate(spec: dict[str, Any], key: str) -> str:
    goals = spec.get("forecast", {}).get("goals", [])
    return str(next((g.get("aggregate", "value") for g in goals if g["key"] == key), "value"))


def goal(s: Any, run: Run, spec: dict[str, Any], results: list[dict[str, Any]]) -> list[Proposal]:
    """A goal the run recorded as at risk (the goal test, never a Sweep finding) is proposed
    once per goal and fiscal year: "mark it at risk". Every figure is from the computed
    projection (`forecast.goals`)."""
    from othor.briefs import values
    from othor.engine.detect import GOAL_AT_RISK

    made = []
    for r in results:
        sl = r.get("slots") or {}
        if sl.get("kind") != "goal" or r.get("reason") != GOAL_AT_RISK:
            continue
        by = dt.date.fromisoformat(sl["by"])
        fp = f"goal:{sl['goal']}:{_fy(by)}"
        if s.scalars(sa.select(Proposal.id).where(Proposal.fingerprint == fp)).first():
            continue
        unit = spec.get("metrics", {}).get(r["metric"], {}).get("unit")
        target = values.rounded(unit, sl["target"])
        tracking = values.rounded(unit, sl["projection"])
        metric = spec.get("metrics", {}).get(r["metric"], {}).get("name") or r["metric"]
        head = (
            f"{metric} {_fy(by)} target {target}"
            if _aggregate(spec, sl["goal"]) == "fiscal_year_total"
            else f"{metric} target {target} by {fmt.day_month(by)}"
        )
        line = f"Mark as at risk · short by {values.rounded(unit, sl['short_by'])}"
        whole_band = False
        if sl.get("lower") is not None and sl.get("upper") is not None:
            line += f" · 80% band {values.band(unit, sl['lower'], sl['upper'])}"
            hi, lo, t = (Decimal(str(sl[k])) for k in ("upper", "lower", "target"))
            whole_band = hi < t if sl.get("direction", "at_least") == "at_least" else lo > t
        p = Proposal(
            kind="goal",
            text=f"{head} → tracking {tracking}",
            evidence={
                "goal": sl["goal"],
                "by": sl["by"],
                "target": sl["target"],
                "projection": sl["projection"],
                "lower": sl.get("lower"),
                "upper": sl.get("upper"),
                "short_by": sl["short_by"],
                "run_date": run.run_date.isoformat(),
                "line": line,
                "confidence": "high" if whole_band else "medium",
            },
            origin={"rule": "goal_at_risk", "goal": sl["goal"], "from": "forecast"},
            fingerprint=fp,
            status="open",
            run_id=run.id,
        )
        s.add(p)
        s.flush()
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="proposal.created",
            object_type="proposal",
            object_id=p.id,
            after={"kind": "goal", "fingerprint": fp},
        )
        made.append(p)
    return made


def definition(s: Any, run: Run, spec: dict[str, Any]) -> list[Proposal]:
    """A cross-source contradiction answered the same way twice (docs/04 §10): two answered
    Needs-confirmation questions on one metric that chose the same side, with no book-of-record
    rule for it yet, propose that side as the book of record."""
    from othor.ontology.models import Question

    rows = s.scalars(
        sa.select(Question).where(
            Question.kind == "needs_confirmation", Question.status == "answered"
        )
    ).all()
    tally: dict[tuple[str, str], list[Question]] = {}
    for q in rows:
        sub = q.subject or {}
        side = q.answer_text or ""
        if sub.get("metric") and side in (sub.get("sides") or []):
            tally.setdefault((sub["metric"], side), []).append(q)
    ruled = {
        str(r.get("concept"))
        for r in spec.get("rules", [])
        if r.get("kind") == "book_of_record" and r.get("concept")
    }
    made = []
    for (metric, side), qs in sorted(tally.items()):
        if len(qs) < 2 or metric in ruled:
            continue
        fp = f"definition:book_of_record:{metric}:{_slug(side)}"
        if s.scalars(sa.select(Proposal.id).where(Proposal.fingerprint == fp)).first():
            continue
        other = sorted({x for q in qs for x in (q.subject or {}).get("sides", []) if x != side})
        name = spec.get("metrics", {}).get(metric, {}).get("name") or metric
        rule = {
            "kind": "book_of_record",
            "concept": metric,
            "source": side,
            "over": other,
            "text": f"{name}: {side} wins over {', '.join(other)}",
        }
        p = Proposal(
            kind="definition_change",
            text=f"{name}: use {side} as the book of record",
            evidence={
                "metric": metric,
                "key": f"book_of_record_{_slug(metric)}",
                "rule": rule,
                "questions": sorted(str(q.id) for q in qs),
                "line": f"You chose {side} {len(qs)} times when the sources disagreed",
                "confidence": "high" if len(qs) >= 3 else "medium",
            },
            origin={"rule": "contradiction_answered_twice", "metric": metric},
            fingerprint=fp,
            status="open",
            run_id=run.id,
        )
        s.add(p)
        s.flush()
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="proposal.created",
            object_type="proposal",
            object_id=p.id,
            after={"kind": "definition_change", "fingerprint": fp},
        )
        made.append(p)
    return made


def _finding_metric(s: Any, ev: dict[str, Any]) -> str | None:
    """M6b's rows didn't store the metric: read it from the finding they were made from."""
    fp = ev.get("fingerprint")
    if not fp:
        return None
    m: str | None = s.scalars(sa.select(Finding.metric).where(Finding.fingerprint == fp)).first()
    return m


def _changes(s: Any, p: Proposal) -> list[Any]:
    """The Ontology change an accepted proposal writes."""
    from othor.ontology import service

    ev = p.evidence or {}
    ref = {"proposal": str(p.id)}
    if p.kind == "suppression":
        start, end = (dt.date.fromisoformat(x) for x in ev["window"])
        body = {
            "name": ev.get("name") or p.text[:200],
            "window": {
                "kind": "annual",
                "start": start.strftime("%m-%d"),
                "end": end.strftime("%m-%d"),
            },
            "metrics": [m] if (m := ev.get("metric") or _finding_metric(s, ev)) else [],
            "confirmed": True,
        }
        key = "proposed_" + _slug(p.fingerprint.removeprefix("suppression:"), 60)
        return [service.put("known_pattern", key, body, ref)]
    if p.kind == "goal":
        rec = next((r for r in service.active(s, ["goal"]) if r.key == ev["goal"]), None)
        if rec is None:
            return []
        return [service.put("goal", rec.key, {**rec.body, "at_risk_since": ev["run_date"]}, ref)]
    if p.kind == "definition_change":
        return [service.put("rule", ev["key"], ev["rule"], ref)]
    if p.kind == "duplicate_metric":  # the same number twice (12 Nov 2026): set one aside
        rec = next((r for r in service.active(s, ["metric"]) if r.key == ev["drop"]), None)
        return (
            []
            if rec is None
            else [service.put("metric", rec.key, {**rec.body, "status": "declined"}, ref)]
        )
    if p.kind == "breakdowns":  # splits found in the scan (12 Nov 2026)
        out = [service.put(t, k, b, ref) for t, k, b in ev.get("records", [])]
        current = {r.key: r for r in service.active(s, ["metric"])}
        for key, bks in sorted((ev.get("metrics") or {}).items()):
            rec = current.get(key)
            if rec is not None and not (rec.body or {}).get("breakdowns"):
                out.append(service.put("metric", key, {**rec.body, "breakdowns": list(bks)}, ref))
        return out
    if p.kind == "areas":  # Othor AI's draft of the areas (M16.1); an area the owner set wins
        current = {r.key: r for r in service.active(s, ["metric"])}
        out = []
        for key, area in sorted((ev.get("areas") or {}).items()):
            rec = current.get(key)
            if rec is not None and not (rec.body or {}).get("area"):
                out.append(service.put("metric", key, {**rec.body, "area": area}, ref))
        return out
    return []


def _decide_one(s: Any, p: Proposal, *, accept: bool, actor: str, now: Any) -> None:
    from othor.ontology import service

    if p.status != "open":
        return
    if accept:
        v = service.apply(
            s, _changes(s, p), actor=actor, reason=f"Accepted on Home: {p.text}"[:300]
        )
        p.applied_version = v.number if v is not None else None
    p.status = "accepted" if accept else "declined"
    p.decided_at, p.decided_by, p.undone_at = now, actor, None
    record_event(
        s,
        actor=actor,
        action="proposal.accepted" if accept else "proposal.declined",
        object_type="proposal",
        object_id=p.id,
        before={"status": "open"},
        after={"status": p.status, "kind": p.kind, "version": p.applied_version},
    )


def undo(s: Any, p: Proposal, *, actor: str, now: Any) -> None:
    """Undo an Accept (a new Ontology version putting the records back) or a Decline; the
    proposal is open again."""
    from othor.ontology import service

    if p.kind == "join" or p.status not in ("accepted", "declined"):
        return
    before = p.status
    version = None
    if p.status == "accepted" and p.applied_version is not None:
        nv = service.undo(s, p.applied_version, actor=actor)
        version = nv.number if nv is not None else None
    p.status, p.decided_at, p.decided_by, p.applied_version = "open", None, None, None
    p.undone_at = now
    record_event(
        s,
        actor=actor,
        action="proposal.undone",
        object_type="proposal",
        object_id=p.id,
        before={"status": before},
        after={"status": "open", "version": version},
    )


SAME_PERIODS = 6  # the last periods two numbers must agree on, every one, to look like one number


def same_numbers(spec: dict[str, Any], points: list[Any]) -> list[tuple[str, str, int, str]]:
    """Pairs of active metrics with the same total in each of their last SAME_PERIODS or more
    periods (not all zero, no gap): (keep, drop, periods, grain). The one kept is a headline
    number or a goal's, else the first by key, so the same series give the same pair."""
    metrics = spec.get("metrics", {})
    goals = {g.get("metric") for g in spec.get("forecast", {}).get("goals", [])}
    series: dict[tuple[str, str], dict[Any, Any]] = {}
    for p in points:
        if p.breakdown == "" and metrics.get(p.metric, {}).get("status") == "active":
            series.setdefault((p.metric, p.grain), {})[p.period] = p.value
    out = []
    keys = sorted(series)
    for i, (a, ga) in enumerate(keys):
        for b, gb in keys[i + 1 :]:
            if ga != gb or a == b:
                continue
            sa_, sb = series[(a, ga)], series[(b, gb)]
            common = sorted(set(sa_) & set(sb))[-12:]
            if len(common) < SAME_PERIODS:
                continue
            pairs = [(sa_[d], sb[d]) for d in common]
            if any(x is None or y is None or x != y for x, y in pairs):
                continue
            if all(x == 0 for x, _ in pairs):
                continue

            def rank(k: str) -> tuple[int, str]:
                return (0 if metrics[k].get("headline") or k in goals else 1, k)

            keep, drop = sorted((a, b), key=rank)
            out.append((keep, drop, len(common), ga))
    return out


def duplicate(s: Any, run: Run, spec: dict[str, Any], points: list[Any]) -> list[Proposal]:
    """A Home proposal for each pair of numbers that are the same number: Remove one (Accept
    takes the duplicate out of the active numbers) or Keep both (Decline, remembered). Nothing
    changes until the owner decides."""
    metrics = spec.get("metrics", {})
    made = []
    for keep, drop, n, grain in same_numbers(spec, points):
        fp = "duplicate:" + ":".join(sorted((keep, drop)))
        if s.scalars(sa.select(Proposal.id).where(Proposal.fingerprint == fp)).first():
            continue
        kn = metrics[keep].get("name") or keep
        dn = metrics[drop].get("name") or drop
        p = Proposal(
            kind="duplicate_metric",
            text=f"{dn} gives the same numbers as {kn}. Keep one?",
            evidence={
                "keep": keep,
                "drop": drop,
                "line": f"The same value in each of the last {n} {grain}s; Accept keeps {kn}",
                "confidence": "high",
            },
            origin={"rule": "same_numbers", "metric": drop},
            fingerprint=fp,
            status="open",
            run_id=run.id,
        )
        s.add(p)
        s.flush()
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="proposal.created",
            object_type="proposal",
            object_id=p.id,
            after={"kind": "duplicate_metric", "fingerprint": fp},
        )
        made.append(p)
    return made
