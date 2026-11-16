# SPDX-License-Identifier: Apache-2.0
"""The built stylesheet, once per test session (scripts/build_css.py)."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def build_css_module():
    spec = importlib.util.spec_from_file_location("build_css", ROOT / "scripts" / "build_css.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_css"] = mod  # its dataclasses look their module up while it loads (M20.2)
    spec.loader.exec_module(mod)
    return mod


def selectors(css: str) -> set[str]:
    return {m.replace("\\", "") for m in re.findall(r"\.((?:[a-zA-Z0-9_-]|\\.)+)", css)}
