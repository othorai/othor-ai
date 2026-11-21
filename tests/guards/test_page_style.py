# SPDX-License-Identifier: Apache-2.0
"""The page style (owner, 13 Nov 2026; docs/07 "The page style"): every app page starts with the
`page_title` row, so a new screen can't come in without it. The open brief is frame only, by
design (docs/07: "The open brief: frame only")."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "othor" / "web" / "templates"
# enterprise features' pages too (17 Nov 2026: Look back's first version came in boxed)
EE = sorted(p for p in (ROOT / "othor_ee").glob("*/templates") if p.is_dir())
FRAME_ONLY = {"brief/brief.html"}
# a dashboard's header (0.29, docs/design-targets) or a report's masthead is its title row
TITLE_ROWS = ("page_title(", "db_header(", "masthead")


def _pages(root: Path) -> list[Path]:
    return [
        p
        for p in sorted(root.rglob("*.html"))
        if '{% extends "shells/app.html" %}' in p.read_text()
    ]


def test_every_app_page_has_the_title_row() -> None:
    pages = [(root, p) for root in (TEMPLATES, *EE) for p in _pages(root)]
    assert pages
    missing = [
        str(p.relative_to(root))
        for root, p in pages
        if str(p.relative_to(root)) not in FRAME_ONLY
        and not any(t in p.read_text() for t in TITLE_ROWS)
    ]
    assert missing == [], missing


BOXED = re.compile(
    # a `section` class without `lines`, in markup or through el()
    r"""class="section(?![^"]*\blines\b)(?:\s[^"]*)?"|"""
    r"""el\(\s*['"](?:section|div)['"]\s*,\s*['"]section(?![^'"]*\blines\b)(?:\s[^'"]*)?['"]"""
)


def test_new_features_use_the_page_style_sections() -> None:
    """docs/07 "The page style": sections are `section lines` (a caps heading, rows with a rule,
    no box). The boxed `section` is the old look, still on pages not yet converted (the v0.11
    list); an enterprise feature's templates, new from 17 Nov 2026, never use it."""
    found = [
        f"{p.relative_to(ROOT)}:{n}"
        for root in EE
        for p in sorted(root.rglob("*.html"))
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if BOXED.search(line)
    ]
    assert found == [], found
