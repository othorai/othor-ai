# SPDX-License-Identifier: Apache-2.0
"""Business Model changes in the one Othor AI chat (0.26, M26.4; owner 18 Nov 2026: "the business
described once, kept true, and updated and changed in plain-language chat").

Subjects: "ontology" (the page's Chat to update; "Tell Othor AI" until 0.26), "ontology:add" (a
new number) and "ontology:metric:<key>" (Change with Othor AI on a number). The owner writes;
one model call (`ontology_change` v4, the thread as history) returns the full list of changes
wanted now, at most 5; each goes through the same checks Tell Othor AI always had
(`othor.home.changes.check`: allowed types and fields, the record schema, numbers only from the
owner's words) and its effect is computed by code (which numbers change meaning, which briefs
and watches read them). The list is the chat's pending set; a reply reshapes it; nothing is
written until Apply, which writes every ready change in one Business Model version (non-
negotiable 6). Without a model the chat says so and nothing is drafted (a change in the owner's
words needs one)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from othor.chat import pending
from othor.chat.service import prop
from othor.core.events import record_event
from othor.home import changes as hc
from othor.ontology import service
from othor.setup.models import ChatMessage, ChatThread

PROMPT, VERSION = "ontology_change", "v5"  # v5: new numbers, formulas, rules, watches (2.4)
MAX = 5
NO_MODEL = (
    "I can't draft changes right now. Nothing has changed; try again in a little while, or "
    "edit the line on the Business Model page."
)


def is_subject(subject: str) -> bool:
    return subject == "ontology" or subject.startswith("ontology:")


def subject_ok(s: Session, subject: str) -> bool:
    from othor.chat import goals

    if goals.is_subject(subject):  # 0.28: Add a goal / change one goal in the chat
        return goals.subject_ok(s, subject)
    from othor.chat import level_drafts

    if level_drafts.is_subject(subject):  # 2.4 L3: a draft found in the files and sources
        return True
    if subject in ("ontology", "ontology:add"):
        return True
    if subject.startswith("ontology:metric:"):
        key = subject.removeprefix("ontology:metric:")
        return any(r.key == key for r in service.active(s, ["metric"]))
    return False


def old_subject(subject: str) -> str:
    """The subject as `ontology_change` knows it: "page", "add" or "metric:<key>"."""
    rest = subject.removeprefix("ontology").removeprefix(":")
    return rest or "page"


def title(s: Session, subject: str) -> str:
    from othor.chat import goals

    if goals.is_subject(subject):
        return goals.title(subject)
    if subject.startswith("ontology:level:"):  # 2.4 L3
        return "Found in your files"
    if subject == "ontology:add":
        return "Add a number"
    if subject.startswith("ontology:metric:"):
        key = subject.removeprefix("ontology:metric:")
        rec = next((r for r in service.active(s, ["metric"]) if r.key == key), None)
        name = str((rec.body.get("name") if rec else None) or key.replace("_", " ").capitalize())
        return f"Change {name}"
    return "Update your Business Model"


def opening(subject: str) -> str:
    from othor.chat import goals

    if goals.is_subject(subject):
        return goals.opening(subject)
    if subject == "ontology:add":
        return (
            "Which number should I compute? Say it in your words; I'll draft it for you to apply."
        )
    if subject.startswith("ontology:metric:"):
        return (
            "What should change about this number? I'll draft it; nothing changes until you apply."
        )
    return (
        "Tell me what changed in the business: a goal, a holiday, a new name for something, "
        "or how a number is worked out. "
        "I'll draft the changes; nothing changes until you apply."
    )


def _refs(change: dict[str, Any]) -> set[str]:
    """The numbers a drafted number is built from (its formula and per-breakdown formulas)."""
    from othor.home.changes import _metric_refs

    if change["type"] != "metric" or not change.get("body"):
        return set()
    b = change["body"]
    out = set(_metric_refs(b.get("formula")))
    for f in (b.get("breakdown_formulas") or {}).values():
        out |= set(_metric_refs(f))
    return out


def needs_of(change: dict[str, Any], drafted: list[dict[str, Any]], live: set[str]) -> list[str]:
    """The new numbers of the same set this change is built from (2.4 L2: "minus rebates" with
    Rebates drafted beside it): applied with it, and in its effect and values."""
    new = {c["key"] for c in drafted if c["type"] == "metric" and c["key"] not in live}
    return sorted((_refs(change) & new) - {change["key"]})


def needed(items: list[dict[str, Any]], it: dict[str, Any]) -> list[dict[str, Any]]:
    """The changes an item needs, from its set."""
    want = set(it.get("needs") or [])
    return [i["change"] for i in items if i.get("change") and i["change"]["key"] in want]


def item(
    s: Session,
    company: Any,
    n: int,
    change: dict[str, Any],
    also: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One drafted change as an item of the set: the line before and after, and its effect, all
    from code. A change that can't be computed needs the owner (no Apply for it). `also`: the
    changes of the set it needs, compiled with it."""
    section, before, after = hc.card_line(s, change)
    eff = hc.effect(s, company, change, also=also)
    ok = eff.error is None
    verb = "Remove" if change["op"] == "retire" else ("Change" if before else "Add")
    lines = hc.effect_lines(eff) if ok else []
    if change["type"] == "watch":
        lines = ["Checked every night from tonight; Home tells you when it crosses."]
    return {
        "id": f"c:{n}",
        "kind": "change",
        "text": f"{verb} · {section.capitalize()}",
        "before": before,
        "after": after,
        "lines": lines,
        "effect": " ".join(lines) if ok else (eff.error or ""),
        "needs": sorted(c["key"] for c in also or []),
        "answer_type": "choice",
        "options": [{"key": "apply", "label": after or before, "note": "", "tag": None}],
        "choice": "apply" if ok else None,
        "status": "pick" if ok else "needs_you",
        "change": change,
        "high_impact": bool(eff.metrics),
    }


def _pending_input(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": i["change"]["type"],
            "key": i["change"]["key"],
            "op": i["change"]["op"],
            "line": i.get("after") or i.get("before") or "",
            "skipped": i.get("status") == "skipped",
        }
        for i in items
        if i.get("change")
    ]


def draft(
    s: Session,
    gateway: Any,
    company: Any,
    t: ChatThread,
    text: str,
    *,
    actor: str,
    today: dt.date,
) -> ChatMessage | None:
    """The owner's message → Othor AI's message with the changes drafted (never applied)."""
    from othor.ai.guards import numbers_grounded
    from othor.chat import service as chat

    text = text.strip()[:500]
    if not text:
        return None
    m = chat.last_set(s, t)
    items = list(prop(m)["items"]) if m is not None else []
    hist = chat._history(s, t)
    s.add(ChatMessage(thread_id=t.id, role="user", text=text))
    s.commit()  # the gateway logs the call in its own session
    inputs = {
        **hc.model_inputs(s, text, old_subject(t.subject), today),
        "pending": _pending_input(items),
    }
    out: dict[str, Any] | None = None
    guard = "template:no-model"
    if gateway is not None:
        got = gateway.complete(company.id, PROMPT, VERSION, inputs, history=hist)
        guard = got.guard
        out = got.output if got.ok and isinstance(got.output, dict) else None
    if out is None:
        new = ChatMessage(thread_id=t.id, role="ai", text=NO_MODEL, proposal={"kind": "reply"})
        if m is not None:  # the set stays as it was: still there to apply
            new.proposal = {**prop(m), "kind": "reply"}
    elif out.get("clarify"):
        new = ChatMessage(
            thread_id=t.id,
            role="ai",
            text=str(out["clarify"])[:300],
            proposal={**prop(m), "kind": "reply"} if m is not None else {"kind": "reply"},
        )
    else:
        drafted: list[dict[str, Any]] = []
        refused = 0
        raws = [r for r in (out.get("changes") or [])[:MAX] if isinstance(r, dict)]
        live = {r.key for r in service.active(s, ["metric"])}
        # 2.4 L2: a new number drafted beside a formula that uses it (Rebates, for "minus rebates")
        known = {
            str(r.get("key") or "")
            for r in raws
            if r.get("type") == "metric" and r.get("op") == "put" and r.get("key") not in live
        }
        for raw in raws:
            d = hc.check(s, {"change": raw, "reply": ""}, inputs, text, today, known=known)
            if d.change is None:
                refused += 1
                continue
            if any((x["type"], x["key"]) == (d.change["type"], d.change["key"]) for x in drafted):
                continue  # one change per record
            drafted.append(d.change)
        # a formula whose new part was refused can't stand alone
        keys = {c["key"] for c in drafted}
        kept = [c for c in drafted if not ((_refs(c) & known) - keys)]
        refused += len(drafted) - len(kept)
        drafted = kept
        new_items = [
            item(
                s,
                company,
                n,
                c,
                also=[x for x in drafted if x["key"] in needs_of(c, drafted, live)],
            )
            for n, c in enumerate(drafted, start=1)
        ]
        said = str(out.get("reply") or "").strip()[:300]
        if said and not numbers_grounded(said, {**inputs, "said": text}):
            said = ""
        if not new_items:
            said = hc.COULDNT if refused or not said else said
        elif not said:
            n = len(new_items)
            said = f"Here {'is the change' if n == 1 else f'are the {n} changes'} I'd make."
        if refused and new_items:
            said += f" I left out {refused} I couldn't turn into a change."
        new = ChatMessage(
            thread_id=t.id,
            role="ai",
            text=said,
            proposal={"kind": "set", "items": new_items} if new_items else {"kind": "reply"},
        )
    if m is not None and (new.proposal or {}).get("items") is not None:
        m.applied = True  # superseded
        m.proposal = {**prop(m), "superseded": True}
    elif m is not None and out is not None and not out.get("clarify"):
        m.applied = True  # the owner's message emptied the list
        m.proposal = {**prop(m), "superseded": True}
    s.add(new)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="othor_chat.drafted",
        object_type="chat_thread",
        object_id=t.id,
        after={
            "subject": t.subject,
            "guard": guard,
            "changes": len((new.proposal or {}).get("items") or []),
        },
    )
    return new


def _picked(i: dict[str, Any]) -> dict[str, Any]:
    """An item whose options each carry a whole change (a goal's, 0.28): the chosen one's."""
    opt = next(
        (o for o in i.get("options") or [] if o.get("key") == i.get("choice") and o.get("change")),
        None,
    )
    if opt is None:
        return i
    return {
        **i,
        "change": opt["change"],
        "after": opt["label"],
        "replaces": opt.get("replaces"),
    }


def apply(
    s: Session, company: Any, t: ChatThread, *, actor: str, today: dt.date | None = None
) -> ChatMessage | None:
    """Every ready change of the current set in one Business Model version, then compiled."""
    from othor.chat import service as chat
    from othor.ontology.compiler import compile_version

    m = chat.last_set(s, t)
    if m is None:
        return None
    every = list(prop(m)["items"])
    ready = [_picked(i) for i in pending.to_apply(every)]
    # 2.4 L2: a formula goes with the new numbers it's built from, even one set aside
    have = {i["change"]["key"] for i in ready if i.get("change")}
    for i in list(ready):
        for x in every:
            key = (x.get("change") or {}).get("key")
            if key and key in (i.get("needs") or []) and key not in have:
                ready.append(x)
                have.add(key)
    started = [i for i in ready if i["change"]["type"] == "watch"]
    ready_records = [i for i in ready if i["change"]["type"] != "watch"]
    puts = [
        service.retire(i["change"]["type"], i["change"]["key"])
        if i["change"]["op"] == "retire"
        else service.put(
            i["change"]["type"], i["change"]["key"], i["change"]["body"], {"chat": str(t.id)}
        )
        for i in ready_records
    ]
    # 0.28: a goal changed to a new date has a new key; the old one goes in the same version
    puts += [service.retire("goal", i["replaces"]) for i in ready_records if i.get("replaces")]
    v = (
        service.apply(
            s,
            puts,
            actor=actor,
            reason=f"Othor AI chat: {len(puts)} change{'s' if len(puts) != 1 else ''}",
        )
        if puts
        else None
    )
    m.applied = True
    for i in started:  # 2.4 L2: a watch starts now, outside the Business Model's versions
        from othor.engine import watches

        b = i["change"]["body"]
        watches.create(
            s,
            label=b["label"],
            indicator={"kind": "value", "metric": b["metric"], "grain": "month"},
            threshold=b["threshold"],
            direction=b["direction"],
            owner_name=b.get("owner") or "",
            since=today or dt.date.today(),
            actor=actor,
        )
    n = len(ready_records) if v is not None else 0
    text = (
        f"Applied {n} change{'s' if n != 1 else ''} as Business Model v{v.number}."
        if v is not None
        else "Nothing was applied: those lines are already in your Business Model."
        if not started
        else ""
    )
    if started:
        w = len(started)
        text = (text + " " if text else "") + f"Started {w} watch{'es' if w != 1 else ''}."
    done = ChatMessage(
        thread_id=t.id,
        role="ai",
        text=text + " Tell me if anything else changed.",
        proposal={
            "kind": "applied",
            "version": v.number if v is not None else None,
            "applied": [
                {
                    "text": i["text"],
                    "answer": i.get("after") or i.get("before"),
                    "effect": i["effect"],
                }
                for i in ready
            ]
            if v is not None or started
            else [],
        },
        applied=True,
    )
    s.add(done)
    s.flush()
    if v is not None:
        compile_version(s, company, actor=actor)
    record_event(
        s,
        actor=actor,
        action="othor_chat.applied",
        object_type="chat_thread",
        object_id=t.id,
        after={"subject": t.subject, "version": v.number if v is not None else None, "changes": n},
    )
    return done
