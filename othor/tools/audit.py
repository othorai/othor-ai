# SPDX-License-Identifier: Apache-2.0
"""The Audit log as a read-only tool (0.35): "who changed the Net revenue definition last week?"
is answered from the events, as the Audit log page words them. Its own calls are audited too
(`tool.called`)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from othor.audit import view, words
from othor.core import fmt
from othor.tools import Card, Ctx, Tool, register

SCAN = 2_000  # the most recent rows a question looks through


def _search(ctx: Ctx, args: dict[str, Any]) -> Card:
    days = int(args.get("days") or 7)
    limit = int(args.get("limit") or 10)
    about = str(args.get("about") or "").strip().lower()
    kind = str(args.get("kind") or "")
    f = view.Filters(kind=kind if kind in words.KIND_LABEL else "", days="all")
    now = (
        dt.datetime.combine(ctx.today + dt.timedelta(days=1), dt.time(0), dt.UTC)
        if isinstance(ctx.today, dt.date)
        else dt.datetime.now(dt.UTC)
    )
    since = now - dt.timedelta(days=days + 1)
    got: list[view.Row] = []
    cursor: str | None = None
    seen = 0
    done = False
    while not done and seen < SCAN:
        p = view.page(ctx.s, f, now, cursor, size=200)
        for r in p.rows:
            seen += 1
            if view._utc(r.at) < since:
                done = True
                break
            if not about or about in r.text.lower() or about in r.who.lower():
                got.append(r)
                if len(got) >= limit:
                    done = True
                    break
        cursor = p.more
        done = done or cursor is None
    where = f" about “{args['about']}”" if about else ""
    span = f"the last {fmt.num(days)} day{'s' if days != 1 else ''}"
    return Card(
        "audit_search",
        f"Who did what{where} in {span}",
        facts=[(r.when, f"{r.who}: {r.text}") for r in got],
        lines=["Every row is on the Audit log (/audit), newest first."] if got else [],
        empty="" if got else f"Nothing{where} in {span}.",
    )


register(
    Tool(
        "audit_search",
        "Who did what in Othor AI, and when, from the Audit log: changes to the Business Model, "
        "sources, briefs and settings, sign-ins and runs. `about` narrows to rows naming it (a "
        "number, a source, a person); `days` is how far back (default 7); `kind` is one of "
        + ", ".join(k for k, _ in words.KINDS)
        + ".",
        {
            "type": "object",
            "properties": {
                "about": {"type": "string", "maxLength": 120},
                "days": {"type": "integer", "minimum": 1, "maximum": 365},
                "kind": {"type": "string", "enum": [k for k, _ in words.KINDS]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "additionalProperties": False,
        },
        _search,
        keywords=("who changed", "who did", "audit log", "who signed in"),
    )
)
