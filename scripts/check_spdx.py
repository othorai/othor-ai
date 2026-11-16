# SPDX-License-Identifier: Apache-2.0
"""Every source file says its licence in its first lines (M20.5, the public repository).

    python scripts/check_spdx.py          # fail on a file without its SPDX line (CI)
    python scripts/check_spdx.py --fix    # add the missing lines

The open core (`othor/`, `tests/`, `scripts/`, the self-host files) is Apache-2.0; `othor_ee/`
is LicenseRef-Othor-Enterprise. Vendored files keep their own licence and are listed in
docs/licences/assets.yml; data, images, fonts, goldens and Markdown carry no header.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = "SPDX-License-Identifier:"
APACHE = "Apache-2.0"
EE = "LicenseRef-Othor-Enterprise"

# where the check looks (the public tree's source; othor_site is private and never exported)
SCOPE = ("othor/", "othor_ee/", "tests/", "scripts/", "deploy/selfhost/")
TOP = ("Dockerfile", "Makefile", "compose.selfhost.yml", "docker-compose.yml")
VENDORED = ("htmx.min.js", "alpine-csp.min.js")
SKIP_PARTS = ("/golden/", "/__pycache__/")


def licence_of(path: str) -> str:
    return EE if path.startswith("othor_ee/") else APACHE


def comment(path: str, licence: str) -> str | None:
    """The header line for this kind of file, or None when it carries none."""
    name = path.rsplit("/", 1)[-1]
    if name in VENDORED or any(p in f"/{path}" for p in SKIP_PARTS):
        return None
    if name in ("Dockerfile", "Makefile", "Caddyfile", "Caddyfile.internal") or name.endswith(
        (".py", ".sh", ".yml", ".yaml")
    ):
        return f"# {TAG} {licence}"
    if path.startswith("othor/web/static/") and name.endswith(".html"):
        return f"<!-- {TAG} {licence} -->"  # served as is, not through Jinja
    if name.endswith(".html"):
        # a Jinja comment that strips the whitespace after it: the page renders exactly as before
        return f"{{#- {TAG} {licence} -#}}"
    if name.endswith((".css", ".js")):
        return f"/* {TAG} {licence} */"
    return None


def files() -> list[str]:
    out = subprocess.run(  # noqa: S603
        ["git", "ls-files", *SCOPE, *TOP],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return sorted(set(out))


def has_tag(text: str) -> bool:
    return TAG in "\n".join(text.splitlines()[:3])


def add(text: str, line: str) -> str:
    if text.lower().startswith("<!doctype"):
        first, _, rest = text.partition("\n")
        return f"{first}\n{line}\n{rest}"
    if text.startswith("#!"):
        first, _, rest = text.partition("\n")
        return f"{first}\n{line}\n{rest}"
    return f"{line}\n{text}"


def main(argv: list[str]) -> int:
    fix = "--fix" in argv
    missing: list[str] = []
    for path in files():
        line = comment(path, licence_of(path))
        if line is None or not (ROOT / path).is_file():
            continue
        text = (ROOT / path).read_text(encoding="utf-8")
        if has_tag(text):
            continue
        if fix:
            (ROOT / path).write_text(add(text, line), encoding="utf-8")
        missing.append(path)
    if missing and not fix:
        print(f"{len(missing)} files without an SPDX line (run with --fix):", file=sys.stderr)
        for p in missing[:30]:
            print(f"  {p}", file=sys.stderr)
        return 1
    if fix:
        print(f"added an SPDX line to {len(missing)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
