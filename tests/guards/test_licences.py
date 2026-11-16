# SPDX-License-Identifier: Apache-2.0
"""The licence check (decided 5 Nov 2026): the image's packages and assets pass today, and the
check fails on what it must refuse."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from check_licences import ASSETS, EXCEPTIONS, Package, load_yaml, problems  # noqa: E402

EXC = load_yaml(EXCEPTIONS)


def _pkgs(*extra: Package) -> list[Package]:
    base = [
        Package("psycopg", "3.3.7", "LGPL-3.0-only", []),
        Package("certifi", "2026.10.23", "MPL-2.0", []),
    ]
    base.append(Package("psycopg-binary", "3.3.7", "LGPL-3.0-only", []))
    # WeasyPrint's hyphenation, offered under three licences, taken under MPL-1.1 (M15.7)
    base.append(Package("pyphen", "0.18.1", "GPL-2.0-or-later OR LGPL-2.1-or-later OR MPL-1.1", []))
    return [*base, *extra]


def test_the_image_passes() -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_licences.py")],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr


def test_gpl_and_missing_licences_fail_with_the_package_named() -> None:
    bad = problems(
        _pkgs(
            Package("diptest", "0.12.0", "GPL-2.0-or-later", []),
            Package("mystery", "1.0", None, []),
        ),
        EXC,
        [],
    )
    assert "diptest 0.12.0: GPL-2.0-or-later is never allowed" in bad
    assert "mystery 1.0: no licence found" in bad


def test_an_exception_covers_its_package_version_and_licence_only() -> None:
    assert problems(_pkgs(), EXC, []) == []
    moved = [
        p if p.name != "psycopg" else Package("psycopg", "4.0.0", "LGPL-3.0-only", [])
        for p in _pkgs()
    ]
    assert any("outside its reviewed exception" in x for x in problems(moved, EXC, []))
    relicensed = [
        p if p.name != "certifi" else Package("certifi", "2027.1.1", "GPL-3.0-only", [])
        for p in _pkgs()
    ]
    assert any("differs from the reviewed MPL-2.0" in x for x in problems(relicensed, EXC, []))
    other = _pkgs(Package("some-lgpl", "1.0", "LGPL-3.0-only", []))
    assert "some-lgpl 1.0: LGPL-3.0-only isn't on the allowed list" in problems(other, EXC, [])


def test_ofl_only_for_fonts_and_assets() -> None:
    assert problems(_pkgs(), EXC, load_yaml(ASSETS)) == []
    assert problems(_pkgs(Package("fontish", "1.0", "OFL-1.1", [])), EXC, []) == [
        "fontish 1.0: OFL-1.1 isn't on the allowed list"
    ]
    tool = [{"name": "x", "kind": "tool", "licence": "OFL-1.1"}]
    assert problems(_pkgs(), EXC, tool) == ["asset x: OFL-1.1 isn't allowed for a tool"]


def test_permissive_expressions_pass() -> None:
    ok = _pkgs(
        Package("numpy", "2", "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0", []),
        Package("crypto", "1", "Apache-2.0 OR BSD-3-Clause", []),
        Package("dual", "1", "MIT OR GPL-3.0-only", []),  # we take it under MIT
        Package("gplonly", "1", "GPL-3.0-only AND MIT", []),
    )
    assert problems(ok, EXC, []) == ["gplonly 1: GPL-3.0-only AND MIT is never allowed"]
