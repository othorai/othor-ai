# SPDX-License-Identifier: Apache-2.0
"""The page style (owner, 13 Nov 2026; docs/07 "The page style"): every app page starts with the
`page_title` row, so a new screen can't come in without it. The open brief is frame only, by
design (docs/07: "The open brief: frame only")."""

from __future__ import annotations

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[2] / "othor" / "web" / "templates"
FRAME_ONLY = {"brief/brief.html"}


def test_every_app_page_has_the_title_row() -> None:
    pages = [
        p
        for p in sorted(TEMPLATES.rglob("*.html"))
        if '{% extends "shells/app.html" %}' in p.read_text()
    ]
    assert pages
    missing = [
        str(p.relative_to(TEMPLATES))
        for p in pages
        if str(p.relative_to(TEMPLATES)) not in FRAME_ONLY and "page_title(" not in p.read_text()
    ]
    assert missing == [], missing
