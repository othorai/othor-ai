# SPDX-License-Identifier: Apache-2.0
"""Numbers as slots (#122, 0.37): the model writes {n1}, code writes the number (rule 1).

The numbers guard checks that every number a model writes appears somewhere in its input, so a
computed number under the wrong label passed ("3 were suppressed" when the card says Suppressed 2
and Could not run 3). With slots, each figure reaches the model as a labelled slot
({n1} = "Suppressed: 2"); the model writes the slot where the figure goes and code fills in the
value. A sentence with a digit or a number in words outside a slot, or a slot it wasn't given, is
dropped. The numbers guard still checks what is left, as a second line.

Same method as the fine-tune test of 25 Nov 2026 (finetune-lab/brief-wording, placeholders.py).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from othor.ai.guards import _word_numbers, numbers_grounded

SLOT = re.compile(r"\{n(\d+)\}")
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
# a number as text shows it, the longest kinds first: money, a day and month, a month and year,
# 2nd, a code with a digit (TiO2, FY27), then a plain number with its unit (12%, 4.3, 3 pp, 90+)
_SPAN = re.compile(
    "|".join(
        [
            r"[+\-−]?₹\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:Cr|L|K|k|lakh|crore)\b)?",
            rf"\b\d{{1,2}}\s{_MONTH}(?:\s\d{{4}})?",
            rf"\b{_MONTH}\s\d{{4}}\b",
            r"\b\d{4}-\d{2}-\d{2}\b",
            r"\b\d+(?:st|nd|rd|th)\b",
            r"(?<!{)\b[A-Za-z]+\d\w*",
            r"(?<![\w{])[+\-−]?\d[\d,]*(?:\.\d+)?(?:\s?%|\s?pp\b|×|x\b|\+)?",
        ]
    )
)
_DIGIT = re.compile(r"\d")
INSTRUCTION = (
    "Numbers, dates, percentages and amounts reach you only as slots: {n1}, {n2}, … Each slot "
    "says what it is. Write the slot itself where its figure belongs, exactly as given, and only "
    "for the thing its label names. Never write a digit or a number in words, and never make up "
    "a slot."
)


@dataclass
class Slots:
    values: dict[str, str] = field(default_factory=dict)  # "{n1}" -> "2"
    labels: dict[str, str] = field(default_factory=dict)  # "{n1}" -> "Suppressed: 2"
    _by_value: dict[str, str] = field(default_factory=dict)

    def new(self, value: str, label: str) -> str:
        key = f"{{n{len(self.values) + 1}}}"
        self.values[key] = value
        self.labels[key] = label
        return key

    def text(self, text: str) -> str:
        """Free text with each number replaced by a slot; one value is one slot."""

        def one(m: re.Match[str]) -> str:
            raw = m.group(0)
            lead = raw[: len(raw) - len(raw.lstrip())]
            val = raw.strip()
            if val not in self._by_value:
                self._by_value[val] = self.new(val, val)
            return lead + self._by_value[val]

        return _SPAN.sub(one, text)

    def walk(self, x: Any) -> Any:
        """Every string in a JSON-like value through `text`, in order."""
        if isinstance(x, str):
            return self.text(x)
        if isinstance(x, list):
            return [self.walk(v) for v in x]
        if isinstance(x, dict):
            return {k: self.walk(v) for k, v in x.items()}
        return x

    def fill(self, text: str) -> tuple[str, list[str]]:
        """Slots -> the computed values; returns (text, slots it doesn't know)."""
        unknown: list[str] = []

        def one(m: re.Match[str]) -> str:
            if m.group(0) in self.values:
                return self.values[m.group(0)]
            unknown.append(m.group(0))
            return m.group(0)

        return SLOT.sub(one, text), unknown


def used(text: str) -> set[str]:
    return {m.group(0) for m in SLOT.finditer(text)}


def problems(sentence: str, slots: Slots, allowed: str = "") -> list[str]:
    """Why one sentence of the model's can't be shown ([] = it can): a slot it wasn't given, a
    digit outside a slot (one the owner typed in `allowed` excepted), a number in words that
    `allowed` doesn't have (#118)."""
    out: list[str] = []
    _, unknown = slots.fill(sentence)
    if unknown:
        out.append("unknown-slot")
    bare = SLOT.sub(" ", sentence)
    allowed = SLOT.sub(" ", allowed)
    typed = {m.group(0) for m in re.finditer(r"\d[\d,.]*", allowed)}
    for m in re.finditer(r"\d[\d,.]*", bare):
        if m.group(0).rstrip(".,") not in {t.rstrip(".,") for t in typed}:
            out.append("digit")
            break
    if _word_numbers(bare) - _word_numbers(allowed):
        out.append("number-word")
    return out


_BREAK = re.compile(r"(?<=[.!?])\s+(?=[\"'(*A-Z{\d])")


def sentences(line: str) -> list[str]:
    """A line split into sentences (slots hide decimals, so a full stop ends one)."""
    return [p for p in _BREAK.split(line) if p.strip()]


def keep_clean(
    text: str,
    slots: Slots,
    *,
    allowed: str = "",
    grounded_in: Any = None,
    extra: Callable[[str], bool] | None = None,
) -> tuple[str, int]:
    """The model's text with every sentence that breaks a slot rule dropped and the rest filled
    in; a filled sentence must also pass the numbers guard against `grounded_in` (the second
    line). Line breaks stay; a line left empty goes. Returns (text, sentences dropped)."""
    out_lines: list[str] = []
    dropped = 0
    for line in text.split("\n"):
        if not line.strip():
            out_lines.append("")
            continue
        m = re.match(r"^(\s*(?:[-*]|\d+\.)\s+)", line)
        lead = m.group(1) if m else ""
        body = line[len(lead) :]
        kept: list[str] = []
        for sent in sentences(body):
            filled, _ = slots.fill(sent)
            bad = problems(sent, slots, allowed)
            if not bad and grounded_in is not None and not numbers_grounded(filled, grounded_in):
                bad = ["numbers"]
            if not bad and extra is not None and not extra(filled):
                bad = ["extra"]
            if bad:
                dropped += 1
            else:
                kept.append(filled.strip())
        if kept:
            out_lines.append(lead + " ".join(kept))
    joined = "\n".join(out_lines)
    return re.sub(r"\n{3,}", "\n\n", joined).strip(), dropped


def clean_field(
    text: str, slots: Slots, *, allowed: str = "", only: Iterable[str] = ()
) -> str | None:
    """One short field (a title, an action): filled when every rule holds, else None. `only`:
    the slots this field may use (an action keeps its own figures)."""
    if problems(text, slots, allowed):
        return None
    if only and not used(text) <= set(only):
        return None
    return slots.fill(text)[0]
