# SPDX-License-Identifier: Apache-2.0
"""The numbers in a text, numerals and numbers written as words (#118). Plain text only, no
model: the guards (`othor.ai.guards`) and the Ontology's catalogue check both read it, and the
Ontology may not import the AI module (pyproject import contracts)."""

from __future__ import annotations

import re

_NUM = re.compile(r"\d+(?:[.,]\d+)*")

_SMALL = {
    w: i
    for i, w in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        ]
    )
}
_TENS = {
    w: 10 * i
    for i, w in enumerate(
        ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"], 2
    )
}
_MULT = {"hundred": 100, "thousand": 1000}
_UNITS = {"lakh", "lakhs", "crore", "crores", "million", "millions", "billion", "billions"}
_WORD = re.compile(
    r"\b(?:"
    + "|".join([*_SMALL, *_TENS, *_MULT, *_UNITS])
    + r")(?:[- ](?:"
    + "|".join([*_SMALL, *_TENS, *_MULT, *_UNITS, "and"])
    + r"))*\b",
    re.IGNORECASE,
)


def _word_numbers(text: str) -> set[str]:
    """Numbers written as words (#118): "Fourteen customers" → 14, "twenty-one" → 21, "three
    hundred" → 300, "two lakh" → 2 (the unit is a unit, as "2 L" in the input). A lone "one" or
    "zero" is left alone ("one question", "zero in on"), and "one" leading a compound counts."""
    out: set[str] = set()
    for m in _WORD.finditer(text):
        words = [w for w in re.split(r"[- ]", m.group(0).lower()) if w and w != "and"]
        if words in (["one"], ["zero"]):
            continue
        total, cur, seen = 0, 0, False
        for w in words:
            if w in _SMALL:
                cur += _SMALL[w]
                seen = True
            elif w in _TENS:
                cur += _TENS[w]
                seen = True
            elif w in _MULT:
                cur = (cur or 1) * _MULT[w]
                if w == "thousand":
                    total, cur = total + cur, 0
                seen = True
            else:  # a unit word ends the number
                break
        if seen:
            out.add(str(total + cur))
    return out


def numerals(text: str) -> set[str]:
    """Every number in the text: numerals, and numbers written as words (#118)."""
    return {m.group(0).replace(",", "") for m in _NUM.finditer(text)} | _word_numbers(text)
