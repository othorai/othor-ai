# SPDX-License-Identifier: Apache-2.0
"""One type scale (owner, 12 Nov 2026): the app and its panels use the same sizes. The two
sign-in sizes (24 and 15 px, owner 10 Nov 2026) stay on the sign-up and company sign-in screens
(`.auth`) and the sidebar's nav items, the AI usage card among them (it reads as one, owner
14 Nov 2026); a new screen can't drift onto them unnoticed."""

from __future__ import annotations

import re
from pathlib import Path

STYLES = Path(__file__).resolve().parents[2] / "othor" / "web" / "styles"
ALLOWED = (".auth", ".nav-item")  # selectors that may use the sign-in sizes
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


# v0.25 (M25.3, owner 18 Nov 2026): one scale by role, the Big Picture report's type. Every
# size, line height and letter spacing in the app's and the site's CSS is a token; the few
# exceptions are geometry, not type: 0 and 1 (a chart's box, a one-line display), a badge's px
# line height that centres its number, 0 spacing that undoes a caps label, and the tiny numbers
# inside the Intelligence ring (an SVG drawn at 8 px).
CSS = (
    STYLES / "components.css",
    STYLES / "base.css",
    STYLES.parents[2] / "othor_site" / "styles" / "components-site.css",
)
RAW = re.compile(r"(font-size|line-height|letter-spacing)\s*:\s*([^;}]+)")
OK_RAW = {("line-height", "0"), ("line-height", "1"), ("letter-spacing", "0")}
RING = (".intel-ring",)


def _raw(path: Path) -> list[str]:
    css = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
    bad = []
    for sel, body in RULE.findall(css):
        for prop, val in RAW.findall(body):
            v = val.strip()
            if "var(--" in v or (prop, v) in OK_RAW or v == "inherit":
                continue
            if prop == "line-height" and v.endswith("px"):
                continue  # a badge's height, centring its number
            if sel.strip().startswith(RING):
                continue
            bad.append(f"{path.name}: {sel.strip()[:60]} {{ {prop}: {v} }}")
    return bad


def test_every_type_value_is_a_token() -> None:
    # the site's CSS only when the website is in the tree (the open core has none)
    bad = [b for p in CSS if p.exists() for b in _raw(p)]
    assert bad == []


def test_the_old_px_names_are_gone() -> None:
    theme = (STYLES / "theme.css").read_text()
    for old in ("--text-11:", "--text-12:", "--text-13:", "--text-14:", "--text-16:"):
        assert old not in theme
