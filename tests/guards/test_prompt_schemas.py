# SPDX-License-Identifier: Apache-2.0
"""Every prompt's output schema is one the provider's structured output accepts (12 Nov 2026):
brief_draft and brief_edit v1 described `exclude` as a free-form map, which the provider rejects,
so every New brief draft on production fell back to "Othor AI isn't available right now". The
test model in the other tests doesn't check schemas, so this guard does, for each prompt's newest
version (the one in use)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PROMPTS = Path(__file__).resolve().parents[2] / "othor" / "ai" / "prompts"


def _newest() -> list[Path]:
    out = []
    for folder in sorted(p for p in PROMPTS.iterdir() if p.is_dir()):
        files = sorted(
            folder.glob("v*.output.json"),
            key=lambda f: int(re.match(r"v(\d+)", f.name).group(1)),  # type: ignore[union-attr]
        )
        if files:
            out.append(files[-1])
    return out


def _problems(node: Any, where: str) -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            if node.get("additionalProperties") is not False:
                out.append(f"{where}: an object must set additionalProperties: false")
            if not isinstance(node.get("properties"), dict):
                out.append(f"{where}: an object must list its properties")
        for k, v in node.items():
            out += _problems(v, f"{where}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += _problems(v, f"{where}[{i}]")
    return out


def test_every_prompt_in_use_has_a_schema_the_provider_accepts() -> None:
    files = _newest()
    assert files, "prompts with output schemas"
    problems = []
    for f in files:
        problems += _problems(json.loads(f.read_text()), f"{f.parent.name}/{f.name}")
    assert problems == [], problems


def test_brief_drafts_read_the_list_shape() -> None:
    from othor.briefs.drafts import DRAFT, EDIT, _by_breakdown

    assert DRAFT[1] == "v4" and EDIT[1] == "v3"  # M15.7c: weekly and monthly; v4: honest picks
    assert _by_breakdown([{"breakdown": "region", "values": ["South", "West"]}]) == {
        "region": ["South", "West"]
    }
    assert _by_breakdown({"region": ["Export"]}) == {"region": ["Export"]}  # v1's map still reads
    assert _by_breakdown(None) == {}
