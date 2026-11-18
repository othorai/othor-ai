# SPDX-License-Identifier: Apache-2.0
"""The read tools (0.26, M26.6): what the night computed and stored, said by code. Each reuses
the view the app's own pages use, so a tool and its page never disagree."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from othor.core import fmt
from othor.tools import Card, Ctx, Refused, Tool, register

NO_RUN = "There's no finished run yet: the first one makes these numbers."


def _provenance(ctx: Ctx, rd: Any) -> dict[str, Any]:
    from othor.ontology.models import Spec

    if rd.run is None:
        return {}
    spec = ctx.s.scalars(sa.select(Spec).where(Spec.id == rd.run.spec_id)).first()
    return {
        "run": rd.run.number,
        "run_date": rd.run.run_date.isoformat(),
        "spec": spec.hash[:12] if spec else "",
        "version": spec.ontology_version if spec else None,
    }


def _rd(ctx: Ctx) -> Any:
    from othor.web.ontology_view import run_data

    return run_data(ctx.s, ctx.settings)


# --- numbers -------------------------------------------------------------------------------------


def _numbers(ctx: Ctx, args: dict[str, Any]) -> Card:
    from othor.web.ontology_view import number_rows

    rd = _rd(ctx)
    rows = number_rows(ctx.s, rd)[: int(args.get("limit") or 12)]
    return Card(
        "numbers",
        "Your numbers, as of the last run",
        facts=[(r["name"], r["today"]) for r in rows],
        provenance=_provenance(ctx, rd),
        empty="No numbers are confirmed yet." if not rows else "",
    )


register(
    Tool(
        "numbers",
        "Every confirmed number with its latest value from the last nightly run.",
        {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 40}},
            "additionalProperties": False,
        },
        _numbers,
        ("numbers", "metrics", "kpi", "how are we doing", "overview"),
    )
)


def _metric(ctx: Ctx, args: dict[str, Any]) -> Card:
    from othor.ontology import service
    from othor.web.ontology_view import tile_for

    want = str(args["metric"]).strip().lower()
    recs = [r for r in service.active(ctx.s, ["metric"]) if r.body.get("status") != "declined"]
    rec = next(
        (r for r in recs if r.key == want or str(r.body.get("name") or "").lower() == want),
        None,
    ) or next((r for r in recs if want and want in str(r.body.get("name") or "").lower()), None)
    if rec is None:
        raise Refused(f"there's no number called {args['metric']}")
    rd = _rd(ctx)
    name = str(rec.body.get("name") or rec.key)
    t = tile_for(rd, rec.key) if rd.run is not None else None
    if t is None:
        return Card("metric", name, provenance=_provenance(ctx, rd), empty=NO_RUN)
    facts = [(str(t.get("period") or "Latest"), str(t["value"]))]
    if t.get("comparison"):
        facts.append(("Compared", str(t["comparison"])))
    lines = [str(rec.body.get("description") or "")] if rec.body.get("description") else []
    if t.get("stale"):
        lines.append(str(t["stale"]))
    return Card("metric", name, facts=facts, lines=lines, provenance=_provenance(ctx, rd))


register(
    Tool(
        "metric",
        "One number's latest value, what it's compared with, and what it means. "
        "`metric` is the number's name or key.",
        {
            "type": "object",
            "properties": {"metric": {"type": "string", "maxLength": 120}},
            "required": ["metric"],
            "additionalProperties": False,
        },
        _metric,
    )
)


# --- Sweep ---------------------------------------------------------------------------------------


def _findings(ctx: Ctx, args: dict[str, Any]) -> Card:
    from othor.web import sweep_view as sv

    runs = sv.detecting_runs(ctx.s)
    rv = sv.load(ctx.s, ctx.settings, runs[0], ctx.today) if runs else None
    if rv is None:
        return Card("findings", "What Sweep found", empty=NO_RUN)
    new, old = sv.finding_cards(rv)
    cards = (new + old)[: int(args.get("limit") or 5)]
    rd = _rd(ctx)
    return Card(
        "findings",
        f"What Sweep found in run #{runs[0].number:04d}",
        lines=[f"{c['title']} · {c['label'].lower()}" for c in cards]
        + [f"{fmt.num(len(new))} new, {fmt.num(len(old))} still open."],
        provenance=_provenance(ctx, rd),
        empty="Nothing open: every check came back quiet." if not cards else "",
    )


register(
    Tool(
        "findings",
        "The open findings from the last Sweep (every number tested every night), most money "
        "first.",
        {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}},
            "additionalProperties": False,
        },
        _findings,
        ("finding", "sweep", "wrong", "problem", "anomal", "unusual", "flag"),
    )
)


def _coverage(ctx: Ctx, args: dict[str, Any]) -> Card:
    from othor.web import sweep_view as sv

    del args
    runs = sv.detecting_runs(ctx.s)
    rv = sv.load(ctx.s, ctx.settings, runs[0], ctx.today) if runs else None
    if rv is None:
        return Card("sweep_coverage", "Which checks ran", empty=NO_RUN)
    cov = sv.coverage(rv)
    facts = []
    for group, rows in cov.items():
        if isinstance(rows, list):
            facts.append((str(group).replace("_", " ").capitalize(), fmt.num(len(rows))))
    return Card(
        "sweep_coverage",
        f"Which checks ran in run #{runs[0].number:04d}",
        facts=facts,
        provenance=_provenance(ctx, _rd(ctx)),
    )


register(
    Tool(
        "sweep_coverage",
        "How many of Sweep's checks ran last night, and how many couldn't and why.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _coverage,
        ("coverage", "checks ran", "detector"),
    )
)


# --- the Business Model ---------------------------------------------------------------------


def _history(ctx: Ctx, args: dict[str, Any]) -> Card:
    from othor.web.ontology_view import history, user_names

    rows = history(ctx.s, user_names(ctx.s))[: int(args.get("limit") or 5)]
    return Card(
        "business_model_history",
        "Recent changes to your Business Model",
        facts=[(f"v{r['number']} · {fmt.day_month(r['date'])}", r["title"]) for r in rows],
        provenance={"version": rows[0]["number"]} if rows else {},
        empty="No changes yet." if not rows else "",
    )


register(
    Tool(
        "business_model_history",
        "The latest versions of the Business Model: what changed, when, and who changed it.",
        {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}},
            "additionalProperties": False,
        },
        _history,
        ("history", "changed", "version"),
    )
)


def _questions(ctx: Ctx, args: dict[str, Any]) -> Card:
    from othor.home import answers

    del args
    qs = answers.queue(ctx.s, ctx.today)
    return Card(
        "questions",
        "Questions waiting for you",
        facts=[(f"{i + 1}", q.text) for i, q in enumerate(qs[:8])],
        lines=[f"{fmt.num(len(qs))} waiting. Answering them lets Sweep run more checks."]
        if qs
        else [],
        empty="No questions for you right now." if not qs else "",
    )


register(
    Tool(
        "questions",
        "The questions Othor AI is waiting on the owner to answer, in order.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _questions,
        ("question", "waiting", "need from me"),
    )
)


# --- Home's own view: today's briefs, System Health, Intelligence, what's ahead (0.26.2) ----


def _home(ctx: Ctx) -> dict[str, Any]:
    """Home's view, computed the way Home computes it, so a tool and the page never disagree."""
    import datetime as dt

    from othor.web import home_view as hv

    return hv.page(ctx.s, ctx.settings, ctx.company, ctx.today, dt.datetime.now(dt.UTC))


def _run_prov(ctx: Ctx, home: dict[str, Any]) -> dict[str, Any]:
    run = home.get("run")
    return _provenance(ctx, _rd(ctx)) if run is not None else {}


def _brief(ctx: Ctx, args: dict[str, Any]) -> Card:
    home = _home(ctx)
    cards = list(home.get("cards") or [])
    want = str(args.get("name") or "").strip().lower()
    if want:
        cards = [c for c in cards if want in str(c.get("name") or "").lower()] or cards
    lines = []
    for c in cards[:6]:  # a headline is a sentence: a line, not a label and value
        lines.append(f"{c.get('name')} brief: {c.get('title') or 'No issue yet'}")
        if c.get("meta"):
            lines.append(str(c["meta"]))
    return Card(
        "brief",
        "Today's briefs",
        lines=lines,
        provenance=_run_prov(ctx, home),
        empty="No brief yet." if not cards else "",
    )


register(
    Tool(
        "brief",
        "Today's Morning Brief: each brief's headline, how many findings it carries and when it "
        "goes out. `name` picks one brief.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "maxLength": 80}},
            "additionalProperties": False,
        },
        _brief,
        ("brief", "headline", "morning"),
    )
)


STATE_WORD = {
    "ok": "Fine",
    "note": "Note",
    "warn": "Needs a look",
    "bad": "Needs you",
    "info": "Info",
}


def _system_health(ctx: Ctx, args: dict[str, Any]) -> Card:
    del args
    home = _home(ctx)
    lt = home.get("lights") or {}
    rows = list(lt.get("lights") or [])
    order = {"bad": 0, "warn": 1, "note": 2, "info": 3, "ok": 4}
    rows.sort(key=lambda x: order.get(str(x.get("state")), 5))
    return Card(
        "system_health",
        "System Health",
        facts=[
            (
                str(x.get("label") or ""),
                f"{STATE_WORD.get(str(x.get('state')), '')} · {x.get('line')}",
            )
            for x in rows
        ],
        lines=[str(lt.get("summary"))] if lt.get("summary") else [],
        provenance=_run_prov(ctx, home),
        empty=NO_RUN if not rows else "",
    )


register(
    Tool(
        "system_health",
        "System Health: whether the sources answered and were fresh, how the night's run went, "
        "and what needs the owner, most urgent first.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _system_health,
        ("health", "healthy", "system", "fresh"),
    )
)


def _intelligence(ctx: Ctx, args: dict[str, Any]) -> Card:
    del args
    home = _home(ctx)
    c = home.get("checks")
    if not c:
        return Card("intelligence", "Intelligence", empty=NO_RUN)
    facts = [("Intelligence", f"{fmt.num(c['pct'])}%")]
    for g in c.get("groups") or []:
        if g.get("n"):
            facts.append((str(g.get("label")), fmt.num(g["n"])))
    lines = [str(c.get("explain") or "")]
    if c.get("tail"):
        lines.append(str(c["tail"]))
    return Card(
        "intelligence",
        "How much Sweep can check",
        facts=facts,
        lines=[x for x in lines if x],
        provenance=_run_prov(ctx, home),
    )


register(
    Tool(
        "intelligence",
        "Intelligence: the share of Sweep's checks that could run last night, why the rest "
        "couldn't, and what would raise it.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _intelligence,
        ("intelligence", "how much can", "coverage percent"),
    )
)


def _whats_ahead(ctx: Ctx, args: dict[str, Any]) -> Card:
    home = _home(ctx)
    o = home.get("outlook") or {}
    weeks = list(o.get("weeks") or [])[: int(args.get("weeks") or 4)]
    return Card(
        "whats_ahead",
        "The next weeks",
        facts=[
            (f"{w.get('name')} · {w.get('dates')}", f"{w.get('label')} · {w.get('line')}")
            for w in weeks
        ],
        provenance=_run_prov(ctx, home),
        empty="No outlook yet: it needs a few weeks of history." if not weeks else "",
    )


register(
    Tool(
        "whats_ahead",
        "What's ahead: each of the next weeks (from forecasts, goals and deadlines), what could "
        "go wrong and how sure Othor AI is.",
        {
            "type": "object",
            "properties": {"weeks": {"type": "integer", "minimum": 1, "maximum": 4}},
            "additionalProperties": False,
        },
        _whats_ahead,
        ("ahead", "next week", "coming up", "forecast"),
    )
)
