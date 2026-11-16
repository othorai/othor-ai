# SPDX-License-Identifier: Apache-2.0
"""Editions (M20.4): the core image ships only the open core; othor-ee adds the enterprise tree;
othor.ai's image keeps everything. Checked on pyproject.toml's text (scripts/edition.py)."""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _edition():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("edition", ROOT / "scripts" / "edition.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["edition"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_each_edition_names_exactly_its_packages() -> None:
    ed = _edition()
    text = (ROOT / "pyproject.toml").read_text()
    for edition, keep in (
        ("core", ["othor"]),
        ("ee", ["othor", "othor_ee"]),
        ("hosted", ["othor", "othor_site", "othor_ee"]),
    ):
        # in a tree already trimmed (CI's core-alone job, the public repo) only the editions
        # whose packages are here can be checked
        if not all((ROOT / pkg).is_dir() for pkg in keep):
            continue
        doc = tomllib.loads(ed.trim(text, ed.PRIVATE[edition]))
        assert doc["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == keep, edition
        eps = doc["project"].get("entry-points", {}).get("othor.plugins", {})
        assert sorted(v.split(":")[0] for v in eps.values()) == sorted(
            k for k in keep if k != "othor"
        )
        assert doc["tool"]["importlinter"]["root_packages"] == keep
        for c in doc["tool"]["importlinter"]["contracts"]:
            tops = {m.split(".")[0] for m in c.get("forbidden_modules", [])}
            assert tops <= {*keep, "tests"}, c
        assert all(
            not p.startswith(("othor_site", "othor_ee")) or p.split("/")[0] in keep
            for p in doc["tool"]["pytest"]["ini_options"]["testpaths"]
        )


def test_open_images_default_to_one_company() -> None:
    from othor.core.config import Settings

    assert Settings(edition="core").tenancy == "single"
    assert Settings(edition="ee").tenancy == "single"
    assert Settings(edition="hosted").tenancy == "multi"
    assert Settings(edition="dev").tenancy == "multi"
    # an explicit choice wins (an ee install with the multi_company licence)
    assert Settings(edition="ee", tenancy="multi").tenancy == "multi"
