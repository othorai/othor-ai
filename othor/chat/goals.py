# SPDX-License-Identifier: Apache-2.0
"""Goals in the one Othor AI chat (0.28, owner 19 Nov 2026: "let the user add a goal through a
single chat input, then show suggested options by AI for the inputs not given, with apply and
edit").

Subjects: "ontology:goal" (Add a goal) and "ontology:goal:<key>" (change one goal). Before the
first message the chat says what a goal needs (the number, the target, by when) with examples.
The owner writes one line; one model call (`goal_parse` v1) reads which of the confirmed numbers
it means and whatever the owner gave of the target, the date, at least or at most, a year's total
or a level. Code then checks every part and fills the rest:
- the number: one of the company's confirmed numbers, or up to three suggested from the briefs'
  headline numbers when the owner didn't name one;
- the target: only from the owner's own words (its digits must be in what they wrote, read in the
  number's unit), or up to three computed by code (`goal_targets`: where it's heading, a stretch,
  last year's actual) — never a model's figure (non-negotiable 1);
- by when: the owner's date, else the end of this fiscal year; the kind: the number's own.
Each choice is a whole goal, shown as one card's options with Othor AI's pick; Apply writes it as
one Business Model version (non-negotiable 6); the owner's next line reshapes it."""

from __future__ import annotations

import contextlib
import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from othor.chat.service import prop
from othor.core.events import record_event
from othor.ontology import service
from othor.ontology.goal_suggest import _headline, build_options, goal_key  # noqa: F401
from othor.setup.models import ChatMessage, ChatThread

PROMPT, VERSION = "goal_parse", "v1"
SUBJECT = "ontology:goal"
INTRO = (
    "Tell me your goal in one line: which number, the target, and by when. For example "
    "“Net revenue at least ₹60 Cr this financial year” or “Keep DSO under 45 days by March”. "
    "Leave out anything you're unsure of and I'll suggest it from your numbers; nothing changes "
    "until you apply."
)
CHANGE_INTRO = (
    "What should change about this goal: the target, the date, or the number? Say it in a line, "
    "or say “remove it”; nothing changes until you apply."
)
NO_MODEL = "I can't read a goal right now. Nothing has changed; try again in a little while."
SUGGEST_LINE = "Suggest a goal"
# 0.28: "remove this goal" in a goal's own chat drafts its removal, read by code (no model)
REMOVE = re.compile(r"\b(remove|delete|drop|scrap|cancel|get rid of)\b", re.I)
WHICH = "Which number is the goal for? Name it, for example “net revenue” or “DSO”."


def _and(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def is_subject(subject: str) -> bool:
    return subject == SUBJECT or subject.startswith(SUBJECT + ":")


def _goal_key(subject: str) -> str | None:
    return subject.removeprefix(SUBJECT + ":") if subject.startswith(SUBJECT + ":") else None


def subject_ok(s: Session, subject: str) -> bool:
    key = _goal_key(subject)
    return key is None or any(r.key == key for r in service.active(s, ["goal"]))


def title(subject: str) -> str:
    return "Change this goal" if _goal_key(subject) else "Add a goal"


def opening(subject: str) -> str:
    return CHANGE_INTRO if _goal_key(subject) else INTRO


def _numbers(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "key": k,
            "name": str(m.get("name") or k),
            "unit": m.get("unit") or "count",
            "kind": m.get("kind") or "flow",
        }
        for k, m in sorted(spec.get("metrics", {}).items())
        if m.get("status") == "active"
    ]


def read(
    out: dict[str, Any], numbers: list[dict[str, Any]], said: str, today: dt.date
) -> dict[str, Any]:
    """The model's reading of the owner's line, kept only where code can check it."""
    from othor.briefs import values

    keys = {n["key"]: n for n in numbers}
    got: dict[str, Any] = {}
    metric = out.get("metric")
    if isinstance(metric, str) and metric in keys:
        got["metric"] = metric
    target = out.get("target")
    if isinstance(target, str) and target.strip() and "metric" in got:
        digits = re.findall(r"\d+(?:\.\d+)?", target)
        said_digits = set(re.findall(r"\d+(?:\.\d+)?", said.replace(",", "")))
        if digits and all(d in said_digits for d in digits):  # the owner's own figure only
            with contextlib.suppress(ValueError, InvalidOperation):
                got["target"] = values.parse(keys[got["metric"]]["unit"], target)
    by = out.get("by")
    if isinstance(by, str):
        try:
            d = dt.date.fromisoformat(by)
            if today <= d <= today + dt.timedelta(days=3 * 366):
                got["by"] = d
        except ValueError:
            pass
    if out.get("direction") in ("at_least", "at_most"):
        got["direction"] = out["direction"]
    if out.get("aggregate") in ("fiscal_year_total", "value"):
        got["aggregate"] = out["aggregate"]
    return got


WANTED = (
    ("stretch", ("stretch", "ambitious", "push harder", "higher target")),
    ("last_year", ("last year", "last year's", "same as last")),
    ("heading", ("heading", "on course", "where it's going", "realistic")),
)


def wanted(text: str) -> str | None:
    """Which computed target the owner asked for in words, if any."""
    low = text.lower()
    for kind, words in WANTED:
        if any(w in low for w in words):
            return kind
    return None


def options(
    s: Session,
    company: Any,
    rd: Any,
    got: dict[str, Any],
    today: dt.date,
    replaces: str | None = None,
    prefer: str | None = None,
) -> list[dict[str, Any]]:
    from othor.engine.store import read_json

    fc = (read_json(rd.files.outputs / "forecasts.json") or {}) if rd.files else {}
    return build_options(
        rd.spec,
        rd.points,
        fc.get("rows") or [],
        int(company.fiscal_year_start_month or 4),
        got,
        today,
        replaces,
        prefer,
    )


def _current(s: Session, t: ChatThread) -> dict[str, Any]:
    """The goal being changed (its record), or the last card's pick, as fields to keep."""
    from othor.chat import service as chat

    key = _goal_key(t.subject)
    if key:
        rec = next((r for r in service.active(s, ["goal"]) if r.key == key), None)
        if rec is not None:
            b = rec.body
            return {
                "metric": b.get("metric"),
                "target": str(b.get("target")),
                "by": b.get("by"),
                "direction": b.get("direction"),
                "aggregate": b.get("aggregate"),
            }
    m = chat.last_set(s, t)
    if m is not None:
        for i in prop(m).get("items") or []:
            pick = next(
                (o for o in i.get("options") or [] if o.get("key") == i.get("choice")), None
            )
            if pick:
                b = pick["change"]["body"]
                return {k: b.get(k) for k in ("metric", "target", "by", "direction", "aggregate")}
    return {}


def _removal(
    s: Session, t: ChatThread, text: str, key: str, rd: Any, *, actor: str
) -> ChatMessage | None:
    """The goal's removal as one card: Apply retires it (a new Business Model version)."""
    from othor.chat import service as chat
    from othor.ontology.goal_targets import goal_text

    rec = next((r for r in service.active(s, ["goal"]) if r.key == key), None)
    if rec is None:
        return None
    metrics = rd.spec.get("metrics", {})
    names = {k: str(m.get("name") or k) for k, m in metrics.items()}
    unit = (metrics.get(rec.body.get("metric")) or {}).get("unit")
    label = goal_text(rec.body, unit, names)
    last = chat.last_set(s, t)
    s.add(ChatMessage(thread_id=t.id, role="user", text=text))
    item = {
        "id": "c:1",
        "kind": "change",
        "text": "Remove · Goal",
        "before": label,
        "after": "",
        "lines": [],
        "effect": "Briefs stop showing how likely it is.",
        "answer_type": "choice",
        "options": [{"key": "apply", "label": label, "note": "", "tag": None}],
        "choice": "apply",
        "status": "pick",
        "change": {"op": "retire", "type": "goal", "key": key},
        "high_impact": False,
    }
    new = ChatMessage(
        thread_id=t.id,
        role="ai",
        text="I'll remove this goal when you apply.",
        proposal={"kind": "set", "items": [item]},
    )
    if last is not None:
        last.applied = True  # superseded
        last.proposal = {**prop(last), "superseded": True}
    s.add(new)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="othor_chat.drafted",
        object_type="chat_thread",
        object_id=t.id,
        after={"subject": t.subject, "guard": "code:remove", "changes": 1},
    )
    return new


def draft(
    s: Session,
    gateway: Any,
    company: Any,
    settings: Any,
    t: ChatThread,
    text: str,
    *,
    actor: str,
    today: dt.date,
    suggest: bool = False,
) -> ChatMessage | None:
    """The owner's line → one goal card: their parts as given, the rest suggested (never
    applied). `suggest` (Suggest a goal, no line): the whole goal from the company's own numbers,
    by code alone (no model call)."""
    from othor.ai.guards import numbers_grounded
    from othor.chat import service as chat
    from othor.web.ontology_view import run_data

    text = SUGGEST_LINE if suggest else text.strip()[:500]
    if not text:
        return None
    rd = run_data(s, settings)
    numbers = _numbers(rd.spec)
    key = _goal_key(t.subject)
    if key and not suggest and REMOVE.search(text):
        return _removal(s, t, text, key, rd, actor=actor)
    current = _current(s, t)
    hist = chat._history(s, t)
    last = chat.last_set(s, t)
    s.add(ChatMessage(thread_id=t.id, role="user", text=text))
    s.commit()  # the gateway logs the call in its own session
    from othor.ontology import goal_targets

    inputs = {
        "said": text,
        "numbers": numbers,
        "today": today.isoformat(),
        "fiscal_year_end": goal_targets.fiscal_year_end(
            today, int(company.fiscal_year_start_month or 4)
        ).isoformat(),
        "current": current or None,
    }
    out: dict[str, Any] | None = None
    guard = "template:no-model"
    if suggest:
        out, guard = {}, "code:suggest"
    elif gateway is not None:
        got = gateway.complete(company.id, PROMPT, VERSION, inputs, history=hist)
        guard = got.guard
        out = got.output if got.ok and isinstance(got.output, dict) else None
    if out is None:
        new = ChatMessage(thread_id=t.id, role="ai", text=NO_MODEL, proposal={"kind": "reply"})
    else:
        fields = read(out, numbers, text, today)
        # the parts the owner gave before stay unless this line changes them
        for k, v in current.items():
            if k not in fields and v is not None:
                if k == "by":
                    fields[k] = dt.date.fromisoformat(str(v))
                elif k == "target":
                    fields[k] = Decimal(str(v))
                else:
                    fields[k] = v
        prefer = wanted(text)
        if prefer:
            fields.pop("target", None)  # a computed target asked for by name replaces the old one
        opts = options(s, company, rd, fields, today, replaces=_goal_key(t.subject), prefer=prefer)
        said = str(out.get("reply") or "").strip()[:300]
        if said and not numbers_grounded(said, {"said": text}):
            said = ""
        if not opts:
            said = str(out.get("clarify") or "").strip()[:300] or WHICH
            if not numbers_grounded(said, {"said": text}):
                said = WHICH
            new = ChatMessage(thread_id=t.id, role="ai", text=said, proposal={"kind": "reply"})
        else:
            missing = [
                w
                for w, k in (("the number", "metric"), ("the target", "target"), ("the date", "by"))
                if k not in fields
            ]
            if not said:
                said = (
                    f"I suggested {_and(missing)} from your numbers."
                    if missing
                    else "Here's your goal. Apply it, or tell me what to change."
                )
            item = {
                "id": "c:1",
                "kind": "change",
                "text": "Change · Goal" if _goal_key(t.subject) else "Add · Goal",
                "before": "",
                "after": opts[0]["label"],
                "lines": [],
                "effect": "Every night shows how likely you are to reach it.",
                "answer_type": "choice",
                "options": opts,
                "choice": opts[0]["key"],
                "status": "pick",
                "change": opts[0]["change"],
                "high_impact": False,
            }
            new = ChatMessage(
                thread_id=t.id,
                role="ai",
                text=said,
                proposal={"kind": "set", "items": [item]},
            )
    if last is not None and (new.proposal or {}).get("items") is not None:
        last.applied = True  # superseded
        last.proposal = {**prop(last), "superseded": True}
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
