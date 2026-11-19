# SPDX-License-Identifier: Apache-2.0
"""Draft goals from the owner's files (0.28, M28.3; owner 19 Nov 2026: "setup turns quoted
targets into draft goals").

Setup already reads targets out of uploaded files ("Revenue ₹60 Cr for FY27", Board review,
slide 4), keeping a PDF's quote only when its figures are in the PDF's own text. One model call
(`goal_from_targets` v1) says which quote is a goal on which drafted number; code then keeps a
goal only when its number is one of the drafts and its target's digits are in that quote (read
in the number's unit), so the figure is always the file's, never a model's (non-negotiable 1).
The date defaults to this fiscal year's end; a level (days, a rate, a balance) is a goal on its
value by the date, never a year's total. Drafts are shown in the Business Model draft with their
source and become goal records when the owner confirms (non-negotiable 6). Without a model the
quotes stay shown as before and no goal is drafted."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.setup import drafts
from othor.setup.models import SetupDraft

PROMPT, VERSION = "goal_from_targets", "v1"
KIND = "goal"
MAX_QUOTES = 10


_LOW = re.compile(r"\b(under|below|within|less than|no more than|at most|reduce|cut|max)\b", re.I)


def _direction(quote: str) -> str:
    """The quote's own words when the model left the direction out."""
    return "at_most" if _LOW.search(quote) else "at_least"


def _cite(upload: str, locator: str) -> str:
    stem = upload.rsplit(".", 1)[0]
    return " · ".join(x for x in (stem, locator) if x)


def numbers(s: Session) -> list[dict[str, Any]]:
    """The drafted numbers a goal may be on: kept, with a definition."""
    return [
        {
            "key": r.key,
            "name": str(r.body.get("name") or r.key),
            "unit": r.body.get("unit") or "inr",
            "kind": r.body.get("kind") or "flow",
        }
        for r in drafts.stored(s)
        if r.state != "skipped" and r.body.get("status", "active") == "active"
    ]


def quotes(refs: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    """The files' target quotes that carry a figure (an image's figures are cut when it's read,
    so only a PDF's or a sheet's quote can become a goal)."""
    return [(u, r) for u, r in refs if r.kind == "target" and re.search(r"\d", r.text)][:MAX_QUOTES]


def model_input(
    qs: list[tuple[str, Any]], nums: list[dict[str, Any]], today: dt.date, fy_end: dt.date
) -> dict[str, Any] | None:
    if not qs or not nums:
        return None
    return {
        "quotes": [{"id": i, "text": r.text} for i, (_u, r) in enumerate(qs)],
        "numbers": nums,
        "today": today.isoformat(),
        "fiscal_year_end": fy_end.isoformat(),
    }


def checked(
    out: dict[str, Any] | None,
    qs: list[tuple[str, Any]],
    nums: list[dict[str, Any]],
    today: dt.date,
    fy_end: dt.date,
) -> list[tuple[str, dict[str, Any], str, str]]:
    """(key, goal body, cite, the quote) for each goal code can stand behind; the rest
    are dropped."""
    from othor.chat.goals import read
    from othor.ontology.goal_suggest import goal_key, summable
    from othor.ontology.types import validate_body

    by_key = {n["key"]: n for n in nums}
    got: dict[str, tuple[dict[str, Any], str, str]] = {}
    for item in (out or {}).get("goals") or []:
        if not isinstance(item, dict):
            continue
        i = item.get("quote")
        if not isinstance(i, int) or not 0 <= i < len(qs):
            continue
        upload, ref = qs[i]
        fields = read(item, nums, ref.text, today)  # the figure must be the quote's own
        if "metric" not in fields or "target" not in fields:
            continue
        m = by_key[fields["metric"]]
        by = fields.get("by") or fy_end
        aggregate = (fields.get("aggregate") or "fiscal_year_total") if summable(m) else "value"
        body = validate_body(
            "goal",
            {
                "name": m["name"],
                "metric": m["key"],
                "target": f"{fields['target']:f}",
                "direction": fields.get("direction") or _direction(ref.text),
                "by": by.isoformat(),
                "aggregate": aggregate,
            },
        )
        key = goal_key(m["key"], by)
        got.setdefault(key, (body, _cite(upload, ref.locator), ref.text))
    return [(k, b, c, q) for k, (b, c, q) in sorted(got.items())]


def store(s: Session, goals: list[tuple[str, dict[str, Any], str, str]]) -> None:
    """Tonight's drafts replace the last ones (a confirmed or locked draft stays)."""
    old = {r.key: r for r in drafts.stored(s, KIND)}
    for i, (key, body, cite, quote) in enumerate(goals):
        origin = {"by": "model", "prompt": f"{PROMPT}/{VERSION}", "cite": cite, "quote": quote}
        row = old.pop(key, None)
        if row is None:
            s.add(
                SetupDraft(
                    kind=KIND,
                    key=key,
                    body=body,
                    origin=origin,
                    confidence="check",
                    state="needs_confirmation",
                    position=i,
                )
            )
        elif not drafts.locked(row):
            row.body, row.origin, row.position = body, origin, i
    for row in old.values():
        if not drafts.locked(row):
            s.delete(row)
    s.flush()


def kept(s: Session) -> set[str]:
    """The drafted numbers still kept: a goal on a skipped number isn't written at Confirm."""
    return {n["key"] for n in numbers(s)}


def shown(s: Session) -> list[SetupDraft]:
    return [
        r
        for r in s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == KIND)).all()
        if r.state != "skipped"
    ]
