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


def number_line(r: dict[str, Any], run_date: Any) -> str:
    """0.27.3 (an outside AI charted balances beside period totals): a number's value with what
    it is (a total for a period, a balance on a day, or worked out from other numbers), the
    period, and its change, all as the brief's tile computed them."""
    t = r.get("tile")
    if not t:
        return str(r["today"])
    import datetime as dt

    period = dt.date.fromisoformat(t["period"])
    g = t.get("grain")
    if g == "month":
        when = f"{period:%b %Y}" + (
            " so far"
            if run_date and (period.year, period.month) == (run_date.year, run_date.month)
            else ""
        )
    elif g == "week":
        when = f"the week of {fmt.day_month(period)}"
    else:
        when = fmt.day_month(period)
    what = {
        "flow": f"total for {when}",
        "snapshot": f"balance on {fmt.day_month(period)}"
        if g in (None, "day", "as_of")
        else f"balance at the end of {when}",
        "derived": f"for {when}",
    }.get(str(r.get("kind")), f"for {when}")
    bits = [str(t["value"]), what]
    comp = t.get("comparison") or {}
    if comp.get("kind") in ("period", "band", "goal") and comp.get("text"):
        bits.append(str(comp["text"]))
    if t.get("stale"):
        bits.append(str(t["stale"]))
    return " · ".join(bits)


def _numbers(ctx: Ctx, args: dict[str, Any]) -> Card:
    from othor.web.ontology_view import number_rows

    rd = _rd(ctx)
    rows = number_rows(ctx.s, rd)[: int(args.get("limit") or 12)]
    run_date = rd.run.run_date if rd.run is not None else None
    return Card(
        "numbers",
        "Your numbers, as of the last run",
        facts=[(r["name"], number_line(r, run_date)) for r in rows],
        provenance=_provenance(ctx, rd),
        empty="No numbers are confirmed yet." if not rows else "",
    )


register(
    Tool(
        "numbers",
        "Every confirmed number from the last nightly run: its latest value, whether it's a "
        "total for a period or a balance on a day (never add or chart the two together), the "
        "period, and its change on the last period.",
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
    """One number. A word that fits several ("revenue": invoiced, net, export) gets the numbers
    it could mean, each with its value, so the owner picks (#94, 19 Nov 2026: "the AI should be
    helpful and guiding"), never the first that happens to contain the word."""
    import re

    from othor.ontology import service
    from othor.web.ontology_view import number_rows

    want = str(args["metric"]).strip().lower()
    recs = [r for r in service.active(ctx.s, ["metric"]) if r.body.get("status") != "declined"]
    exact = [r for r in recs if r.key == want or str(r.body.get("name") or "").lower() == want]
    word = re.compile(rf"\b{re.escape(want)}", re.I) if want else None
    near = [
        r
        for r in recs
        if word and (word.search(str(r.body.get("name") or "")) or word.search(r.key))
    ]
    hits = exact or near
    if not hits:
        raise Refused(f"there's no number called {args['metric']}")
    rd = _rd(ctx)
    if rd.run is None:
        name = str(hits[0].body.get("name") or hits[0].key)
        return Card("metric", name, provenance=_provenance(ctx, rd), empty=NO_RUN)
    run_date = rd.run.run_date
    rows = {r["key"]: r for r in number_rows(ctx.s, rd)}
    if len(hits) > 1:
        return Card(
            "metric",
            f"Numbers that match \u201c{args['metric']}\u201d",
            facts=[
                (str(r.body.get("name") or r.key), number_line(rows[r.key], run_date))
                for r in hits
                if r.key in rows
            ],
            lines=["Ask about one of them by name for what it counts and how it changed."],
            provenance=_provenance(ctx, rd),
        )
    rec = hits[0]
    name = str(rec.body.get("name") or rec.key)
    row = rows.get(rec.key)
    t = (row or {}).get("tile")
    if not row or not t:
        return Card(
            "metric",
            name,
            provenance=_provenance(ctx, rd),
            empty=f"{name} has no value yet: the last run had nothing to work it out from.",
        )
    facts = [("Latest", number_line(row, run_date))]
    comp = t.get("comparison") or {}
    if comp.get("kind") == "history" and comp.get("text"):
        facts.append(("History", str(comp["text"])))
    lines = [str(rec.body.get("description") or "")] if rec.body.get("description") else []
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
                f"{STATE_WORD.get(str(x.get('state')), '')} · {x.get('line')}"
                + "".join(f" · {d}" for d in x.get("details") or []),  # 0.27.2: the details too
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


def _segs(x: Any) -> str:
    """A stored Big Picture sentence (checked segments, numbers already computed) as text."""
    if isinstance(x, list):
        return "".join(str(p.get("t", "")) for p in x if isinstance(p, dict)).strip()
    return str(x or "").strip()


def _big_picture(ctx: Ctx, args: dict[str, Any]) -> Card:
    """0.27.3: the latest Big Picture report as stored (never written anew: no model call)."""
    del args
    from othor.briefs.models import BigPicture
    from othor.engine.models import Run

    row = ctx.s.execute(
        sa.select(BigPicture, Run.number, Run.run_date)
        .join(Run, Run.id == BigPicture.run_id)
        .where(BigPicture.deleted_at.is_(None))
        .order_by(
            Run.number.desc(), (BigPicture.language == "en").desc(), BigPicture.created_at.desc()
        )
    ).first()
    if row is None:
        return Card(
            "big_picture",
            "Big Picture",
            empty="No Big Picture report yet: open Big Picture in Othor AI to make one.",
        )
    bp, number, run_date = row
    b = bp.body or {}
    facts = [(str(k.get("label", "")), str(k.get("value", ""))) for k in b.get("kpis") or []]
    lines = [_segs(b.get("one_line"))] + [_segs(x) for x in b.get("summary") or []]
    for c in b.get("findings") or []:
        lines.append(f"{_segs(c.get('title'))}: {_segs(c.get('sentence'))}")
    for x in b.get("stakes") or []:
        money = f"{x.get('value', '')} {x.get('unit') or ''}".strip()
        lines.append(f"At stake: {money} · {_segs(x.get('desc'))}")
    for a in b.get("look_first") or []:
        lines.append(f"Look first: {_segs(a.get('text'))}")
    return Card(
        "big_picture",
        f"Big Picture for night run #{number}",
        facts=facts,
        lines=[x for x in lines if x and x != ":"],
        provenance={"run": number, "run_date": run_date.isoformat(), "written": bp.prompt},
    )


register(
    Tool(
        "big_picture",
        "The latest Big Picture report as Othor AI wrote it: the situation in a few sentences, the "
        "headline numbers, what was found, what's at stake and what to look at first. Every "
        "figure in it was computed and checked.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _big_picture,
        ("big picture", "summary report", "overall", "how are we doing"),
    )
)
