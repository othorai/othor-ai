# SPDX-License-Identifier: Apache-2.0
"""Areas (M16.1): a short label on each metric record (Sales, Collections …) that Summary groups
rows by. Othor AI drafts them from the short list; the owner applies them and may add a name.
A metric with no area shows under "Other numbers"."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

SHORT = ("Sales", "Collections", "Operations", "Finance", "Customers")
MODEL_CHOICES = (*SHORT, "Other")  # "Other" from the model is stored as no area
OTHER = "Other numbers"
MAX_LEN = 40


def clean(value: Any) -> str | None:
    """The stored form of an area: trimmed, at most 40 characters, no digits (no number is ever
    a label). Empty, "Other" and "Other numbers" mean no area. Raises ValueError otherwise."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("an area is a short name")
    text = " ".join(value.split())
    if not text or text.casefold() in ("other", OTHER.casefold()):
        return None
    if len(text) > MAX_LEN:
        raise ValueError(f"an area is at most {MAX_LEN} characters")
    if any(c.isdigit() for c in text):
        raise ValueError("an area has no numbers in it")
    for s in SHORT:  # the short list's own spelling
        if text.casefold() == s.casefold():
            return s
    return text


def in_use(bodies: Iterable[dict[str, Any]]) -> list[str]:
    """The short list, then any name the owner added, in first-seen order: the panel's select."""
    out = list(SHORT)
    for b in bodies:
        a = b.get("area")
        if isinstance(a, str) and a not in out:
            out.append(a)
    return out


def label(area: str | None) -> str:
    return area or OTHER
