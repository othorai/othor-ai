# SPDX-License-Identifier: Apache-2.0
"""The figure check (rule 1, AI drafts, code computes): every number a model writes must be one
of the computed values it was given. Moved here from Big Picture (v0.19) for every writer that
words computed results (Look back, Discover's write-up, 0.29); behaviour unchanged.

- `Values.note(text, href)` registers every numeric span in a computed text (a range's ends too).
- `sentence(text, values)` splits a sentence the model wrote into segments, each figure linked
  to where it came from; None when any figure isn't a computed value (the sentence is dropped,
  never repaired). `\\d` matches any script's digits (Devanagari too).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_UNIT = r"(?:\s?(?:Cr|L m|L|K|M|B|%|pp|d\b|days?|weeks?|months?|m\b|x|×))?"
# one numeric span: an optional sign and currency, the digits, then a unit or a month (and year)
NUM = re.compile(
    r"(?:(?:" + _MONTHS + r")\s(?=\d{4}))?"  # a month before its year: Mar 2027
    r"(?:[−\-+]\s?)?(?:[₹$]\s?)?(?:FY)?\d+(?:,\d+)*(?:\.\d+)?"
    r"(?:–\d+(?:,\d+)*(?:\.\d+)?)?"  # a range: 37–50
     + _UNIT + r"(?:\s(?:" + _MONTHS + r")(?:\s\d{4})?)?"
)
_RANGE = re.compile(r"^([−\-+]?[₹$]?)(\d+(?:,\d+)*(?:\.\d+)?)–(\d+(?:,\d+)*(?:\.\d+)?)(.*)$")


def norm(n: str) -> str:
    return "−" + n[1:] if n.startswith("-") else n  # a hyphen-minus is the minus sign


@dataclass
class Values:
    """The computed values the facts carry: text → where it came from (the first place wins, so
    the same facts give the same table every time)."""

    table: dict[str, str] = field(default_factory=dict)

    def _add(self, n: str, href: str) -> None:
        self.table.setdefault(n, href)

    def note(self, text: str, href: str = "") -> str:
        """Register every numeric span in a computed text; the text is returned as it is."""
        for m in NUM.finditer(text or ""):
            n = m.group(0)
            self._add(n, href)
            r = _RANGE.match(n)
            if r:  # a range's ends are computed values too: ₹54.1–59.2 Cr → ₹54.1 Cr, ₹59.2 Cr
                pre, lo, hi, unit = r.groups()
                self._add(f"{pre}{lo}{unit}", href)
                self._add(f"{pre}{hi}{unit}", href)
        return text or ""

    def find(self, n: str) -> str | None:
        """Where a figure the model wrote came from, or None when it isn't a computed value."""
        n = norm(n)
        if n in self.table:
            return self.table[n]
        for k, v in self.table.items():  # a signed value written without its sign
            if k[:1] in "+−" and k[1:] == n:
                return v
        return None


def sentence(text: Any, vals: Values) -> list[dict[str, str]] | None:
    """One sentence as segments [{t}, {t, href}]: each figure a computed value, linked to its
    source. None when the check drops it: a figure that isn't computed, or a stray numeral."""
    if not isinstance(text, str) or not text.strip():
        return None
    out: list[dict[str, str]] = []
    pos = 0
    for m in NUM.finditer(text):
        href = vals.find(m.group(0))
        if href is None:
            return None  # a number the model wrote itself
        if m.start() > pos:
            out.append({"t": text[pos : m.start()]})
        out.append({"t": m.group(0), "href": href} if href else {"t": m.group(0)})
        pos = m.end()
    if pos < len(text):
        out.append({"t": text[pos:]})
    return out  # (every digit starts a NUM span, so none is left unchecked)


def plain(segments: list[dict[str, str]] | None) -> str:
    return "".join(s["t"] for s in segments or [])
