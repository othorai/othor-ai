# SPDX-License-Identifier: Apache-2.0
"""Trim the project to one edition (M20.4): what a Docker image or the core-only CI job builds.

    python scripts/edition.py core    # the open core: othor/ only
    python scripts/edition.py ee      # the open core + othor_ee/ (licence-gated)
    python scripts/edition.py hosted  # othor.ai: core + othor_ee/ + othor_site/ (no change)

It deletes the packages the edition doesn't ship and removes them from pyproject.toml: the
wheel's packages, the plug-in entry points, the import contracts that name them, the test paths
and mypy's list. Run it on a copy of the tree (the image's build stage, a CI checkout), never on
a working copy you care about. Plain text edits, so pyproject.toml keeps its comments.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = {"core": ("othor_ee", "othor_site"), "ee": ("othor_site",), "hosted": ()}


def trim(text: str, drop: tuple[str, ...]) -> str:
    for pkg in drop:
        quoted = f'"{pkg}"'
        # list entries: packages, root_packages, testpaths, mypy packages
        text = re.sub(rf',\s*"{pkg}(?:/tests)?"', "", text)
        text = re.sub(rf'"{pkg}(?:/tests)?",\s*', "", text)
        # its plug-in entry point
        text = re.sub(rf'^\w+\s*=\s*"{pkg}:plugin".*\n', "", text, flags=re.M)
        # the import contracts that name it
        blocks = re.split(r"(?=^\[\[tool\.importlinter\.contracts\]\])", text, flags=re.M)
        text = "".join(
            b
            for b in blocks
            if not (b.startswith("[[tool.importlinter.contracts]]") and quoted in b)
        )
        # its lint rules
        text = re.sub(rf'^"{pkg}/[^\n]*\n', "", text, flags=re.M)
        assert quoted not in text, f"{pkg} is still named in pyproject.toml"
    return text


def main() -> int:
    edition = sys.argv[1] if len(sys.argv) > 1 else ""
    if edition not in PRIVATE:
        print("usage: edition.py core|ee|hosted", file=sys.stderr)
        return 2
    drop = PRIVATE[edition]
    for pkg in drop:
        shutil.rmtree(ROOT / pkg, ignore_errors=True)
    py = ROOT / "pyproject.toml"
    py.write_text(trim(py.read_text(), drop))
    print(f"edition {edition}: removed {', '.join(drop) or 'nothing'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
