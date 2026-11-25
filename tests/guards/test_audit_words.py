# SPDX-License-Identifier: Apache-2.0
"""0.35: every event action in the code has a plain sentence in the Audit log's words, so a new
action can't slip in unnamed. Finds `action="…"` literals, an action passed to the auth and
members helpers by position, and `… = "x.y"` constants used as actions, in othor/ and othor_ee/
(the open core checks what it has)."""

from __future__ import annotations

import re
from pathlib import Path

from othor.audit import words

ROOT = Path(__file__).resolve().parents[2]
ACTION = re.compile(r"""\baction\s*=\s*["']([a-z_]+\.[a-z_]+)["']""")
POSITIONAL = re.compile(
    r"""\b_?event\(\s*[^,()]+,\s*[^,()]+,\s*(?:[^,()]+,\s*)?["']([a-z_]+\.[a-z_]+)["']"""
)
CONSTANT = re.compile(r"""^[A-Z_]+\s*=\s*["']((?:member|auth|invitation)\.[a-z_]+)["']""", re.M)


def actions() -> dict[str, str]:
    found: dict[str, str] = {}
    for top in ("othor", "othor_ee"):
        base = ROOT / top
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if "tests" in p.relative_to(ROOT).parts:
                continue
            text = p.read_text()
            for rx in (ACTION, POSITIONAL, CONSTANT):
                for m in rx.finditer(text):
                    found.setdefault(m.group(1), str(p.relative_to(ROOT)))
    return found


def test_the_scan_finds_the_actions() -> None:
    got = actions()
    assert len(got) > 150
    assert {"auth.signed_in", "source.connected", "run.finished", "tool.called"} <= set(got)


def test_every_action_has_a_sentence() -> None:
    missing = sorted(f"{a} ({where})" for a, where in actions().items() if not words.named(a))
    assert missing == []
