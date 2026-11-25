# SPDX-License-Identifier: Apache-2.0
"""#119: a model that writes the tool as the text "null" (or "none", "") picked no tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from othor.chat.ask import _route


@dataclass
class _Got:
    output: dict[str, Any]
    ok: bool = True
    guard: str = "ok"


class _Gateway:
    def __init__(self, tool: Any) -> None:
        self.tool = tool

    def complete(self, *_a: Any, **_k: Any) -> _Got:
        return _Got({"tool": self.tool, "args_json": "{}", "change": False, "reply": "Hello."})


@pytest.mark.parametrize("raw", ["null", "NULL", " Null ", "none", "None", "", "  ", None])
def test_null_text_is_no_tool(raw: Any) -> None:
    tool, _args, change, reply, _guard = _route(_Gateway(raw), 1, "hi", ())
    assert tool is None
    assert not change
    assert reply == "Hello."


def test_a_real_tool_name_is_kept_trimmed() -> None:
    tool, *_ = _route(_Gateway(" brief_today "), 1, "what's in today's brief", ())
    assert tool == "brief_today"
