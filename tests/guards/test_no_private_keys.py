# SPDX-License-Identifier: Apache-2.0
"""No private key is ever committed (M20.3: the licence-signing key lives outside the repository;
tools/licence/keygen.py refuses to write it inside)."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKERS = (
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "BEGIN OPENSSH PRIVATE KEY",
    "BEGIN EC PRIVATE KEY",
)


def test_no_tracked_file_holds_a_private_key() -> None:
    files = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 - git on PATH, a fixed command
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.split("\0")
    found = []
    for f in files:
        p = ROOT / f
        if not f or not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        found += [f for m in MARKERS if m in text and f != "tests/guards/test_no_private_keys.py"]
    assert not found, f"a private key is committed: {sorted(set(found))}"
