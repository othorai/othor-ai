# SPDX-License-Identifier: Apache-2.0
"""Who did it: one way to write and read the `actor` of an event (0.35, the Audit log).

Written forms (no schema change: `event.actor` is String(64)):

- a person: their user id, `<uuid>` (also read: `user:<uuid>`, and, for rows written before
  0.35, their email address);
- `othor_ai:<uuid>`: Othor AI acting on that person's request (a chat change it applied, a
  "Let Othor AI decide" answer, a tool it called in chat);
- `system`: the night, the scheduler and the app itself (`trial` too);
- `operator`: Othor support, from the command line on the server (`cli` too);
- `mcp:<key id>`: an AI app connected with a key;
- `recipient:<id>`: someone a brief is sent to, from a signed link.

When the owner applies a draft Othor AI wrote, the actor stays the owner and the event's
`after.drafted_by` is "othor_ai" (the Audit log says "Applied Othor AI's draft: …")."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

SYSTEM = "system"
OPERATOR = "operator"
DRAFTED_BY_AI = "othor_ai"  # after.drafted_by when the owner applies Othor AI's draft

# kinds
USER = "user"
OTHOR_AI = "othor_ai"
SYSTEM_KIND = "system"
SUPPORT = "support"
MCP = "mcp"
RECIPIENT = "recipient"

_SYSTEM_WORDS = {"system", "trial", "scheduler", "worker", "night"}
_SUPPORT_WORDS = {"operator", "cli", "admin", "support"}


@dataclass(frozen=True)
class Actor:
    kind: str
    user_id: str | None = None  # a person's id (str), for user and othor_ai
    email: str | None = None  # an old row's email actor, before it's matched to a user
    key: str | None = None  # an MCP key's id, a recipient's id
    raw: str = ""


def user(uid: uuid.UUID | str) -> str:
    """A person, as written."""
    return str(uid)


def othor_ai(uid: uuid.UUID | str | None) -> str:
    """Othor AI acting on this person's request. Without a person it's just Othor AI."""
    if uid is None or str(uid) in ("", "owner"):
        return "othor_ai"
    a = parse(str(uid))
    return f"othor_ai:{a.user_id}" if a.user_id else "othor_ai"


def mcp(key_id: uuid.UUID | str) -> str:
    return f"mcp:{key_id}"


def _uuid(text: str) -> str | None:
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError):
        return None


def parse(raw: str | uuid.UUID | None) -> Actor:
    text = str(raw or "").strip()
    low = text.lower()
    head, _, rest = text.partition(":")
    if rest and head.lower() == "user":
        uid = _uuid(rest)
        return Actor(USER, user_id=uid, raw=text) if uid else Actor(USER, raw=text)
    if head.lower() == "othor_ai":
        return Actor(OTHOR_AI, user_id=_uuid(rest) if rest else None, raw=text)
    if rest and head.lower() == "mcp":
        return Actor(MCP, key=rest, raw=text)
    if rest and head.lower() == "recipient":
        return Actor(RECIPIENT, key=rest, raw=text)
    uid = _uuid(text)
    if uid:
        return Actor(USER, user_id=uid, raw=text)
    if "@" in text and " " not in text:
        return Actor(USER, email=low, raw=text)
    if low in _SYSTEM_WORDS or not text:
        return Actor(SYSTEM_KIND, raw=text)
    if low in _SUPPORT_WORDS:
        return Actor(SUPPORT, raw=text)
    if low == "owner":  # an early chat job's fallback: the person isn't known
        return Actor(USER, raw=text)
    # anything else was written by a script on the server (a demo install, a test seed)
    return Actor(SUPPORT, raw=text)


def first_name(name: str) -> str:
    n = (name or "").strip()
    if "@" in n and " " not in n:
        n = n.split("@")[0]
    return n.split(" ")[0] if n else ""


def label(
    a: Actor,
    names: Mapping[str, str],
    *,
    by_email: Mapping[str, str] | None = None,
    unknown: str = "A former member",
) -> str:
    """Who, in plain words. `names` maps a user id (str) to the person's name (or email);
    `by_email` maps an old row's email to the user id."""
    uid = a.user_id or ((by_email or {}).get(a.email or "") if a.email else None)
    if a.kind == USER:
        if uid and names.get(uid):
            return names[uid]
        return a.email or unknown
    if a.kind == OTHOR_AI:
        who = first_name(names.get(uid or "", ""))
        return f"Othor AI (asked by {who})" if who else "Othor AI"
    if a.kind == SUPPORT:
        return "Othor support"
    if a.kind == MCP:
        return "A connected AI app"
    if a.kind == RECIPIENT:
        return "Someone the brief is sent to"
    return "System"


def person_id(a: Actor, by_email: Mapping[str, str] | None = None) -> str | None:
    """The person behind an actor (the asker for Othor AI), when there is one."""
    if a.user_id:
        return a.user_id
    if a.email:
        return (by_email or {}).get(a.email)
    return None
