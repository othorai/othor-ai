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


def actions(issue_body: dict[str, Any], prose: dict[str, Any] | None) -> list[dict[str, Any]]:
    """What to do (#110): the issue's stored actions, each with the sentence to show: the model's
    when its numbers passed (rewrite.py checks each one), else the template's."""
    stored = list(issue_body.get("actions") or [])
    said: list[str] = []
    if prose and prose.get("source") == "model":
        said = [str(x) for x in (prose.get("model") or {}).get("actions") or []]
    if len(said) != len(stored):
        said = [str(a.get("text") or "") for a in stored]
    return [{**a, "said": s} for a, s in zip(stored, said, strict=True)]


def shown(issue_body: dict[str, Any], prose: dict[str, Any] | None) -> dict[str, str]:
    if prose and prose.get("source") == "model":
        return {k: str(prose["model"].get(k) or issue_body.get(k, "")) for k in FIELDS}
    return {k: str(issue_body.get(k, "")) for k in FIELDS}


def reworded(issue_body: dict[str, Any], prose: dict[str, Any] | None) -> tuple[int, int]:
    """(reworded, sentences) (#124): of the issue's sentences shown (title, line, situation and
    each action, when not empty), how many differ from the template's, counted from the stored
    fields; a sentence dropped back to its template counts as the template's."""
    now = shown(issue_body, prose)
    pairs = [(str(issue_body.get(k) or ""), now[k]) for k in FIELDS]
    pairs += [(str(a.get("text") or ""), str(a["said"])) for a in actions(issue_body, prose)]
    pairs = [(t.strip(), s.strip()) for t, s in pairs if t.strip()]
    return sum(t != s for t, s in pairs), len(pairs)


def words_note(issue_body: dict[str, Any], prose: dict[str, Any] | None) -> str:
    """The brief's (i) Words row (#124), in plain words."""
    n, m = reworded(issue_body, prose)
    if n == 0:
        return "from templates"
    if n == m:
        return "rewritten by Othor AI, figures checked"
    return f"{n} of {m} sentences reworded by Othor AI, figures checked; the rest from templates"
