# SPDX-License-Identifier: Apache-2.0
"""Questions for you as one message (Chat 1.0, the owner's handoff design/questions-chat/).

Every waiting question becomes one item of the pending set:
- **needs** questions: the suggestion cards code found at night are the options; Othor AI's pick
  is the card it would decide on (strong, computed evidence), unless the answer is high-impact;
- questions answered by a fixed reply (a known pattern, a plant, which side is right, how long
  to wait): those replies are the options; no pick (only a person knows), so they need the owner;
- the free-text questions (a plant by name): answered in a sentence in the chat.

Apply writes every ready answer in **one** Business Model version and marks each question
answered, with an event each (non-negotiable 6: the button is the apply). The answers are the
same records the old panel wrote (`othor.home.answers`), so nothing downstream changes."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from othor.chat import pending
from othor.core.events import record_event
from othor.core.tenancy import Repo
from othor.home import answers
from othor.ontology import preview, service
from othor.ontology.models import Question

# answers that change how a number is computed could rewrite past findings: never Othor AI's
# pick, always the owner's (the handoff's open question 4, owner 18 Nov 2026)
HIGH_IMPACT_TYPES = frozenset({"metric"})
NOT_A_CHOICE = frozenset({"We don't have this", "Another plant", "No, the dates change"})


def _tag(change: dict[str, Any]) -> dict[str, str] | None:
    """Where a card's answer reads from: the first table it names (source › table)."""

    def walk(node: Any) -> dict[str, str] | None:
        if isinstance(node, dict):
            if isinstance(node.get("source"), str) and isinstance(node.get("table"), str):
                return {"source": node["source"], "table": node["table"]}
            for v in node.values():
                if (got := walk(v)) is not None:
                    return got
        if isinstance(node, list):
            for v in node:
                if (got := walk(v)) is not None:
                    return got
        return None

    return walk(change.get("body") or {})


def _effect(q: Question) -> str:
    dets = list((q.unblocks or {}).get("detectors") or [])
    if dets:
        n = len(dets)
        return f"Sweep can run {n} more check{'s' if n != 1 else ''} on your numbers."
    if q.kind == "pattern":
        return "Sweep stops flagging this pattern as a problem."
    if q.kind in ("needs_confirmation", "not_determinable"):
        return "The brief stops asking about it."
    return ""


def item(q: Question) -> dict[str, Any]:
    qid = f"q:{q.id}"
    base: dict[str, Any] = {
        # a question from a finding: the records in question, from what the night stored
        "evidence": bool((q.unblocks or {}).get("finding")),
        "id": qid,
        "text": q.text,
        "why": q.why,
        "effect": _effect(q),
        "unblocks": list((q.unblocks or {}).get("detectors") or []),
        "choice": None,
        "answer": None,
        "high_impact": False,
    }
    if q.kind == "goal_missing":  # 0.28 (M28.2): a whole goal, suggested by code at night
        cards = list((q.subject or {}).get("cards") or [])
        if not cards:
            return {**base, **_later()}
        return {
            **base,
            "answer_type": "choice",
            "options": [
                {
                    "key": str(c["key"]),
                    "label": str(c["label"]),
                    "note": str(c.get("note") or ""),
                    "tag": None,
                    "type": "goal",
                }
                for c in cards
            ],
            "choice": str(cards[0]["key"]),
            "status": "pick",
            "effect": "Every night shows how likely you are to reach it.",
            "why_not": str(cards[0].get("note") or "").capitalize(),
        }
    if q.kind == "needs":
        cards = answers.suggestions(q)
        opts = [
            {
                "key": str(c.get("key")),
                "label": str(c.get("label") or ""),
                "note": str(c.get("sub") or ""),
                "tag": _tag(dict(c.get("change") or {})),
                "type": str((c.get("change") or {}).get("type") or ""),
                # real rows (M26.3) or, for a pair, the two numbers from stored series (0.26.2)
                "preview": preview.for_change(dict(c.get("change") or {})) is not None
                or (dict(c.get("change") or {}).get("body") or {}).get("kind") == "pair",
            }
            for c in cards
            if c.get("key") and c.get("change")
        ]
        dec = answers.decision(q)
        pick = dec.get("pick")
        card = next((c for c in cards if c.get("key") == pick), None)
        strong = bool(card and card.get("strong"))
        # owner, 18 Nov 2026: Othor AI always suggests an answer; the owner edits if it looks
        # wrong. The decided card when there is one, else the best-ranked card (cards come in
        # code's order); a weak one says why under it.
        chosen = card or (cards[0] if cards else None)
        high = (
            chosen is not None
            and str((chosen.get("change") or {}).get("type")) in HIGH_IMPACT_TYPES
        )
        if not opts or chosen is None:
            return {**base, **_later()}
        return {
            **base,
            "answer_type": "choice",
            "options": opts,
            "choice": str(chosen.get("key")),
            "status": "pick",
            "high_impact": high,
            # a weak suggestion: why, and the next step, as code found them at night
            "why_not": (
                " ".join(str(dec.get(k) or "").strip() for k in ("why", "next")).strip()
                if not (strong and card is chosen)
                else ""
            ),
        }
    opts = [
        {"key": r, "label": r, "note": "", "tag": None, "type": ""}
        for r in (q.quick_replies or [])
        if r not in NOT_A_CHOICE
    ]
    if not opts:  # a free-text question (a plant by name): nothing to suggest yet
        return {**base, **_later()}
    # owner, 18 Nov 2026: where Othor AI can't pick a side (the replies offer "look into it"),
    # its suggestion is "Skip for now" (set aside a week, nothing written); else the first reply
    if any(o["key"] in answers.LOOK_INTO_IT for o in opts):
        return {**base, **_later(), "options": [*_later()["options"], *opts]}
    chosen = opts[0]
    return {
        **base,
        "answer_type": "choice",
        "options": opts,
        "choice": chosen["key"],
        "status": "pick",
        **({"free_text_ok": True} if q.kind == "unmapped_plant" else {}),
    }


LATER = "__later"


def _later() -> dict[str, Any]:
    """Nothing Othor AI can pick: it suggests "Skip for now" (Apply sets it aside a week and
    writes nothing), never a bare Needs you (owner, 18 Nov 2026)."""
    return {
        "answer_type": "choice",
        "options": [{"key": LATER, "label": "Skip for now", "note": "", "tag": None}],
        "choice": LATER,
        "status": "pick",
        "effect": "Set aside for a week; nothing changes.",
    }


def build(s: Session, today: dt.date) -> list[dict[str, Any]]:
    """The set for today's questions, in Home's order."""
    return [item(q) for q in answers.queue(s, today)]


def _question(s: Session, item_id: str) -> Question | None:
    import uuid

    try:
        qid = uuid.UUID(item_id.removeprefix("q:"))
    except ValueError:
        return None
    return Repo(s).get(Question, qid)  # scoped: never another company's question


def _changes(
    s: Session, q: Question, it: dict[str, Any], today: dt.date
) -> tuple[list[Any], str] | None:
    """The records an item's answer writes, and the answer as the owner gave it."""
    if q.kind == "goal_missing" and it.get("choice"):
        card = next(
            (c for c in (q.subject or {}).get("cards") or [] if c.get("key") == it["choice"]), None
        )
        if card is None:
            return None
        ch = card["change"]
        return [service.put("goal", ch["key"], ch["body"], answers._ref(q))], str(card["label"])
    if q.kind == "needs" and it.get("choice"):
        card = answers._card(q, str(it["choice"]))
        ch = answers._card_change(s, card) if card else None
        if ch is None or card is None:
            return None
        return [service.put(ch["type"], ch["key"], ch["body"], answers._ref(q))], str(
            card.get("label") or ""
        )
    if it.get("answer_type") == "free_text" or (it.get("answer") and q.kind == "unmapped_plant"):
        text = str(it.get("answer") or "").strip()
        if q.kind == "unmapped_plant" and answers.PLANT.match(text):
            c = answers._plant(s, q, text)
            return ([c], text) if c is not None else None
        return None
    if it.get("choice"):
        changes, _out, said = answers.changes_for_reply(s, q, str(it["choice"]), today)
        return (changes, said) if changes else None
    return None


def apply(
    s: Session, items: list[dict[str, Any]], *, actor: str, now: dt.datetime, today: dt.date
) -> dict[str, Any]:
    """Apply every ready item in one Business Model version. Returns {applied: [...], version,
    not_applied: [ids]} (an item whose answer can't be written now, e.g. its card is gone)."""
    plan: list[tuple[Question, list[Any], str, dict[str, Any]]] = []
    missed: list[str] = []
    for it in pending.to_apply(items):
        if it.get("choice") == LATER:  # set aside by skip(), not answered
            continue
        q = _question(s, str(it["id"]))
        if q is None or q.status == "answered":
            missed.append(str(it["id"]))
            continue
        got = _changes(s, q, it, today)
        if got is None:
            missed.append(str(it["id"]))
            continue
        plan.append((q, got[0], got[1], it))
    if not plan:
        return {"applied": [], "version": None, "not_applied": missed}
    changes = [c for _q, cs, _a, _i in plan for c in cs]
    n = len(plan)
    v = service.apply(
        s,
        changes,
        actor=actor,
        reason=f"Othor AI chat: {n} answer{'s' if n != 1 else ''} to Questions for you",
        allow_empty=False,
    )
    done = []
    for q, cs, said, it in plan:
        c = cs[0]
        rec = answers._record(s, c.type, c.key)
        q.status, q.answered_at, q.answer_text = "answered", now, said[:500]
        q.answer_version = v.number if v is not None else None
        q.answer_record_id = rec.id if rec is not None else None
        q.skipped_until, q.draft = None, None
        record_event(
            s,
            actor=actor,
            action="question.answered",
            object_type="question",
            object_id=q.id,
            before={"status": "open"},
            after={
                "answer": said[:200],
                "version": q.answer_version,
                "record": f"{c.type}:{c.key}",
                "via": "othor_chat",
                "picked_by": "othor_ai" if it.get("status") == "pick" else "owner",
            },
        )
        done.append(
            {
                "id": it["id"],
                "text": it["text"],
                "answer": said,
                "effect": it["effect"],
                "unblocks": it.get("unblocks") or [],
            }
        )
    return {"applied": done, "version": v.number if v is not None else None, "not_applied": missed}


def skip(s: Session, items: list[dict[str, Any]], *, actor: str, today: dt.date) -> list[str]:
    """Skipped items come back in a week, as Ask me later did."""
    out = []
    for it in items:
        later = it.get("choice") == LATER and it.get("status") in pending.APPLY
        if it.get("status") != "skipped" and not later:
            continue
        q = _question(s, str(it["id"]))
        if q is not None and q.status != "answered":
            answers.skip(s, q, actor=actor, today=today)
            out.append(str(it["id"]))
    return out
