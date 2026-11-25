# SPDX-License-Identifier: Apache-2.0
"""The Ontology page (6a, 6a1), the metric panel (6a2, 10n) and History (6a4) — M10b.

Everything shown is computed from the live records, the last run and the compiled spec; nothing
is copied from a board. "Today" is the last complete period of the last run's series, in the
metric's unit (the brief tile's own builder), never a model's number.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.briefs import service as briefs
from othor.core import fmt
from othor.core.config import Settings
from othor.core.models import Company
from othor.engine.models import Finding, Run, Watch
from othor.engine.store import RunFiles, read_json
from othor.ontology import content, service
from othor.ontology.models import OntologyRecord, OntologyVersion, Question, Spec

SECTION_OF = {
    "business_fact": "The business",
    "goal": "The business",
    "metric": "Numbers",
    "metric_family": "Numbers",
    "breakdown": "Numbers",
    "rule": "Numbers",
    "vocabulary": "Names and words",
    "alias": "Names and words",
    "customer": "Names and words",
    "competitor": "Names and words",
    "owner": "Names and words",
    "known_pattern": "Known patterns",
    "weather": "The business",  # the state of the business's thresholds (v0.12)
}
FILTERS = ("all", "confirmed", "needs", "unbriefed")


# --- the last run --------------------------------------------------------------------------


@dataclass
class RunData:
    run: Run | None
    spec: dict[str, Any]
    record: list[dict[str, Any]] = field(default_factory=list)
    goals: list[dict[str, Any]] = field(default_factory=list)
    stale: dict[str, dt.date] = field(default_factory=dict)
    files: RunFiles | None = None

    @property
    def points(self) -> list[Any]:
        """Every point of the run, read when asked for (load test, 25 Nov 2026: a page that
        shows a few metrics reads them through `run_cache.by_metric` instead)."""
        if self.files is None:
            return []
        from othor.web import run_cache

        return run_cache.points(self.files.series)


def run_data(s: Session, settings: Settings) -> RunData:
    from othor.briefs.step import stale_as_of
    from othor.web.home_view import latest_run

    run = latest_run(s)
    if run is None:
        return RunData(None, {})
    row = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).first()
    spec = dict(row.compiled) if row else {}
    files = RunFiles(settings.runs_dir(run.company_id), run.id)
    outputs = read_json(files.outputs_json) or {}
    forecasts = read_json(files.outputs / "forecasts.json") or {}
    context = read_json(files.inputs / "context.json") or {}
    return RunData(
        run,
        spec,
        (context.get("forecasts") or {}).get("of_record") or [],
        forecasts.get("goals") or [],
        stale_as_of(spec, outputs.get("coverage") or {}),
        files,
    )


def tile_for(rd: RunData, key: str) -> dict[str, Any] | None:
    from othor.briefs import tiles
    from othor.briefs.assemble import _known

    if rd.run is None or key not in rd.spec.get("metrics", {}):
        return None
    from othor.web import run_cache

    # M15.1: only this metric's points (the tile filters them as before, over a short list)
    points = list(run_cache.by_metric(rd.files.series).get(key, ())) if rd.files else rd.points
    return tiles.tile(
        rd.spec,
        key,
        points,
        rd.record,
        rd.goals,
        rd.run.run_date,
        stale_as_of=rd.stale,
        known=_known(rd.spec, key, rd.run.run_date),
    )


# --- citations ---------------------------------------------------------------------------------


def cited(s: Session, ref: dict[str, Any] | None, at: dt.datetime | None = None) -> str:
    """Where a record came from, in words ("Your answer on Home · 30 Oct")."""
    ref = ref or {}
    if "answer" in ref:
        q = None
        try:
            import uuid

            q = s.scalars(
                sa.select(Question).where(Question.id == uuid.UUID(str(ref["answer"])))
            ).first()
        except ValueError:
            pass
        day = q.answered_at.date() if q and q.answered_at else None
        if day is None:
            try:
                day = dt.date.fromisoformat(str(ref["answer"]))
            except ValueError:
                day = at.date() if at else None
        return "Your answer on Home" + (f" · {fmt.day_month(day)}" if day else "")
    if "proposal" in ref:
        return "Proposal on Home" + (f" · {fmt.day_month(at.date())}" if at else "")
    if "restore" in ref:
        return f"Restore v{ref['restore']}"
    if "undo" in ref:
        return f"Undo v{ref['undo']}"
    if "chat" in ref:
        return "Othor AI chat" + (f" · {fmt.day_month(at.date())}" if at else "")
    for k in ("upload", "from", "source"):
        if ref.get(k):
            return str(ref[k])
    return "Your Business Model"


# --- record lines ------------------------------------------------------------------------------


def line(r: OntologyRecord | Any) -> str:
    """One record as the sentence the page shows."""
    b = r.body
    t = r.type
    if t == "business_fact":
        return str(b.get("text", ""))
    if t == "vocabulary":
        if b.get("kind") == "prefix_map":
            entries = ", ".join(f"{v} ({k})" for k, v in sorted(b.get("entries", {}).items()))
            return f"{b.get('term', '')}: {b.get('text') or entries}"
        if b.get("kind") == "values" and b.get("values"):
            return f"{b.get('term', '')}: {', '.join(b['values'])}"
        return f"{b.get('term', '')} {b.get('text', '')}".strip()
    if t == "alias":
        return f"{b.get('term', '')} means {b.get('means', '')}"
    if t in ("customer", "competitor"):
        return str(b.get("name") or r.key)
    if t == "owner":
        return f"{b.get('name', '')}" + (f", {b['role']}" if b.get("role") else "")
    if t == "known_pattern":
        return str(b.get("name") or r.key)
    if t == "goal":
        return str(b.get("name") or r.key)
    if t == "metric":
        return str(b.get("name") or r.key)
    if t == "rule":
        return str(b.get("text") or b.get("kind", "").replace("_", " "))
    if t == "weather":
        parts = [f"{k.replace('_', ' ')} {v}" for k, v in sorted(b.items())]
        return "State-of-the-business thresholds: " + ", ".join(parts)
    return str(b.get("name") or b.get("text") or r.key)


def bold_term(r: OntologyRecord) -> tuple[str, str]:
    """(bold lead, rest) for a Names and words line."""
    text = line(r)
    term = str(r.body.get("term") or r.body.get("name") or "")
    if term and text.startswith(term):
        return term, text[len(term) :]
    return "", text


# --- the page ----------------------------------------------------------------------------------


def goal_lines(s: Session, rd: RunData) -> list[dict[str, Any]]:
    from othor.briefs.assemble import goal_lines as brief_goal_lines

    recs = service.active(s, ["goal"])
    lines = (
        {g["key"]: g for g in brief_goal_lines(rd.spec, [r.key for r in recs], rd.goals)}
        if rd.spec
        else {}
    )
    out = []
    for r in recs:
        g = lines.get(r.key)
        track, tone = "", "muted"
        if g is not None:
            track = g["text"].split(" · tracking ", 1)[-1]
            track = "tracking " + track if "tracking" in g["text"] else ""
            tone = {"off": "neg", "risk": "warn", "ok": "accent"}.get(g["tone"], "muted")
        at_risk = r.body.get("at_risk_since")
        mark = ""
        if at_risk:
            since = fmt.day_month(dt.date.fromisoformat(at_risk))
            mark = (
                f"at risk since {since}"
                if g and not g["on_track"]
                else f"on track · marked at risk {since}"
            )
        out.append(
            {
                "key": r.key,
                "text": line(r),
                "track": track,
                "tone": tone,
                "mark": mark,
                "source": cited(s, r.source_ref, r.created_at),
            }
        )
    return out


GOAL_GRAINS = ("month",)  # a goal reads a number's monthly series and its monthly forecast


def _goal_unit(rd: RunData, metric: str) -> str | None:
    return (rd.spec.get("metrics", {}).get(metric) or {}).get("unit")


from othor.ontology.goal_targets import goal_text  # noqa: E402


def goal_rows(s: Session, rd: RunData, today: dt.date) -> list[dict[str, Any]]:
    """0.28 (M28.1): every goal, with how it's doing tonight (code's numbers only): the chance of
    meeting it and where it's heading (goals_v2), on track / at risk / off track (the brief's
    tone), reached or past (marked, never deleted), the briefs that watch it, where it came from."""
    from othor.briefs import values
    from othor.briefs.assemble import goal_lines as brief_goal_lines

    recs = service.active(s, ["goal"])
    names = {k: str(m.get("name") or k) for k, m in rd.spec.get("metrics", {}).items()}
    tones = (
        {g["key"]: g for g in brief_goal_lines(rd.spec, [r.key for r in recs], rd.goals)}
        if rd.spec
        else {}
    )
    v1 = {g["key"]: g for g in rd.goals}
    fc = (read_json(rd.files.outputs / "forecasts.json") or {}) if rd.files else {}
    v2 = {g["key"]: g for g in fc.get("goals_v2") or []}
    watch: dict[str, list[str]] = {}
    for b in briefs.active(s):
        v = briefs.current(s, b)
        for k in (v.body if v else {}).get("goals") or []:
            watch.setdefault(k, []).append(b.name)
    out = []
    for r in sorted(recs, key=lambda r: (str(r.body.get("by")), r.key)):
        b = r.body
        unit = _goal_unit(rd, str(b.get("metric")))
        by = dt.date.fromisoformat(str(b["by"]))
        target = Decimal(str(b["target"]))
        up = b.get("direction", "at_least") == "at_least"
        g1, g2, tl = v1.get(r.key) or {}, v2.get(r.key) or {}, tones.get(r.key)
        to_date = Decimal(str(g1["to_date"])) if g1.get("to_date") is not None else None
        reached = to_date is not None and up and to_date >= target
        if reached:
            state, tone = "Reached", "accent"
        elif by < today:
            state, tone = "Past", "muted"
        elif tl is not None:
            state = {"off": "Off track", "risk": "At risk", "ok": "On track"}[tl["tone"]]
            tone = {"off": "neg", "risk": "warn", "ok": "accent"}[tl["tone"]]
        else:
            state, tone = "Not forecast yet", "muted"
        bits = []
        if g2.get("probability") is not None and not reached and by >= today:
            bits.append(f"{g2['probability']}% likely")
        if g2.get("projection") is not None and not reached and by >= today:
            proj = values.goal_value(unit, g2["projection"], like=b["target"])
            bits.append(f"heading for {proj}")
        out.append(
            {
                "key": r.key,
                "text": goal_text(b, unit, names),
                "state": state,
                "tone": tone,
                "track": " · ".join([state, *bits]),
                "briefs": ", ".join(sorted(watch.get(r.key, []))),
                "source": cited(s, r.source_ref, r.created_at),
                "href": f"/ontology?panel=othor&subject=ontology:goal:{r.key}",
            }
        )
    return out


def in_briefs(s: Session) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for b in briefs.active(s):
        v = briefs.current(s, b)
        body = v.body if v else {}
        keys = set(body.get("headline") or []) | set((body.get("scope") or {}).get("metrics") or [])
        for k in keys:
            out.setdefault(k, []).append(b.name)
    return out


def number_rows(s: Session, rd: RunData) -> list[dict[str, Any]]:
    used = in_briefs(s)
    spec_m = rd.spec.get("metrics", {})
    order = {k: i for i, k in enumerate(rd.spec.get("order", []))}
    rows = []
    for r in service.active(s, ["metric"]):
        b = r.body
        if b.get("listed") is False or b.get("status") in ("suggested", "declined"):
            continue
        needs = b.get("status") == "needs_source"
        e = spec_m.get(r.key, {})
        t = None if needs else tile_for(rd, r.key)
        bds = [
            rd.spec.get("breakdowns", {}).get(x, {}).get("name", x)
            for x in b.get("breakdowns") or []
        ]
        today = "No data yet"  # v0.11: a confirmed number the run hasn't computed yet
        if t is not None:
            today = t["value"] + (f" · {t['stale']}" if t.get("stale") else "")
        rows.append(
            {
                "key": r.key,
                "name": b.get("name") or r.key,
                "sub": "Needs a source · " + str(b.get("needs") or "not computed yet")
                if needs
                else " · ".join(
                    [
                        f"{b.get('grain') or e.get('grain') or 'month'}ly".replace(
                            "dayly", "daily"
                        ),
                        ", ".join(bds).lower(),
                    ]
                ).strip(" ·"),
                # 6a1: the drafted definition stays; what it needs goes under the name
                "definition": str(b.get("description") or ""),
                "source": "—" if needs else str(e.get("source_label") or ""),
                "today": "—" if needs else today,
                "nodata": not needs and t is None,
                "tile": t,  # 0.27.3: the number's period and comparison, for the numbers tool
                "kind": "derived" if b.get("formula") else str(b.get("kind") or "flow"),
                "briefs": ", ".join(sorted(used.get(r.key, []))) or "—",
                "needs": needs,
                "headline": bool(b.get("headline")),
                "rank": (0 if b.get("headline") else 1, order.get(r.key, 999), r.key),
            }
        )
    return sorted(rows, key=lambda x: x["rank"])


def filter_rows(rows: list[dict[str, Any]], f: str) -> list[dict[str, Any]]:
    if f == "confirmed":
        return [r for r in rows if not r["needs"]]
    if f == "needs":
        return [r for r in rows if r["needs"]]
    if f == "unbriefed":
        return [r for r in rows if r["briefs"] == "—"]
    return rows


def filter_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {f: len(filter_rows(rows, f)) for f in FILTERS}


def settings_line(company: Company) -> str:
    """'Fiscal year April to March · ₹ in lakh and crore · IST' from Settings (7a)."""
    import calendar

    start = int(company.fiscal_year_start_month or 4)
    end = (start - 2) % 12 + 1
    from othor.web.routes.auth import CURRENCY_LINE

    tz = fmt.tz_abbr(company.timezone or "Asia/Kolkata", dt.date.today())
    money = CURRENCY_LINE.get(company.currency or "INR", company.currency or "INR")
    return (
        f"Fiscal year {calendar.month_name[start]} to {calendar.month_name[end]} · {money} · {tz}"
    )


FLOOR_LEAD = "Problems worth reporting"


def floor_line(s: Session, settings: Settings, company: Company) -> dict[str, Any]:
    """#107 (owner, 21 Nov 2026): the floor (M15.6) as a line of The business, edited in place.
    The owner's `materiality` record, else the currency's default; and a suggestion, 1% of a
    typical month's revenue from the last run, computed by code. The same record the night's
    spec compiles from, so moving it here changes no number."""
    from othor.briefs import weather
    from othor.engine.models import DETECTING_KINDS
    from othor.ontology.compiler.build import DEFAULT_FLOOR

    rec = next((r for r in service.active(s, ["materiality"])), None)
    amount = (
        Decimal(rec.body["amount"]) if rec else Decimal(DEFAULT_FLOOR.get(company.currency, "5000"))
    )
    suggest = None
    run = s.scalars(
        sa.select(Run)
        .where(Run.status.in_(("done", "partial")), Run.kind.in_(DETECTING_KINDS))
        .order_by(Run.number.desc())
        .limit(1)
    ).first()
    if run is not None:
        spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one()
        base = read_json(RunFiles(settings.runs_dir(company.id), run.id).baselines) or {}
        got = weather.revenue(dict(spec.compiled), base)
        if got is not None and got[1] > 0:
            suggest = (got[1] / 100).quantize(Decimal("1000"))
    shown = fmt.money(amount)
    return {
        "amount": amount,
        "owner": rec is not None,
        "text": f"{FLOOR_LEAD} · over {shown}",
        "value": shown,  # typed back as money: "₹5 L", "5 lakh" and "500000" all read
        "source": (f"You · {fmt.day_month(rec.created_at.date())}" if rec else "The default"),
        "hint": (f"1% of a typical month’s revenue is {fmt.money(suggest)}" if suggest else ""),
    }


def parse_floor(text: str) -> Decimal:
    """The floor as typed: "₹5 L", "5 lakh", "$5K", "500000" or "5,00,000". Whole rupees or
    dollars above zero; ValueError otherwise."""
    from decimal import InvalidOperation

    from othor.briefs.values import parse

    try:
        v = parse("inr", text)
    except (ValueError, InvalidOperation):
        raise ValueError(text) from None
    v = v.quantize(Decimal("1"))
    if v <= 0 or v >= Decimal("1e12"):
        raise ValueError(text)
    return v


def name_lines(s: Session) -> list[dict[str, Any]]:
    out = []
    customers = service.active(s, ["customer"])
    if customers:
        out.append(
            {
                "type": "customer",
                "key": "customers",
                "term": "Key customers",
                "meaning": ", ".join(line(r) for r in customers),
                "source": cited(s, customers[0].source_ref, customers[0].created_at),
            }
        )
    for r in service.active(s, ["vocabulary", "alias", "competitor", "owner"]):
        lead, rest = bold_term(r)
        out.append(
            {
                "type": r.type,
                "key": r.key,
                # v0.11: three columns, the term · its meaning · its origin
                "term": lead,
                "meaning": rest.lstrip(" :,") if lead else rest,
                "source": cited(s, r.source_ref, r.created_at),
            }
        )
    return out


def pattern_lines(s: Session) -> list[dict[str, Any]]:
    open_q = {
        (q.subject or {}).get("pattern")
        for q in s.scalars(sa.select(Question).where(Question.status.in_(("open", "skipped"))))
        if q.kind == "pattern"
    }
    out = []
    for r in service.active(s, ["known_pattern"]):
        unconfirmed = r.body.get("confirmed", True) is False
        out.append(
            {
                "key": r.key,
                "text": line(r),
                "source": cited(s, r.source_ref, r.created_at),
                "pending": "Same dates every year? Answer on Home"
                if unconfirmed and r.key in open_q
                else ("Not confirmed yet" if unconfirmed else ""),
            }
        )
    return out


def decided_lines(s: Session) -> list[dict[str, Any]]:
    """Let Othor AI decide (v0.12): each record Othor AI decided and the owner hasn't confirmed,
    with its computed evidence and date, Confirm · Change · Undo."""
    from othor.home import answers

    out = []
    for r in answers.decided_records(s):
        ref = r.source_ref or {}
        qid = str(ref.get("answer") or "")
        try:
            on = dt.date.fromisoformat(str(ref.get("decided_on")))
        except ValueError:
            on = r.created_at.date() if r.created_at else None
        out.append(
            {
                "id": str(r.id),
                "text": answers.record_line(r),
                "evidence": str(ref.get("evidence") or ""),
                "source": "Decided by Othor AI" + (f" · {fmt.day_month(on)}" if on else ""),
                "question": qid,
                "change": "/home?panel=othor&subject=questions" if qid else "",
            }
        )
    return out


def suppressed_this_month(s: Session, today: dt.date) -> int:
    first = today.replace(day=1)
    rows = s.execute(
        sa.select(Finding.fingerprint)
        .join(Run, Run.id == Finding.run_id)
        .where(Finding.status == "covered", Run.run_date >= first)
    ).all()
    return len({r[0] for r in rows})


def level_view(s: Session, settings: Settings, *, upto: int = 3) -> dict[str, Any]:
    """Business Model 2.4: the level card (L1, code's counts), each missing item's ready drafts
    found in the files and sources (L3, the chat's sets) and whether Othor AI can look (a model).
    `upto`: the levels shown (onboarding's end shows 1 and 2, L5)."""
    from othor.ai.gateway import model_status
    from othor.chat import level_drafts
    from othor.core.models import utcnow
    from othor.ontology import levels, size_numbers

    v = levels.check(s).view()
    found = level_drafts.drafts(s)
    sized = size_numbers.level_rows(s)  # #76: a number for the company's size that fits the item
    for g in v["groups"]:
        for row in g["rows"]:
            row["drafts"] = [] if row["done"] else found.get(row["key"], [])
            row["size"] = [] if row["done"] else sized.get(row["key"], [])
    v["groups"] = [g for g in v["groups"] if g["level"] <= upto]
    available, _ = model_status(settings, s, utcnow())
    v["can_find"] = available and bool(v["left"])
    v["has_drafts"] = any(row["drafts"] for g in v["groups"] for row in g["rows"])
    return v


def page(
    s: Session, settings: Settings, company: Company, today: dt.date, numbers_filter: str | None
) -> dict[str, Any]:
    from othor.home import answers
    from othor.web.home_view import proposal_rows

    rd = run_data(s, settings)
    v = service.history(s)[0] if service.confirmed(s) else None
    rows = number_rows(s, rd)
    facts = [
        {"text": line(r), "source": cited(s, r.source_ref, r.created_at)}
        for r in service.active(s, ["business_fact"])
        if not r.key.startswith("setting:")  # Settings' own (M15.6): the settings line covers them
    ]
    facts.append({"text": settings_line(company), "source": "Settings"})
    floor = floor_line(s, settings, company)  # #107: the floor, edited in place on this line
    props, qs = len(proposal_rows(s)), len(answers.queue(s, today))
    home = []
    if props:
        home.append(f"{fmt.num(props)} proposal{'s' if props != 1 else ''}")
    if qs:
        home.append(f"{fmt.num(qs)} question{'s' if qs != 1 else ''}")
    later = len(answers.set_aside(s, today))  # set aside with Ask me later: still waiting
    if later:
        home.append(f"{fmt.num(later)} set aside")
    f = numbers_filter if numbers_filter in FILTERS else None
    # the short list: the first six, plus every number that needs a source (it needs the owner;
    # 6a shows it in red with Fix without opening All)
    shown = filter_rows(rows, f or "all") if f else rows[:6] + [r for r in rows[6:] if r["needs"]]
    pats = pattern_lines(s)
    names = name_lines(s)
    business = facts
    goals = goal_rows(s, rd, today)

    return {
        "levels": level_view(s, settings),  # 2.4 L1, L3: the level card, its checklist, drafts
        "version": v.number if v else 0,
        "changed": v.created_at.date() if v else None,
        "home_line": (" · ".join(home) + " waiting on Home") if home else "",
        "home_count": props + qs + later,  # the title row's button: "N to review on Home"
        "business": business,
        "floor": floor,
        "goals": goals,
        "business_count": len(business) + len(goals),
        "numbers": shown,
        "numbers_all": len(rows),
        "numbers_needs": sum(1 for r in rows if r["needs"]),
        "filter": f,
        "counts": filter_counts(rows),
        "names": names,
        "patterns": pats,
        "suppressed": suppressed_this_month(s, today),
        "decided": decided_lines(s),
        "expanded": f is not None,
    }


# --- 6a2 / 10n · the metric panel ----------------------------------------------------------


def metric_panel(
    s: Session, settings: Settings, company: Company, key: str, today: dt.date
) -> dict[str, Any] | None:
    from othor.engine import sweep_text
    from othor.web import sweep_view as sv

    rec = next((r for r in service.active(s, ["metric"]) if r.key == key), None)
    if rec is None:
        return None
    rd = run_data(s, settings)
    b = rec.body
    needs = b.get("status") == "needs_source"
    v = service.current_number(s)
    first = s.scalar(
        sa.select(sa.func.min(OntologyRecord.version_from)).where(
            OntologyRecord.type == "metric", OntologyRecord.key == key
        )
    )
    base = {
        "key": key,
        "name": b.get("name") or key,
        "grain": (b.get("grain") or "month")
        .capitalize()
        .replace("Day", "Daily")
        .replace("Month", "Monthly")
        .replace("Week", "Weekly"),
        "needs": needs,
        "version": v,
        "since": f"v{first}" if first else "",
        "source": cited(s, rec.source_ref, rec.created_at),
        **area_choice(s, b),
    }
    used = in_briefs(s)
    if needs:
        return {**base, **needs_source(s, rd, rec, used)}
    e = rd.spec.get("metrics", {}).get(key, {})
    t = tile_for(rd, key)
    runs = sv.detecting_runs(s)
    rv = sv.load(s, settings, runs[0], today) if runs else None
    tab = (
        sv.metric_tab(rv, s, {"metric": key})
        if rv
        else {"definition": [], "rules": [], "source": []}
    )
    grain = e.get("grain") or "month"
    sql = (e.get("queries") or {}).get(f"{grain}:") or (e.get("queries") or {}).get("as_of:") or ""
    if sql:
        sv_ = rd.spec.get("ontology_version", v)
        sql = f"-- {key} · {grain} · spec v{sv_} · generated from the definition\n{sql}"
    goal = next(
        (
            g
            for g in goal_lines(s, rd)
            if any(x.get("metric") == key for x in rd.goals if x["key"] == g["key"])
        ),
        None,
    )
    cov = (rv.doc.get("coverage") if rv else []) or []
    tested = sorted({r["detector"] for r in (rv.results if rv else []) if r.get("metric") == key})
    open_f = [f for f in (rv.shown if rv else []) if f["metric"] == key and f["status"] == "open"]
    del cov
    findings = []
    since = today - dt.timedelta(days=30)
    for f in s.scalars(
        sa.select(Finding)
        .join(Run, Run.id == Finding.run_id)
        .where(Finding.metric == key, Run.run_date >= since)
    ):
        title = (f.body.get("text") or {}).get("title") or f.title_text or f.detector
        findings.append(
            (f.fingerprint, title, sweep_text.LABELS.get(f.detector, f.detector), f.status)
        )
    seen: dict[str, Any] = {}
    for fp, title, label, status in findings:
        seen[fp] = (title, label, status)
    watches = sorted(
        (
            w
            for w in s.scalars(sa.select(Watch).where(Watch.stopped_at.is_(None)))
            if (w.indicator or {}).get("metric") == key
        ),
        key=lambda w: (w.status != "breached", -breach_size(w.last_value, w.threshold), w.label),
    )
    return {
        **base,
        "confirmed_line": (
            f"Confirmed · {fmt.day_month(rec.created_at.date())} · Business Model v{v}"
        ),
        "tile": t,
        "chart": panel_chart(rd, key),
        "description": str(b.get("description") or ""),
        "definition": tab.get("definition", []),
        "where": tab.get("source", []),
        "sql": sql,
        "run_line": run_line(s, rd, key, e),
        "rules": [(r["kind"].capitalize(), r["text"]) for r in tab.get("rules", [])],
        "add_href": add_href(s, key),
        "briefs": sorted(used.get(key, [])),
        "goal": goal,
        "sweep": (
            f"{fmt.num(len(tested))} of {fmt.num(len(sv.shipped()))} detectors · "
            f"{fmt.num(len(open_f))} finding{'s' if len(open_f) != 1 else ''} open"
        ),
        "watching": [w.label for w in watches],
        "findings": [
            {"fp": fp, "title": t_, "label": lab, "status": st}
            for fp, (t_, lab, st) in list(seen.items())[:6]
        ],
        "changed": definition_changed(rd, key),
    }


def area_choice(s: Session, body: dict[str, Any]) -> dict[str, Any]:
    """The Area select (M16.1): the short list, the owner's own names, then Other numbers."""
    from othor.ontology import areas

    names = areas.in_use(r.body or {} for r in service.active(s, ["metric"]))
    return {
        "area": body.get("area") or "",
        "area_options": [(a, a) for a in names] + [("", areas.OTHER)],
    }


def run_line(s: Session, rd: RunData, key: str, e: dict[str, Any]) -> str:
    from othor.connectors.models import RunQuery

    parts = [str(e.get("run_location") or e.get("source_label") or "")]
    if rd.run is not None:
        ms = s.scalars(
            sa.select(RunQuery.duration_ms).where(
                RunQuery.run_id == rd.run.id,
                RunQuery.hash.in_(
                    [
                        __import__("hashlib").sha256(q.encode()).hexdigest()
                        for q in (e.get("queries") or {}).values()
                    ]
                ),
            )
        ).all()
        if ms:
            parts.append(f"last run {Decimal(sum(ms)) / 1000:.2f} s")
        parts.append(f"run #{rd.run.number:04d}")
    return " · ".join(p for p in parts if p)


def definition_changed(rd: RunData, key: str) -> str:
    if rd.files is None:
        return ""
    src = rd.spec.get("metrics", {}).get(key, {}).get("source")
    if not src:
        return ""
    meta = read_json(rd.files.source_meta(src)) or {}
    vs = [v for v in meta.get("versions", []) if v.get("metric") == key and v.get("until")]
    if not vs:
        return ""
    last = dt.date.fromisoformat(vs[-1]["until"])
    return f"Definition changed {fmt.day_month(last)} · earlier values used the old definition"


def panel_chart(rd: RunData, key: str) -> Any:
    """12 periods with the band, as a ChartSpec (rendered by render_svg)."""
    from othor.briefs import tiles, values
    from othor.charts.spec import Band, ChartSpec, Period, Series

    m = rd.spec.get("metrics", {}).get(key) or {}
    g = tiles.grain(m)
    from othor.web import run_cache

    mine = list(run_cache.by_metric(rd.files.series).get(key, ())) if rd.files else rd.points
    pts = tiles._series(mine, key, g)[-12:]
    if len(pts) < 2:
        return None
    unit = m.get("unit")
    by = {
        r["period"]: r
        for r in rd.record
        if r["metric"] == key and r["grain"] == g and r.get("lower") is not None
    }
    band_pts = [(p.isoformat(), by[p.isoformat()]) for p, _ in pts if p.isoformat() in by]
    band = (
        Band(
            key,
            0.8,
            tuple((p, values.chart_value(unit, r["lower"])) for p, r in band_pts),
            tuple((p, values.chart_value(unit, r["upper"])) for p, r in band_pts),
        )
        if band_pts
        else None
    )
    name = str(m.get("name") or key)
    return ChartSpec(
        id=f"metric:{key}:{g}:{pts[-1][0].isoformat()}",
        kind="line",
        title=f"{name} · last {len(pts)} {g}s",
        unit=values.chart_unit(unit),
        period=Period(g, pts[0][0].isoformat(), pts[-1][0].isoformat()),  # type: ignore[arg-type]
        series=(
            Series(
                key,
                name,
                "neutral",
                tuple((p.isoformat(), values.chart_value(unit, v)) for p, v in pts),
            ),
        ),  # type: ignore[arg-type]
        band=band,
        alt_text=f"{name}, last {len(pts)} {g}s to {pts[-1][0].isoformat()}: "
        f"{values.text(unit, pts[-1][1])}" + (" against its forecast band." if band else "."),
        provenance={"metric_id": key},
    )


def needs_source(
    s: Session, rd: RunData, rec: OntologyRecord, used: dict[str, list[str]]
) -> dict[str, Any]:
    """10n: what it needs, where it could come from (each connected source checked against its
    scan's columns, by name), what is waiting on it."""
    from othor.connectors.models import Source, SourceColumn, SourceTable

    b = rec.body
    words = {w.strip(" ,.()").lower() for w in str(b.get("needs") or "").replace("/", " ").split()}
    words = {w for w in words if len(w) > 3 and w not in {"with", "from", "that", "each", "list"}}
    rows = []
    for src in s.scalars(
        sa.select(Source).where(Source.deleted_at.is_(None)).order_by(Source.name)
    ):
        cols = s.execute(
            sa.select(SourceTable.name, SourceColumn.name)
            .join(SourceColumn, SourceColumn.table_id == SourceTable.id)
            .where(SourceTable.source_id == src.id, SourceColumn.is_personal.is_(False))
        ).all()
        hits = sorted({f"{t}.{c}" for t, c in cols if any(w in c.lower() for w in words)})[:4]
        kind = {"postgres": "PostgreSQL", "mysql": "MySQL", "gsheets": "Google Sheets"}.get(
            src.kind, src.kind
        )
        rows.append(
            {
                "source": f"{kind} · {src.name}",
                "found": ("Has " + ", ".join(hits)) if hits else "No matching columns",
                "checked": True,
            }
        )
    waiting = [f"{n} · headline number" for n in sorted(used.get(rec.key, []))]
    for c in (
        (read_json(rd.files.outputs_json) or {}).get("sweep", {}).get("coverage", [])
        if rd.files
        else []
    ):
        if c.get("status") == "could_not_run" and rec.key in str(c.get("notes")):
            waiting.append(f"Sweep · {c['detector'].replace('_', ' ')} not tested")
    goals = [r for r in service.active(s, ["goal"]) if r.body.get("metric") == rec.key]
    waiting.append("Goal · " + (", ".join(line(g) for g in goals) if goals else "none"))
    return {
        "needs_line": str(b.get("needs") or ""),
        "description": str(b.get("description") or ""),
        "breakdowns": ", ".join(b.get("breakdowns") or []) or "—",
        "seen_in": cited(s, rec.source_ref, rec.created_at),
        "sources": rows,
        "waiting": waiting,
    }


# --- 6a4 · History -----------------------------------------------------------------------------


def where_from(v: OntologyVersion) -> str:
    ref = v.source_ref or {}
    if content.is_citation_only(v):
        return "Source updated"
    if "restore" in ref:
        return f"Restore v{ref['restore']}"
    if "undo" in ref:
        return f"Undo v{ref['undo']}"
    if "setup_changes" in ref:
        return "Setup"
    reason = v.reason or ""
    if reason.startswith("Answered on Home"):
        return "Answered on Home"
    if reason.startswith("Accepted on Home") or reason.startswith("Accepted:"):
        return "Proposal on Home"
    if reason.startswith("Tell Othor AI") or reason.startswith("Othor AI chat"):
        return "Othor AI chat"  # "Tell Othor AI" until 0.26: the same, one chat now
    return "Metrics" if "metric" in reason.lower() else "Business Model"


def effect(v: OntologyVersion) -> str:
    changes = (v.diff or {}).get("changes") or []
    if content.is_citation_only(v):
        return "no change to the numbers"
    if v.number == 1:  # what setup made
        nm = sum(1 for c in changes if c["type"] == "metric")
        ng = sum(1 for c in changes if c["type"] == "goal")
        return f"{fmt.num(nm)} numbers, {fmt.num(ng)} goal{'s' if ng != 1 else ''}"
    metrics = sorted({c["key"] for c in changes if c["type"] == "metric"})
    patterns = [c for c in changes if c["type"] == "known_pattern"]
    parts = []
    if metrics:
        parts.append(f"{fmt.num(len(metrics))} number{'s' if len(metrics) != 1 else ''} changed")
    if patterns:
        parts.append(f"{fmt.num(len(patterns))} known pattern{'s' if len(patterns) != 1 else ''}")
    if not parts:
        parts.append(f"{fmt.num(len(changes))} line{'s' if len(changes) != 1 else ''}")
    return " · ".join(parts)


def history(s: Session, names: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    versions = service.history(s)
    current = versions[0].number if versions else 0
    for v in versions:
        same = content.same_as(s, v)
        title = short_title(v.reason)
        if v.number == 1:
            trail = (v.source_ref or {}).get("setup_changes")
            title = "First Business Model from onboarding"
            meta_extra = (
                f" · {fmt.num(trail)} change{'s' if trail != 1 else ''} before confirmation"
                if trail
                else ""
            )
        else:
            meta_extra = ""
        who = _version_who(v.created_by, names)
        meta = f"{where_from(v)} · {who} · {effect(v)}{meta_extra}"
        if same is not None:
            meta += f" · same content as v{same}"
        rows.append(
            {
                "number": v.number,
                "date": v.created_at.date(),
                "title": title,
                "meta": meta,
                "current": v.number == current,
            }
        )
    return rows


def compare(s: Session, a: int, b: int) -> dict[str, Any] | None:
    """Two versions side by side: each added, changed or removed line, by section."""
    va = s.scalars(sa.select(OntologyVersion).where(OntologyVersion.number == a)).first()
    vb = s.scalars(sa.select(OntologyVersion).where(OntologyVersion.number == b)).first()
    if va is None or vb is None:
        return None
    ra = {(r.type, r.key): r for r in service.active_at(s, a)}
    rb = {(r.type, r.key): r for r in service.active_at(s, b)}
    sections: dict[str, list[dict[str, str]]] = {}
    for k in sorted(set(ra) | set(rb)):
        x, y = ra.get(k), rb.get(k)
        if x is not None and y is not None and x.body == y.body:
            continue
        sec = SECTION_OF.get(k[0], "Other")
        sections.setdefault(sec, []).append(
            {
                "op": "Added" if x is None else ("Removed" if y is None else "Changed"),
                "before": line(x) if x is not None else "",
                "after": line(y) if y is not None else "",
                "detail": _detail(x, y),
                "same_line": x is not None and y is not None and line(x) == line(y),
            }
        )
    order = ["The business", "Numbers", "Names and words", "Known patterns", "Other"]
    return {
        "a": a,
        "b": b,
        "same": bool(va.content_hash and va.content_hash == vb.content_hash),
        "sections": [(sec, sections[sec]) for sec in order if sec in sections],
    }


def _detail(x: Any, y: Any) -> str:
    """For a changed record whose line reads the same: which fields changed, from what to what."""
    if x is None or y is None or line(x) != line(y):
        return ""
    parts = []
    for k in sorted(set(x.body) | set(y.body)):
        a, b = x.body.get(k), y.body.get(k)
        if a == b:
            continue
        parts.append(f"{k.replace('_', ' ')}: {_short(a)} → {_short(b)}")
    return " · ".join(parts)


def _short(v: Any) -> str:
    if v is None or v == "" or v == []:
        return "—"
    if isinstance(v, str):
        try:
            return fmt.day_month(dt.date.fromisoformat(v))
        except ValueError:
            return v if len(v) <= 40 else v[:39] + "…"
    if isinstance(v, list):
        return ", ".join(_short(x) for x in v)
    return "changed"


def _version_who(raw: str, names: dict[str, str]) -> str:
    """Who made a version: the person, "Othor AI (asked by …)" for a change it applied on their
    request (0.35), "Othor AI" for the system's own."""
    from othor.core import actors

    a = actors.parse(raw)
    if a.kind == actors.SYSTEM_KIND:
        return "Othor AI"
    if a.kind == actors.USER:
        return names.get(a.user_id or "", "Super admin")
    return actors.label(a, names)


def user_names(s: Session) -> dict[str, str]:
    from othor.core.models import AppUser

    return {str(u.id): u.name or u.email for u in s.scalars(sa.select(AppUser))}


def replay_card(s: Session) -> dict[str, Any] | None:
    from othor.web.home_view import latest_run

    run = latest_run(s)
    if run is None:
        return None
    spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).first()
    if spec is None:
        return None
    day = run.run_date - dt.timedelta(days=1)
    return {
        "text": f"{fmt.date_short(run.run_date)} briefs used v{spec.ontology_version}. "
        "Replay gives the same numbers.",
        "run": run.number,
        "day": day,
    }


def breach_size(value: Any, threshold: Any) -> Decimal:
    """How far past its threshold a watch is, as a share (the larger first)."""
    try:
        v, t = Decimal(str(value)), Decimal(str(threshold))
    except (ArithmeticError, ValueError):
        return Decimal(0)
    return abs(v - t) / abs(t) if t else abs(v - t)


def add_href(s: Session, key: str) -> str:
    """Add to a brief: the default brief's Numbers tab, searching for this metric (4d5)."""
    b = next((x for x in briefs.active(s) if x.is_default), None) or next(
        iter(briefs.active(s)), None
    )
    return f"/brief/{b.id}/edit?tab=numbers&q={key}" if b else "/brief"


PREFIXES = ("Accepted on Home: ", "Accepted: ", "Answered on Home: ", "Tell Othor AI: ")


def short_title(reason: str) -> str:
    """A version's title without where it came from (the meta line says that)."""
    for p in PREFIXES:
        if reason.startswith(p):
            return reason[len(p) :]
    return reason
