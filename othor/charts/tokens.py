# SPDX-License-Identifier: Apache-2.0
"""Design tokens for charts, read from the one source pages use: othor/web/styles/theme.css.

Charts (SVG for pages, PNG for email) and the stylesheet share these values; a colour changed in
theme.css changes both. Read once at import; values are returned exactly as written there.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

THEME = Path(__file__).resolve().parents[1] / "web" / "styles" / "theme.css"
_DECL = re.compile(r"^\s*--([a-z0-9-]+):\s*([^;]+);", re.M)


@cache
def tokens() -> dict[str, str]:
    """Every `--name: value` declared in theme.css (without the leading dashes)."""
    text = re.sub(r"/\*.*?\*/", "", THEME.read_text(), flags=re.S)
    return {k: v.strip() for k, v in _DECL.findall(text) if not k.endswith("*")}


def color(name: str) -> str:
    return tokens()[f"color-{name}"]
