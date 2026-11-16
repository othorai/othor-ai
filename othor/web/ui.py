# SPDX-License-Identifier: Apache-2.0
"""The component vocabulary and the page-layout allowlist (docs/plans/UI-foundation.md).

Pages never write visual classes. They call macros; a macro asks `ui(kind, layout)` for its
class string, which refuses
- a visual class that isn't a component (defined in othor/web/styles/components.css), and
- a layout class outside the allowlist below (display, flex, grid, gap, margin, padding, size,
  position, order, overflow, with breakpoint prefixes).
scripts/check_template_classes.py uses the same allowlist for class="…" in page templates.
"""

from __future__ import annotations

import re
import time
from functools import cache
from pathlib import Path

STYLES = Path(__file__).resolve().parent / "styles"

_NUM = r"(?:\d+(?:\.\d+)?|px|full|screen|auto|fit|min|max|dvh|svh|lvh|\d+/\d+)"
LAYOUT = re.compile(
    r"^-?(?:"
    r"block|inline-block|inline|flex|inline-flex|grid|inline-grid|hidden|contents|flow-root"
    r"|flex-(?:row|col)(?:-reverse)?|flex-(?:wrap|nowrap|wrap-reverse)|flex-(?:1|auto|none|initial)"
    r"|grow(?:-0)?|shrink(?:-0)?|basis-" + _NUM + r"|(?:items|justify|self|content|place-items"
    r"|place-content|place-self|justify-items|justify-self)"
    r"-(?:start|end|center|between|around|evenly|stretch|baseline|normal|auto)"
    r"|grid-cols-(?:\d+|none|subgrid)|col-span-(?:\d+|full)|col-(?:start|end)-\d+"
    r"|grid-rows-(?:\d+|none|subgrid)|row-span-(?:\d+|full)|row-(?:start|end)-\d+"
    r"|grid-flow-(?:row|col|dense|row-dense|col-dense)|auto-(?:cols|rows)-(?:auto|min|max|fr)"
    r"|gap(?:-[xy])?-"
    + _NUM
    + r"|space-[xy]-"
    + _NUM
    + r"|[mp][trblxyse]?-"
    + _NUM
    + r"|(?:min-|max-)?[wh]-"
    + _NUM
    + r"|size-"
    + _NUM
    + r"|static|fixed|absolute|relative|sticky"
    r"|inset(?:-[xy])?-"
    + _NUM
    + r"|(?:top|right|bottom|left|start|end)-"
    + _NUM
    + r"|z-(?:\d+|auto)|order-(?:\d+|first|last|none)"
    r"|overflow(?:-[xy])?-(?:auto|hidden|visible|scroll|clip)"
    r"|js-[a-z0-9-]+"
    r")$"
)
PREFIX = re.compile(r"^(?:(?:sm|md|lg|xl|2xl|max-sm|max-md|max-lg|max-xl):)+")


def is_layout(token: str) -> bool:
    return "[" not in token and bool(LAYOUT.match(PREFIX.sub("", token)))


@cache
def _classes(path: Path, mtime: float) -> frozenset[str]:
    text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
    return frozenset(re.findall(r"\.([a-z][a-z0-9-]*)", text))


_KNOWN: tuple[float, frozenset[str]] | None = None
RECHECK_S = 1.0  # M15.1: listing and stat-ing the styles on every el() call cost ~60 µs each


def components() -> frozenset[str]:
    """Every class the component styles define. Looked up again at most once a second, so an
    edited stylesheet is picked up while developing and a page's hundreds of el() calls don't
    each list the folder."""
    global _KNOWN
    now = time.monotonic()
    if _KNOWN is not None and now - _KNOWN[0] < RECHECK_S:
        return _KNOWN[1]
    from othor.core import plugins

    out: set[str] = set()
    folders = [STYLES, *(d for p in plugins.loaded() for d in p.styles)]  # M20.2: plug-ins' too
    for p in sorted(f for d in folders for f in d.glob("components*.css")):
        out |= _classes(p, p.stat().st_mtime)
    _KNOWN = (now, frozenset(out))
    return _KNOWN[1]


def ui(kind: str = "", layout: str = "") -> str:
    """The class string for a component variant plus layout utilities; refuses anything else."""
    known = components()
    for tok in kind.split():
        if tok not in known and not tok.startswith("is-"):
            raise ValueError(f"not a component class: {tok!r} (add it to components.css)")
    for tok in layout.split():
        if not is_layout(tok):
            raise ValueError(f"not a layout utility: {tok!r} (visual classes come from components)")
    return " ".join(p for p in (kind.strip(), layout.strip()) if p)
