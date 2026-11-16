# SPDX-License-Identifier: Apache-2.0
"""One type scale (owner, 12 Nov 2026): the app and its panels use the same sizes. The two
sign-in sizes (24 and 15 px, owner 10 Nov 2026) stay on the sign-up and company sign-in screens
(`.auth`) and the sidebar's nav items, the AI usage card among them (it reads as one, owner
14 Nov 2026); a new screen can't drift onto them unnoticed."""

from __future__ import annotations

import re
from pathlib import Path

STYLES = Path(__file__).resolve().parents[2] / "othor" / "web" / "styles"
ALLOWED = (".auth", ".nav-item", ".nav-usage")  # selectors that may use the sign-in sizes
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _uses(token: str) -> list[str]:
    css = re.sub(r"/\*.*?\*/", "", (STYLES / "components.css").read_text(), flags=re.S)
    bad = []
    for sel, body in RULE.findall(css):
        if f"var({token})" not in body:
            continue
        for one in (x.strip() for x in sel.split(",")):
            if not one.startswith(ALLOWED):
                bad.append(one)
    return bad


def test_the_sign_in_sizes_stay_on_sign_in() -> None:
    assert _uses("--text-24") == []
    assert _uses("--text-15") == []


def test_the_guard_sees_a_stray_use(tmp_path, monkeypatch) -> None:
    (tmp_path / "components.css").write_text(
        ".auth h1 { font-size: var(--text-24); }\n.x { font-size: var(--text-24); }"
    )
    import sys

    monkeypatch.setattr(sys.modules[__name__], "STYLES", tmp_path)
    assert _uses("--text-24") == [".x"]
