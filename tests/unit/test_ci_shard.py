# SPDX-License-Identifier: Apache-2.0
"""The web tests split into four CI jobs (scripts/ci_shard.py): every test file runs in exactly
one of them, and a new file isn't left out."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ci_shard", ROOT / "scripts" / "ci_shard.py")
assert spec and spec.loader
ci_shard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_shard)


def test_every_web_test_file_runs_once() -> None:
    files = [f for n in range(1, ci_shard.SHARDS + 1) for f in ci_shard.shard(n)]
    every = sorted(f"tests/web/{p.name}" for p in (ROOT / "tests" / "web").glob("test_*.py"))
    assert sorted(files) == every
    assert len(files) == len(set(files))
    assert all(ci_shard.shard(n) for n in range(1, ci_shard.SHARDS + 1))


def test_a_named_file_that_is_gone_is_skipped() -> None:
    named = {f for files in ci_shard.NAMED.values() for f in files}
    for f in named:
        assert (ROOT / "tests" / "web" / f).exists(), f"{f} is named in ci_shard.py but gone"
