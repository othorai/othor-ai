# SPDX-License-Identifier: Apache-2.0
"""Rule-based Home questions that come from computed results (docs/04 §10). No model.

`unmapped_plant_questions`: an item code whose prefix isn't in the plant vocabulary lands in the
"Unmapped" bucket; when that is more than the spec's threshold (1%) of a month's revenue, Home
asks which plant the prefix belongs to. One question per prefix (fingerprinted), never repeated.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core.events import SYSTEM_ACTOR, record_event
from othor.ontology.models import Question
from othor.ontology.suggest import EXPLAIN, QUESTION


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def unmapped_candidates(
    check: Mapping[str, Any],
    unmapped: Iterable[tuple[dt.date, str, Decimal]],
    totals: Mapping[dt.date, Decimal],
) -> list[dict[str, Any]]:
    """The questions the rule asks, as plain data (no session): the nightly run stores these
    in its outputs, and replay recomputes them."""
    threshold = Decimal(str(check["threshold"]))
    by_prefix: dict[str, list[tuple[dt.date, Decimal, Decimal]]] = {}
    for month, prefix, value in unmapped:
        total = totals.get(month)
        if not total or Decimal(value) <= 0:
            continue
        share = Decimal(value) / Decimal(total)
        if share > threshold:
            by_prefix.setdefault(prefix, []).append((month, Decimal(value), share))
    out: list[dict[str, Any]] = []
    for prefix in sorted(by_prefix):
        months = sorted(by_prefix[prefix])
        worst = max(months, key=lambda x: (x[2], x[0]))
        out.append(
            {
                "fingerprint": _fingerprint("unmapped_plant", check["metric"], prefix),
                "text": check["question"].format(prefix=prefix),
                "why": f"{prefix} items aren't in the plant list, so they can't be split by plant.",
                "unblocks": {"metric": check["metric"], "breakdown": check["breakdown"]},
                "origin": {
                    "rule": "unmapped_plant",
                    "metric": check["metric"],
                    "prefix": prefix,
                    "month": worst[0].isoformat(),
                    "value": str(worst[1]),
                    "share": str(worst[2].quantize(Decimal("0.000001"))),
                },
                "quick_replies": ["Surat", "Coimbatore", "Another plant"],
                "kind": "unmapped_plant",
                "subject": {"metric": check["metric"], "prefix": prefix, "money": str(worst[1])},
                "rank": "5|",
            }
        )
    return out


def _window_text(w: Mapping[str, Any]) -> str:
    from othor.core import fmt

    if w.get("kind") == "annual" and w.get("start") and w.get("end"):
        a = dt.date.fromisoformat(f"2001-{w['start']}")
        b = dt.date.fromisoformat(f"2001-{w['end']}")
        return fmt.day_range(a, b)
    if w.get("kind") == "range" and w.get("start") and w.get("end"):
        return fmt.day_range(dt.date.fromisoformat(w["start"]), dt.date.fromisoformat(w["end"]))
    return ""


def pattern_candidates(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """An unconfirmed known pattern (a proposed shutdown) suppresses nothing until the owner
    confirms it (docs/04 §4): Home asks "same dates every year?"."""
    out: list[dict[str, Any]] = []
    for p in spec.get("suppressions", []):
        if p.get("confirmed", True) or p.get("entity"):
            continue
        name = p.get("short") or p.get("name") or p["key"]
        if not name.lower().startswith("the "):
            name = "the " + name
        # a name that already says its dates ("Surat plant shutdown, 10–24 August") keeps them
        when = "" if any(c.isdigit() for c in name) else _window_text(p.get("window") or {})
        out.append(
            {
                "fingerprint": _fingerprint("pattern", p["key"]),
                "text": f"Is {name} {when} the same every year?".replace("  ", " "),
                "why": "Until you confirm it, Sweep still flags these dates.",
                "unblocks": {"pattern": p["key"]},
                "origin": {"rule": "unconfirmed_pattern", "pattern": p["key"]},
                "quick_replies": ["Yes, every year", "No, the dates change"],
                "kind": "pattern",
                "subject": {"pattern": p["key"]},
                "rank": "3|",
            }
        )
    return out


# A detector that could not run for want of an Ontology record (docs/04 "needs" table): one
# question per record kind, in the owner's terms, with one plain line on what the check does
# (v0.12, docs/plans/helpful-questions.md). The count of Sweep tests it blocks stays in
# `unblocks` for the checks strip, not on the question (#33). Suggested answers are found by
# code (`othor.ontology.suggest`) and stored on the question's subject.
NEEDS_QUESTIONS = QUESTION
NEEDS_EXPLAIN = EXPLAIN


def needs_asked(coverage: Iterable[Mapping[str, Any]]) -> dict[str, list[str]]:
    """{need: the detectors that could not run for want of it}."""
    seen: dict[str, list[str]] = {}
    for c in coverage:
        need = c.get("needs") or ""
        if c.get("status") == "could_not_run" and need in NEEDS_QUESTIONS:
            seen.setdefault(need, []).append(str(c.get("detector", "")))
    return {n: sorted(set(d)) for n, d in sorted(seen.items())}


def needs_candidates(
    coverage: Iterable[Mapping[str, Any]],
    suggestions: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """`suggestions[need]`: the finder's part of the subject (cards, what Let Othor AI decide
    picks, or why it can't and the next step)."""
    return [
        {
            "fingerprint": _fingerprint("needs", need),
            "text": NEEDS_QUESTIONS[need],
            "why": NEEDS_EXPLAIN[need],
            "unblocks": {"needs": need, "detectors": dets},
            "origin": {"rule": "detector_needs", "needs": need},
            "quick_replies": ["We don't have this"],
            "kind": "needs",
            "subject": {
                "needs": need,
                "detectors": dets,
                **dict((suggestions or {}).get(need) or {}),
            },
            "rank": "4|",
        }
        for need, dets in needs_asked(coverage).items()
    ]


def refresh_needs(s: Session, candidates: Iterable[Mapping[str, Any]]) -> int:
    """Tonight's suggestions on the needs questions still waiting (open or set aside), and the
    v0.12 wording on ones raised before it. A waiting draft or an answer is left as it is."""
    n = 0
    for c in candidates:
        if c.get("kind") != "needs":
            continue
        q = s.scalars(sa.select(Question).where(Question.fingerprint == c["fingerprint"])).first()
        if q is None or q.status not in ("open", "skipped") or q.draft:
            continue
        sub = {**(q.subject or {}), **dict(c["subject"])}
        if sub != q.subject or q.text != c["text"] or q.why != c["why"]:
            q.subject, q.text, q.why = sub, c["text"], c["why"]
            n += 1
    return n


def raise_questions(s: Session, candidates: Iterable[Mapping[str, Any]]) -> list[Question]:
    """Store the candidates not asked before (one per fingerprint, never repeated)."""
    created: list[Question] = []
    for c in candidates:
        fp = c["fingerprint"]
        if s.scalars(sa.select(Question).where(Question.fingerprint == fp)).first() is not None:
            continue
        q = Question(
            text=c["text"],
            why=c["why"],
            unblocks=dict(c["unblocks"]),
            origin=dict(c["origin"]),
            quick_replies=list(c["quick_replies"]),
            fingerprint=fp,
            kind=c.get("kind", "other"),
            subject=dict(c["subject"]) if c.get("subject") else None,
            rank_key=c.get("rank", ""),
        )
        s.add(q)
        s.flush()
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="question.raised",
            object_type="question",
            object_id=q.id,
            after={"text": q.text, "origin": q.origin},
        )
        created.append(q)
    return created


def unmapped_plant_questions(
    s: Session,
    check: Mapping[str, Any],
    unmapped: Iterable[tuple[dt.date, str, Decimal]],
    totals: Mapping[dt.date, Decimal],
) -> list[Question]:
    """`unmapped`: (month, prefix, value) from the check's SQL; `totals`: the metric's value per
    month. Returns the questions created now (existing ones aren't created again)."""
    return raise_questions(s, unmapped_candidates(check, unmapped, totals))


def refresh_goal_question(s: Session, candidate: dict[str, Any] | None, has_goals: bool) -> None:
    """0.28 (M28.2): "What are you aiming for this year?" holds tonight's suggestion while it
    waits; it closes once the company has a goal, and opens again when every goal is removed."""
    from othor.ontology.goal_suggest import KIND

    rows = s.scalars(sa.select(Question).where(Question.kind == KIND)).all()
    for q in rows:
        if has_goals and q.status in ("open", "skipped"):
            q.status, q.answer_text = "answered", "A goal was added"
        elif not has_goals and candidate is not None and q.fingerprint == candidate["fingerprint"]:
            if q.status == "answered":
                q.status, q.answer_text = "open", None
            if q.status in ("open", "skipped"):
                q.subject = dict(candidate["subject"])
