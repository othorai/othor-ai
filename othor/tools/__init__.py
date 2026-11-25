# SPDX-License-Identifier: Apache-2.0
"""What Othor AI knows, as read-only tools (0.26, M26.6; built MCP-ready for 0.27).

One registry: each `Tool` has a name, a plain description, a JSON schema for its arguments and
`run(ctx, args) -> Card`. A `Card` is computed facts (formatted by code), a few plain lines and
where they came from (the run, the spec's hash, the Business Model version). Tools read what the
night already computed and stored; none reads a source live, none writes.

Every call goes through one gate, `call`: the caller's company scope and role (admins only for
now), the arguments checked against the schema (nothing else passes), then the tool, then an
audit event `tool.called` (who, which tool, the arguments, the run, and from where: `chat` now,
`mcp` from 0.27). The in-app chat calls only through it; the MCP server will too."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from othor.core.events import record_event


@dataclass
class Card:
    tool: str
    title: str
    facts: list[tuple[str, str]] = field(default_factory=list)  # (label, value) by code
    lines: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    empty: str = ""  # what to say when there's nothing to show
    # 2.1 (External Signals): one-click actions the chat shows under the card, each {"text",
    # "why", "label", "url"}: a POST to the app's own route; the owner's click is the apply
    actions: list[dict[str, str]] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "tool": self.tool,
            "title": self.title,
            "facts": [list(f) for f in self.facts],
            "lines": list(self.lines),
            "provenance": dict(self.provenance),
            "empty": self.empty,
        }
        if self.actions:
            out["actions"] = [dict(a) for a in self.actions]
        return out


@dataclass(frozen=True)
class Ctx:
    """What a tool may use: the session (inside the company's tenant scope), the settings, the
    company and today."""

    s: Session
    settings: Any
    company: Any
    today: Any


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_schema: dict[str, Any]
    run: Callable[[Ctx, dict[str, Any]], Card]
    keywords: tuple[str, ...] = ()  # the chat without a model matches a question by these
    # False (2.1): the chat says the card's own first line, from code, instead of a model's words
    # (a card whose rows already say everything; no repeat, no tokens)
    worded: bool = True


REGISTRY: dict[str, Tool] = {}
ROLES = frozenset({"admin"})  # who may call (0.27: per-user keys, admins first)


class Refused(ValueError):
    """The call didn't pass the gate; the message is plain."""


def register(tool: Tool) -> Tool:
    REGISTRY[tool.name] = tool
    return tool


def listing() -> list[dict[str, Any]]:
    """The tools as a model or an MCP client sees them: names, descriptions, schemas."""
    _load()
    return [
        {"name": t.name, "description": t.description, "args_schema": t.args_schema}
        for t in sorted(REGISTRY.values(), key=lambda t: t.name)
    ]


def check_args(schema: dict[str, Any], args: Any) -> dict[str, Any]:
    """A small JSON-schema check (object, properties of type string / integer / boolean, enum,
    minimum / maximum, maxLength, required, no extra properties). Raises Refused."""
    if not isinstance(args, dict):
        raise Refused("the arguments must be an object")
    props: dict[str, Any] = schema.get("properties") or {}
    extra = set(args) - set(props)
    if extra and schema.get("additionalProperties") is False:
        raise Refused(f"unknown argument {sorted(extra)[0]}")
    for name in schema.get("required") or []:
        if name not in args:
            raise Refused(f"{name} is needed")
    out: dict[str, Any] = {}
    for name, value in args.items():
        p = props.get(name) or {}
        kind = p.get("type")
        if kind == "string":
            if not isinstance(value, str) or len(value) > int(p.get("maxLength", 200)):
                raise Refused(f"{name} must be a short text")
        elif kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise Refused(f"{name} must be a whole number")
            if ("minimum" in p and value < p["minimum"]) or (
                "maximum" in p and value > p["maximum"]
            ):
                raise Refused(f"{name} is out of range")
        elif kind == "boolean" and not isinstance(value, bool):
            raise Refused(f"{name} must be yes or no")
        if "enum" in p and value not in p["enum"]:
            raise Refused(f"{name} must be one of {', '.join(map(str, p['enum']))}")
        out[name] = value
    return out


def call(
    ctx: Ctx,
    name: str,
    args: Any,
    *,
    actor: str,
    role: str = "admin",
    via: str = "chat",
    extra: dict[str, Any] | None = None,
) -> Card:
    """The one gate. Raises Refused before anything runs when the call isn't allowed. `extra`
    goes into the audit row as well (0.27: the MCP key or grant a call came with)."""
    _load()
    if role not in ROLES:
        raise Refused("only an admin can ask this")
    tool = REGISTRY.get(name)
    if tool is None:
        raise Refused(f"there's no tool called {name}")
    clean = check_args(tool.args_schema, args if args is not None else {})
    card = tool.run(ctx, clean)
    record_event(
        ctx.s,
        actor=_who(actor, via, extra),
        action="tool.called",
        object_type="tool",
        object_id=name,
        after={
            "tool": name,
            "args": clean,
            "via": via,
            "run": card.provenance.get("run"),
            **(extra or {}),
        },
    )
    return card


def _who(actor: str, via: str, extra: dict[str, Any] | None) -> str:
    """0.35: a call from the chat is Othor AI on the person's request; one from an AI app is
    that app's key (the Audit log reads both)."""
    from othor.core import actors

    if via == "mcp" and (extra or {}).get("key"):
        return actors.mcp(str((extra or {})["key"]))
    if via == "chat":
        return actors.othor_ai(actor)
    return actor


def match(question: str) -> str | None:
    """Without a model: the tool whose keywords the question names (first wins, by name)."""
    _load()
    low = question.lower()
    for t in sorted(REGISTRY.values(), key=lambda t: t.name):
        if any(k in low for k in t.keywords):
            return t.name
    return None


def _load() -> None:
    from othor.tools import audit, read  # noqa: F401  (registers the read tools)
