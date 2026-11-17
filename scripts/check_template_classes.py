# SPDX-License-Identifier: Apache-2.0
"""Page templates use layout utilities and the shared macros only (docs/plans/UI-foundation.md).

    uv run python scripts/check_template_classes.py            # report every page template
    uv run python scripts/check_template_classes.py --strict   # exit 1 on any finding

Page templates: everything under othor/web/templates except components/, shells/,
components_demo/ and base.html (those define the look). In a page template:
- allowed classes: display, flex, grid, gap, margin, padding, width/height, position/inset,
  z, order, overflow, with responsive prefixes (sm: md: lg: xl: 2xl: max-*:), and js-* hooks
- never: colour, typography, border, radius, shadow, opacity, transition, arbitrary values
  ([...]), style="…", <style>; visual classes come from the macros in components/
- never: Alpine attributes (x-*, @event, :bound); they live in the macros (M12)

Only literal class tokens are checked; tokens produced inside {{ … }} come from macros or view
code. Findings print as file:line: token. CI runs --strict on the areas listed in STRICT.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from othor.web.ui import is_layout

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "othor" / "web" / "templates"
# the private website's templates (M20.2), checked the same way when it's in the tree
ROOTS = [t for t in (TEMPLATES, ROOT / "othor_site" / "templates") if t.exists()]
# enterprise features' pages too (Look back, 17 Nov 2026): the same rule in othor_ee/*/templates
ROOTS += sorted(p for p in (ROOT / "othor_ee").glob("*/templates") if p.is_dir())
COMPONENT_SCOPE = ("components", "shells", "components_demo")
# every page template is on the shared components (UI foundation, 4 Nov); CI fails on any finding
STRICT: tuple[str, ...] = ("",)

JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
CLASS_ATTR = re.compile(r'\bclass="([^"]*)"')
ALPINE_ATTR = re.compile(
    r"(?<=[\s<])((?:x-[a-z][\w.:-]*)|(?:@[a-z][\w.:-]*)|(?::[a-z][\w-]*))(?==)"
)


def page_templates() -> list[Path]:
    out = []
    for root in ROOTS:
        for p in sorted(root.rglob("*.html")):
            rel = p.relative_to(root)
            if rel.parts[0] in COMPONENT_SCOPE or rel.name == "base.html":
                continue
            out.append(p)
    return out


def allowed(token: str) -> bool:
    return is_layout(token)


def findings(path: Path) -> list[tuple[int, str]]:
    text = path.read_text()
    out: list[tuple[int, str]] = []
    for m in CLASS_ATTR.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        literal = JINJA.sub(" ", m.group(1))
        for tok in literal.split():
            if not allowed(tok):
                out.append((line, tok))
    for m in re.finditer(r"""\blayout=(["'])([^"']*)\1""", text):  # el(…, layout="…")
        line = text.count("\n", 0, m.start()) + 1
        out += [(line, tok) for tok in m.group(2).split() if not allowed(tok)]
    # Alpine (M12): x-*, @event and :bound attributes belong to the macros in components/
    for m in ALPINE_ATTR.finditer(JINJA.sub(lambda j: " " * len(j.group(0)), text)):
        out.append((text.count("\n", 0, m.start()) + 1, m.group(1)))
    for m in re.finditer(r'\bstyle="|<style\b', text):
        out.append((text.count("\n", 0, m.start()) + 1, m.group(0).strip('="')))
    return out


def strict_area(path: Path) -> bool:
    root = next(r for r in ROOTS if path.is_relative_to(r))
    rel = path.relative_to(root).as_posix()
    return any(a == "" or rel == a or rel.startswith(a.rstrip("/") + "/") for a in STRICT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="fail on any finding anywhere")
    args = ap.parse_args()
    bad = 0
    blocking = 0
    for p in page_templates():
        for line, tok in findings(p):
            rel = p.relative_to(ROOT)
            print(f"{rel}:{line}: {tok}")
            bad += 1
            blocking += strict_area(p) or args.strict
    print(f"{bad} finding(s); {blocking} blocking", file=sys.stderr)
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
