# SPDX-License-Identifier: Apache-2.0
"""The pending set (Chat 1.0): what an Othor AI message proposes, item by item.

An item is a plain dict (stored in `ChatMessage.proposal["items"]`):
    id            "q:<question id>", "c:<n>" …, unique in the set
    text          the question or change, in plain words
    status        "pick" (Othor AI's pick) · "needs_you" · "changed" (the owner's reply) · "skipped"
    answer_type   "choice" or "free_text"
    options       [{"key", "label", "note", "tag", ...}]: what may be chosen; nothing else can be
    choice        the chosen option's key, or None
    answer        the owner's words for a free-text item, or None
    effect        what applying it does, in plain words (computed)
    high_impact   True when an answer could rewrite past findings: never Othor AI's pick

A reply's edits come from a model; `edit` keeps only edits that name a real item and one of its
own options (or a free-text answer for a free-text item). Everything else is dropped and counted,
so nothing the model makes up reaches the set (non-negotiable 1's spirit: code decides)."""

from __future__ import annotations

import copy
from typing import Any

APPLY = ("pick", "changed")


def ready(item: dict[str, Any]) -> bool:
    """Would Apply apply it: picked or changed, with a choice or an answer."""
    if item.get("status") not in APPLY:
        return False
    if item.get("answer_type") == "free_text":
        return bool((item.get("answer") or "").strip())
    return item.get("choice") is not None


def to_apply(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [i for i in items if ready(i)]


def edit(items: list[dict[str, Any]], edits: Any) -> tuple[list[dict[str, Any]], int]:
    """(the set after the edits, how many edits were dropped). Edits:
    {"id", "action": "pick", "option": key} · {"id", "action": "skip"} ·
    {"id", "action": "answer", "text": "..."} (free-text items only)."""
    out = copy.deepcopy(items)
    by_id = {i["id"]: i for i in out}
    dropped = 0
    for e in edits if isinstance(edits, list) else []:
        item = by_id.get(e.get("id")) if isinstance(e, dict) else None
        action = e.get("action") if isinstance(e, dict) else None
        if item is None:
            dropped += 1
            continue
        if action == "skip":
            item["status"] = "skipped"
            continue
        if action == "pick":
            keys = {o.get("key") for o in item.get("options") or []}
            if item.get("answer_type") == "free_text" or e.get("option") not in keys:
                dropped += 1
                continue
            item["choice"], item["status"] = e["option"], "changed"
            continue
        if action == "answer" and item.get("answer_type") == "free_text":
            text = str(e.get("text") or "").strip()[:500]
            if not text:
                dropped += 1
                continue
            item["answer"], item["status"] = text, "changed"
            continue
        dropped += 1
    return out, dropped


def counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """For the intro line: how many questions, how many Othor AI picked, how many need you."""
    return {
        "total": len(items),
        "picked": sum(1 for i in items if i.get("status") == "pick"),
        "needs_you": sum(1 for i in items if i.get("status") == "needs_you"),
        "apply": len(to_apply(items)),
    }
