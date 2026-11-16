# SPDX-License-Identifier: Apache-2.0
"""A derived metric's signed parts (M16.1): from the record's formula, so Summary can expand a
row along the real arithmetic (Net revenue = invoice value − credit notes − rebates).

Only a pure sum of metrics has terms: `+` and `-`, nested or with several arguments. Anything
else (× ÷, abs, a number, a function) is not a sum, and `terms` returns None."""

from __future__ import annotations

from typing import Any


def terms(formula: Any) -> list[dict[str, Any]] | None:
    out: list[dict[str, Any]] = []

    def walk(node: Any, sign: int) -> bool:
        if not isinstance(node, dict):
            return False
        if set(node) <= {"metric", "missing"} and isinstance(node.get("metric"), str):
            out.append({"metric": node["metric"], "sign": sign})
            return True
        args = node.get("args")
        if set(node) != {"op", "args"} or not isinstance(args, list) or not args:
            return False
        if node["op"] == "+":
            return all(walk(a, sign) for a in args)
        if node["op"] == "-":
            if len(args) == 1:
                return walk(args[0], -sign)
            return walk(args[0], sign) and all(walk(a, -sign) for a in args[1:])
        return False

    if not walk(formula, 1) or len(out) < 2:
        return None
    return out
