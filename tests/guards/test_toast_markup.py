# SPDX-License-Identifier: Apache-2.0
"""Calm toasts (owner, 19-20 Nov 2026): every toast is drawn by one macro (components/blocks.html
`toast`), so its look, its place over the top bar and its quiet actions can't drift. A template
that writes toast markup by hand fails here; othor.js builds the Copied and offline ones with the
same classes."""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[2] / "othor" / "web" / "templates"
ALLOWED = {"components/blocks.html"}  # the macro itself
HAND = re.compile(r"""class="toast(?:\s[^"]*)?"|el\(\s*["']div["']\s*,\s*["']toast["']""")


def test_every_toast_comes_from_the_macro() -> None:
    bad = []
    for p in sorted(TEMPLATES.rglob("*.html")):
        rel = p.relative_to(TEMPLATES).as_posix()
        if rel in ALLOWED or rel.startswith("components_demo/"):
            continue
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if HAND.search(line):
                bad.append(f"{rel}:{n}")
    assert bad == [], "toast markup written by hand (use the toast macro):\n" + "\n".join(bad)
