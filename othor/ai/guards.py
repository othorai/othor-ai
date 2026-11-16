# SPDX-License-Identifier: Apache-2.0
"""Guards on every model call (docs/06 "Guards"), enforced in code.

1. Inputs never carry personal columns, raw rows, credentials or other companies' data.
2. No number from model output is used unless it appears in the input JSON.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_FORBIDDEN_KEYS = {"password", "secret", "token", "access_token", "refresh_token", "rows", "values"}


class InputRefused(ValueError):
    """The prompt input broke a guard; nothing was sent."""


def numerals(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in _NUM.finditer(text)}


def numbers_grounded(output: str, inputs: Any) -> bool:
    """Every numeral in the output appears in the input JSON."""
    allowed = numerals(json.dumps(inputs, ensure_ascii=False, default=str))
    return numerals(output) <= allowed


def check_input(inputs: Any, personal_names: Iterable[str] = ()) -> None:
    """Raise if the input holds forbidden keys or names a personal column."""
    text = json.dumps(inputs, ensure_ascii=False, default=str).lower()

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if str(k).lower() in _FORBIDDEN_KEYS:
                    raise InputRefused(f"forbidden key in model input: {k}")
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(inputs)
    for p in personal_names:
        if re.search(r'"' + re.escape(p.lower()) + r'"', text):
            raise InputRefused("a personal column name is in the model input")
