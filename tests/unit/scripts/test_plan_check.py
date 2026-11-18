# SPDX-License-Identifier: Apache-2.0
"""The plan check (owner, 18 Nov 2026): an unticked item that isn't deferred with the owner's
OK stops the release; ticked and deferred ones pass; release items may wait until the end."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("plan_check", ROOT / "scripts" / "plan_check.py")
assert spec and spec.loader
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)

PLAN = """# v0.x

## Checklist

- [x] 1. Built
- [ ] 2. Not built yet
- [ ] 3. Cut for now, deferred (owner OK, 18 Nov 2026)
- [ ] 4. Previews to the Desktop
- [ ] 5. Release 0.x: version, CHANGELOG,
      deploy

## Details
- [ ] not in the checklist
"""


def test_open_items_block_and_deferred_ones_pass() -> None:
    left, total = pc.open_items(PLAN)
    assert total == 5
    assert left == [
        "2. Not built yet",
        "4. Previews to the Desktop",
        "5. Release 0.x: version, CHANGELOG, deploy",
    ]
    left, _ = pc.open_items(PLAN, before_release=True)
    assert left == ["2. Not built yet"]


def test_a_plan_without_a_checklist_is_refused(tmp_path: Path) -> None:
    p = tmp_path / "plan.md"
    p.write_text("# no list\n")
    assert pc.main([str(p)]) == 1
    p.write_text(PLAN.replace("- [ ] 2.", "- [x] 2."))
    assert pc.main([str(p), "--before-release"]) == 0
