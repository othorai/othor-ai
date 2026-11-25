# SPDX-License-Identifier: Apache-2.0
"""Every prompt folder is in exactly one kind of work (Any model, 0.37): a company can give each
kind its own model, so a prompt outside the list would silently run on the wrong one."""

from __future__ import annotations

from pathlib import Path

from othor.ai import kinds

ROOT = Path(__file__).resolve().parents[2]


def _folders() -> set[str]:
    roots = [ROOT / "othor" / "ai" / "prompts", *sorted(ROOT.glob("othor_ee/*/prompts"))]
    return {p.name for r in roots if r.is_dir() for p in r.iterdir() if p.is_dir()}


def test_every_prompt_folder_is_in_exactly_one_kind() -> None:
    listed = [p for k in kinds.KINDS for p in kinds.prompts_of(k)]
    assert len(listed) == len(set(listed)), "a prompt is in two kinds"
    folders = _folders() - {kinds.CHECK_PROMPT}
    assert folders - set(listed) == set(), "prompt folders without a kind"
    if (ROOT / "othor_ee").is_dir():  # the open core alone has no Enterprise prompts
        assert set(listed) - folders == set(), "kinds list a prompt that has no folder"
