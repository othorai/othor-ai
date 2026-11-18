# SPDX-License-Identifier: Apache-2.0
"""Ask v0.1 in the one Othor AI chat (0.26, M26.6): "Ask Othor AI" in every top bar.

A question → `ask_route` v1 picks one read-only tool and its arguments from the registry's names,
descriptions and schemas (never a row) → the tool runs through the one gate (`othor.tools.call`:
role, schema, audit) → `ask_answer` v1 words the answer from the card's computed facts; a sentence
with a figure the card doesn't hold is not shown (the card shows alone). The card is stored with
the message, with where it came from; the answer's numbers are always the card's (rule 1).

A change request ("move the Diwali goal") is pointed to Tell Othor AI, where changes are drafted
and applied. Without a model: the question's words are matched to a tool, and the card shows
without words (rule 4)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from othor import tools
from othor.core.events import record_event
from othor.setup.models import ChatMessage, ChatThread

ROUTE, ANSWER = ("ask_route", "v1"), ("ask_answer", "v1")
NONE = (
    "I can answer about your numbers, what Sweep found, which checks ran, your Business Model's "
    "changes and the questions waiting for you. Try one of those."
)
CHANGE = "To change your Business Model, tell me in Tell Othor AI: I'll draft it for you to apply."


def _route(
    gateway: Any, company_id: Any, text: str, hist: tuple[dict[str, Any], ...]
) -> tuple[str | None, dict[str, Any], bool, str, str]:
    """(tool, args, change?, reply, guard)."""
    if gateway is None:
        return tools.match(text), {}, False, "", "template:no-model"
    got = gateway.complete(
        company_id, *ROUTE, {"question": text, "tools": tools.listing()}, history=hist
    )
    out = got.output if got.ok and isinstance(got.output, dict) else None
    if out is None:  # the model is down: code's match
        return tools.match(text), {}, False, "", got.guard
    try:
        args = json.loads(str(out.get("args_json") or "{}"))
    except ValueError:
        args = {}
    tool = out.get("tool") if isinstance(out.get("tool"), str) else None
    return (
        tool,
        args if isinstance(args, dict) else {},
        bool(out.get("change")),
        str(out.get("reply") or "")[:300],
        got.guard,
    )


def _words(gateway: Any, company_id: Any, text: str, card: tools.Card) -> str:
    from othor.ai.guards import numbers_grounded

    if gateway is None or card.empty:
        return ""
    inputs = {"question": text, "card": card.as_json()}
    got = gateway.complete(company_id, *ANSWER, inputs)
    out = got.output if got.ok and isinstance(got.output, dict) else None
    said = str((out or {}).get("answer") or "").strip()[:600]
    return said if said and numbers_grounded(said, inputs) else ""


def answer(
    s: Session,
    gateway: Any,
    tctx: tools.Ctx,
    t: ChatThread,
    text: str,
    *,
    actor: str,
) -> ChatMessage | None:
    from othor.chat import service as chat

    text = text.strip()[:500]
    if not text:
        return None
    hist = chat._history(s, t)
    s.add(ChatMessage(thread_id=t.id, role="user", text=text))
    s.commit()  # the gateway logs the call in its own session
    name, args, change, reply, guard = _route(gateway, tctx.company.id, text, hist)
    card: tools.Card | None = None
    if change:
        said = CHANGE
    elif name is None:
        said = reply or NONE
    else:
        try:
            card = tools.call(tctx, name, args, actor=actor, via="chat")
        except tools.Refused as e:
            said = f"I couldn't look that up: {e}."
        else:
            s.commit()  # the audit event first; the wording call logs in its own session
            said = (
                card.empty or _words(gateway, tctx.company.id, text, card) or "Here's what I found."
            )
    new = ChatMessage(
        thread_id=t.id,
        role="ai",
        text=said,
        proposal={
            "kind": "answer",
            **({"card": card.as_json()} if card is not None else {}),
            **({"change": True} if change else {}),
        },
    )
    s.add(new)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="othor_chat.asked",
        object_type="chat_thread",
        object_id=t.id,
        after={"tool": name, "guard": guard, "change": change},
    )
    return new
