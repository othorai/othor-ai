# SPDX-License-Identifier: Apache-2.0
"""Every module the image ships imports with the production dependencies alone (0.39, 25 Nov
2026: 0.37 imported PyYAML at runtime while it was only a dev dependency, and signed-in Home
failed in production with ModuleNotFoundError).

    UV_PROJECT_ENVIRONMENT=/tmp/prod-env uv sync --frozen --no-dev
    UV_PROJECT_ENVIRONMENT=/tmp/prod-env uv run --no-sync python scripts/check_runtime_imports.py

Imports every module under othor, othor_ee and othor_site (whichever are present: the core
edition has neither of the last two), leaving out tests, dev scripts and the Alembic migrations,
and fails on a ModuleNotFoundError for a package outside the repo. `make runtime-imports` does
both steps (CI's static job). Other import errors are reported too: a module that can't be imported can't ship.
"""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("othor", "othor_ee", "othor_site")
SKIP_PARTS = {"tests", "scripts", "migrations", "__pycache__"}  # migrations: run by Alembic


def modules() -> list[str]:
    out = []
    for pkg in PACKAGES:
        base = ROOT / pkg
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            rel = p.relative_to(ROOT).with_suffix("")
            if SKIP_PARTS & set(rel.parts):
                continue
            parts = list(rel.parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            out.append(".".join(parts))
    return out


def main() -> int:
    sys.path.insert(0, str(ROOT))
    names = modules()
    failed: list[str] = []
    for name in names:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as e:
            missing = (e.name or "").split(".")[0]
            if missing in PACKAGES:  # a repo package missing (the core edition): not a dependency
                continue
            failed.append(f"{name}: needs '{e.name}', not a production dependency")
        except Exception:  # any failure to import is a failure to ship
            failed.append(f"{name}: {traceback.format_exc(limit=1).strip().splitlines()[-1]}")
    for f in failed:
        print(f, file=sys.stderr)
    print(f"{len(names)} modules imported with production dependencies: {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
