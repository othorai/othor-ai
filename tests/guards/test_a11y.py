# SPDX-License-Identifier: Apache-2.0
"""Accessibility guards (M11a): text colours reach WCAG AA, every shell has a skip link, and
keyboard focus is never hidden."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STYLES = ROOT / "othor" / "web" / "styles"
SHELLS = ROOT / "othor" / "web" / "templates" / "shells"

# (text token, background token): every pair the components put text on
TEXT_PAIRS = [
    ("ink", "white"),
    ("on-accent", "accent"),  # the cyan buttons (owner, 12 Nov 2026)
    ("on-accent", "accent-hover"),
    ("ink-2", "white"),
    ("muted", "white"),
    ("muted", "surface"),
    ("muted-2", "white"),
    ("muted-2", "surface"),
    ("accent-deep", "white"),
    ("accent-deep", "accent-tint"),
    ("neg-text", "white"),
    ("neg-text", "neg-tint"),
    ("neg-ink", "neg-tint"),
    ("warn-text", "warn-tint"),
    ("warn-ink", "warn-tint"),
    ("ok-text", "ok-tint"),
    ("ok-text", "white"),
    # the public website (M11d)
    ("site-grey", "white"),
    ("site-grey", "site-card"),
    ("site-head", "white"),
    ("accent-deep", "accent-tint-2"),
    ("accent-deep", "site-card"),
    ("site-violet-ink", "site-violet-tint"),
    ("site-on-dark", "site-panel"),
    ("site-faint", "site-night"),
]

# Headline text only (40 px and up): WCAG AA for large text is 3:1.
LARGE_TEXT_PAIRS = [("site-blue", "white")]


def _tokens() -> dict[str, str]:
    t = (STYLES / "theme.css").read_text()
    return dict(re.findall(r"--color-([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", t))


def _lum(h: str) -> float:
    def f(x: float) -> float:
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4

    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize(("fg", "bg"), TEXT_PAIRS)
def test_text_tokens_reach_aa(fg: str, bg: str) -> None:
    c = _tokens()
    assert contrast(c[fg], c[bg]) >= 4.5, f"{fg} on {bg}: {contrast(c[fg], c[bg]):.2f}"


@pytest.mark.parametrize(("fg", "bg"), LARGE_TEXT_PAIRS)
def test_large_text_tokens_reach_aa(fg: str, bg: str) -> None:
    c = _tokens()
    assert contrast(c[fg], c[bg]) >= 3.0, f"{fg} on {bg}: {contrast(c[fg], c[bg]):.2f}"


def test_data_colours_are_not_small_text() -> None:
    """neg and ok (data and status colours, 4.4:1) are for marks, large figures and borders;
    small text uses neg-text and ok-text."""
    css = (STYLES / "components.css").read_text()
    for rule in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        sel, body = rule
        if re.search(r"(?<![-\w])color: var\(--color-(neg|ok)\)", body):
            assert ".brief-tile .v" in sel or "dot" in sel, sel.strip()


def test_focus_is_never_hidden_for_the_keyboard() -> None:
    base = (STYLES / "base.css").read_text()
    assert ":focus { outline: none; }" not in base
    assert ":focus-visible {" in base


@pytest.mark.parametrize("shell", ["app", "auth", "onboarding", "shared"])
def test_every_shell_has_a_skip_link(shell: str) -> None:
    html = (SHELLS / f"{shell}.html").read_text()
    assert 'class="skip-link" href="#main"' in html
    assert 'id="main"' in html


def test_text_on_the_cyan_is_dark_everywhere() -> None:
    """#47: the side menu's avatar kept white initials on the cyan (1.9:1) after the buttons moved
    to dark text (12 Nov 2026). No rule pairs the cyan background with white text."""
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / "othor/web/styles/components.css").read_text()
    for rule in re.findall(r"[^{}]+\{[^{}]*\}", css):
        body = rule[rule.index("{") :]
        if "background: var(--color-accent)" in body:
            assert "color: var(--color-white)" not in body, rule.strip()[:120]
