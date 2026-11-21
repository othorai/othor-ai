# SPDX-License-Identifier: Apache-2.0
"""Business Model levels (Business Model 2.4 "Level up", docs/plans/business-model-levels.md, L1).

How complete the Business Model is, by code only: which items exist among the live records. Three
levels: 1 · Must have (a brief that's right and says who to ask), 2 · Good to have (a brief that
explains itself), 3 · Full potential (Sweep finds what a person would miss). A level is reached
when every item of it and the levels below is there. Each missing item says what it unlocks in one
line and the one sentence to say in the chat (pre-filled in the chat box), or where to go when it
isn't said in a sentence (a source is connected on Sources). No model, no values: counts of
records only, so it works without a model (rule 4).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlencode

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core import fmt
from othor.ontology import service

TOP = 3
NAMES = {1: "Must have", 2: "Good to have", 3: "Full potential"}
GIVES = {
    1: "A brief that's right and says who to ask",
    2: "A brief that explains itself",
    3: "Sweep finds what a person would miss",
}
# the rules that make numbers true (cancelled out, net of GST, yards to metres, currency)
TRUE_RULES = ("filter", "unit_conversion", "currency", "book_of_record", "allocation")


ITEMS: list[tuple[str, int, str, str]] = [
    (
        "sources",
        1,
        "Sources connected, read-only",
        "Every number is read where it lives, nothing copied",
    ),
    ("numbers", 1, "Core numbers defined right", "Numbers the brief can be trusted on"),
    (
        "rules",
        1,
        "The rules that make them true",
        "Cancelled entries out, net of tax, one unit, one currency: numbers that are true",
    ),
    (
        "goal",
        1,
        "At least one goal",
        "“Are we on track?” every morning, with the chance of getting there",
    ),
    ("owners", 1, "Owners for the main areas", "Each question goes to a named person"),
    ("breakdowns", 2, "Breakdowns", "Which region, plant or customer carries a change"),
    (
        "parts",
        2,
        "Numbers built from parts",
        "Net figures that are right, and new numbers like margin per unit",
    ),
    ("patterns", 2, "Known patterns", "No false alarms in festival weeks or a planned shutdown"),
    ("headline", 2, "Headline numbers", "The numbers at the top of every brief"),
    ("goals", 2, "More goals", "What's ahead for each area, not just one"),
    ("pairs", 3, "Pairs to test", "Sweep tells you when two numbers stop moving together"),
    ("checks", 3, "Cross-source checks", "Records in one system and missing in another, counted"),
    ("customers", 3, "Named customers", "The accounts behind a change, by name"),
    ("words", 3, "Words and facts", "Othor AI speaks your language"),
    ("watches", 3, "Watches", "A number you care about, checked every night against your limit"),
]
_BY_KEY = {k: (lv, label, unlocks) for k, lv, label, unlocks in ITEMS}


@dataclass
class Item:
    key: str
    level: int
    label: str
    unlocks: str
    done: bool
    have: str = ""  # what's there, counted ("3 owners")
    say: str = ""  # the sentence for the chat, pre-filled
    subject: str = "ontology"  # the chat it opens in
    href: str = ""  # where to go instead of the chat (Sources)
    action: str = "Say it"
    drafts: list[dict[str, Any]] = field(default_factory=list)  # L3: ready drafts to apply

    def url(self) -> str:
        if self.href:
            return self.href
        q: dict[str, str] = {"panel": "othor", "subject": self.subject}
        if self.say:
            q["say"] = self.say
        return "/ontology?" + urlencode(q)


@dataclass
class Levels:
    level: int  # reached: 0 (not yet level 1) to 3
    items: list[Item]

    @property
    def left(self) -> list[Item]:
        return [i for i in self.items if not i.done]

    def title(self) -> str:
        return f"Level {self.level} of {TOP}" if self.level else f"Level 1 of {TOP} to reach"

    def line(self) -> str:
        n = len(self.left)
        if not n:
            return "Full potential reached. Every brief and Sweep use all of it."
        return f"{fmt.num(n)} thing{'s' if n != 1 else ''} left to reach full potential"

    def view(self) -> dict[str, Any]:
        groups = []
        for lv in range(1, TOP + 1):
            its = [i for i in self.items if i.level == lv]
            groups.append(
                {
                    "level": lv,
                    "name": NAMES[lv],
                    "gives": GIVES[lv],
                    "done": all(i.done for i in its),
                    "count": f"{fmt.num(sum(1 for i in its if i.done))} of {fmt.num(len(its))}",
                    "rows": [{**asdict(i), "url": i.url()} for i in its],
                }
            )
        return {
            "level": self.level,
            "top": TOP,
            "title": self.title(),
            "line": self.line(),
            "segs": [lv <= self.level for lv in range(1, TOP + 1)],
            "groups": groups,
            "left": len(self.left),
            # the next level's items stay open by default; the rest fold
            "next": min((i.level for i in self.left), default=TOP),
        }


def _n(n: int, one: str, many: str | None = None) -> str:
    return f"{fmt.num(n)} {one if n == 1 else (many or one + 's')}"


def _names(recs: list[Any], k: int = 2) -> list[str]:
    return [str(r.body.get("name") or r.key.replace("_", " ").capitalize()) for r in recs[:k]]


def _mk(key: str, done: bool, have: str = "", **kw: Any) -> Item:
    lv, label, unlocks = _BY_KEY[key]
    return Item(key, lv, label, unlocks, done, have, **kw)


def check(s: Session) -> Levels:
    """The completeness check: which items the live Business Model has (code, counts only)."""
    from othor.connectors.models import Source
    from othor.engine.models import Watch

    recs: dict[str, list[Any]] = {}
    for r in service.active(s):
        recs.setdefault(r.type, []).append(r)
    metrics = [r for r in recs.get("metric", []) if r.body.get("status", "active") == "active"]
    listed = [r for r in metrics if r.body.get("listed", True)]
    waiting = [
        r
        for r in recs.get("metric", [])
        if r.body.get("listed", True) and r.body.get("status") == "needs_source"
    ]
    rules = recs.get("rule", [])
    true_rules = [r for r in rules if r.body.get("kind") in TRUE_RULES]
    pairs = [r for r in rules if r.body.get("kind") == "pair"]
    checks = [r for r in rules if r.body.get("kind") == "reconcile"]
    goals = recs.get("goal", [])
    owners = [r for r in recs.get("owner", []) if r.body.get("areas")]
    used = {b for m in metrics for b in m.body.get("breakdowns") or []}
    breakdowns = [r for r in recs.get("breakdown", []) if r.key in used]
    parts = [r for r in listed if r.body.get("formula")]
    patterns = recs.get("known_pattern", [])
    headline = [r for r in listed if r.body.get("headline")]
    entities = [r for r in recs.get("breakdown", []) if r.body.get("entity")]
    customers = recs.get("customer", []) + entities
    words = recs.get("vocabulary", []) + recs.get("alias", [])
    facts = recs.get("business_fact", [])
    sources = s.scalars(
        sa.select(Source).where(
            Source.deleted_at.is_(None), Source.status.in_(("connected", "paused"))
        )
    ).all()
    watches = s.scalars(sa.select(Watch).where(Watch.stopped_at.is_(None))).all()

    names = _names(listed, 3)
    first = names[0] if names else "Net revenue"
    second = names[1] if len(names) > 1 else "Orders"
    money = [r for r in listed if r.body.get("unit", "inr") == "inr" and not r.body.get("formula")]
    rev = next(
        (str(r.body.get("name")) for r in listed if "revenue" in r.key or "sales" in r.key), first
    )
    items = [
        _mk(
            "sources",
            bool(sources),
            _n(len(sources), "source") if sources else "",
            href="/sources",
            action="Connect a source",
        ),
        _mk(
            "numbers",
            len(listed) >= 3,  # a number waiting for its source shows, but doesn't block
            _n(len(listed), "number")
            + (f" · {fmt.num(len(waiting))} waiting for a source" if waiting else ""),
            href="/ontology?numbers=needs" if waiting else "",
            subject="ontology:add",
            action="Fix" if waiting else "Add a number",
        ),
        _mk(
            "rules",
            bool(true_rules),
            _n(len(true_rules), "rule") if true_rules else "",
            say=f"Leave cancelled entries out of {rev}",
        ),
        _mk(
            "goal",
            bool(goals),
            _n(len(goals), "goal") if goals else "",
            subject="ontology:goal",
            action="Add a goal",
        ),
        _mk(
            "owners",
            bool(owners),
            _n(len(owners), "owner") if owners else "",
            say=f"The Head of Sales owns {rev} and {second}",
        ),
        _mk(
            "breakdowns",
            bool(breakdowns),
            _n(len(breakdowns), "breakdown") if breakdowns else "",
            say=f"Break {rev} down by customer",
        ),
        _mk(
            "parts",
            bool(parts),
            _n(len(parts), "number") if parts else "",
            say=(
                f"{rev} is invoiced value minus credit notes"
                if len(money) < 2
                else f"Add a number: {_names(money)[0]} minus {_names(money)[1]}"
            ),
        ),
        _mk(
            "patterns",
            bool(patterns),
            _n(len(patterns), "pattern") if patterns else "",
            say=f"Every year {rev} dips in Diwali week",
        ),
        _mk(
            "headline",
            bool(headline),
            _n(len(headline), "number") if headline else "",
            say=f"Show {rev} and {second} at the top of the brief",
        ),
        _mk(
            "goals",
            len(goals) >= 2,
            _n(len(goals), "goal") if goals else "",
            subject="ontology:goal",
            action="Add a goal",
        ),
        _mk(
            "pairs",
            bool(pairs),
            _n(len(pairs), "pair") if pairs else "",
            say=f"{first} moves with {second}",
        ),
        _mk(
            "checks",
            bool(checks),
            _n(len(checks), "check") if checks else "",
            say=f"{first} should match {second} every month",
        ),
        _mk(
            "customers",
            bool(customers),
            _n(len(customers), "name") if customers else "",
            say="Our key customers are ",
        ),
        _mk(
            "words",
            bool(words) and bool(facts),
            _n(len(words) + len(facts), "line") if words or facts else "",
            say="When we say dispatch, we mean goods leaving the plant",
        ),
        _mk(
            "watches",
            bool(watches),
            _n(len(watches), "watch", "watches") if watches else "",
            say=f"Watch {rev}: tell me when it falls below ",
        ),
    ]
    level = 0
    for lv in range(1, TOP + 1):
        if all(i.done for i in items if i.level == lv):
            level = lv
        else:
            break
    return Levels(level, items)
