# SPDX-License-Identifier: Apache-2.0
"""The words a stored brief issue shows (the page, the read-only link, the email): the model's
rewrite when it passed the numbers guard, else the template's. Model-free, so delivery and the
pages never import the AI module to read an issue."""

from __future__ import annotations

from typing import Any

FIELDS = ("title", "line", "situation")


def ordinary_lines(issue_body: dict[str, Any]) -> list[str]:
    """A quiet issue's updates from its numbers (Morning Brief 2.4): computed lines, as stored."""
    if not issue_body.get("quiet"):
        return []
    return [str(x["text"]) for x in (issue_body.get("ordinary") or {}).get("lines") or []]


def card_title(issue_body: dict[str, Any], title: str) -> str:
    """A brief card's headline: on a quiet day, the key message and the first update, so the card
    says something about the numbers, not only that nothing came up (Morning Brief 2.4)."""
    lines = ordinary_lines(issue_body)
    return f"{title} {lines[0]}" if lines and title else title


def shown(issue_body: dict[str, Any], prose: dict[str, Any] | None) -> dict[str, str]:
    if prose and prose.get("source") == "model":
        return {k: str(prose["model"].get(k) or issue_body.get(k, "")) for k in FIELDS}
    return {k: str(issue_body.get(k, "")) for k in FIELDS}
