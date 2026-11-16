# SPDX-License-Identifier: Apache-2.0
"""The web tests in four CI jobs that run side by side (owner, 11 Nov 2026: CI took an hour,
the web job alone 49-59 minutes, close to its 60-minute timeout).

    python scripts/ci_shard.py 2      # prints shard 2's test files, for `pytest $(...)`

Files are grouped by weight (rebalanced 12 Nov; see NAMED). A file
not named here, such as a new one, runs in the last shard, so nothing is ever left out.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "tests" / "web"
SHARDS = 4

# 12 Nov 2026: shard 3 took 39 min (4-14 for the others) because the four heaviest files, the
# ones that build a sample-company night per test, were all in it. One heavy file per shard now;
# every job prints its 30 slowest tests (--durations) for the next rebalance.
NAMED: dict[int, tuple[str, ...]] = {
    1: (
        "test_brief_web.py",
        "test_auth_flow.py",
        "test_account_menu.py",
        "test_home_nights.py",
        "test_ontology_nights.py",
        "test_source_matches_web.py",
        "test_page_budget.py",
    ),
    2: (
        "test_ontology_web.py",
        "test_invitations.py",
        "test_errors.py",
        "test_links_web.py",
        "test_reconnect_affects.py",
        "test_setup_web.py",
        "test_rendered_attributes.py",
        "test_metrics_checks.py",
    ),
    3: (
        "test_sweep_web.py",
        "test_board_states.py",
        "test_chat_web.py",
        "test_rerun.py",
        "test_startup.py",
    ),
}


def shard(n: int) -> list[str]:
    """Shard n's test files (1-based), as paths from the repo root."""
    if not 1 <= n <= SHARDS:
        raise ValueError(f"shard {n} is not 1-{SHARDS}")
    everything = sorted(p.name for p in WEB.glob("test_*.py"))
    named = {f for files in NAMED.values() for f in files}
    mine = NAMED.get(n) if n < SHARDS else [f for f in everything if f not in named]
    return [f"tests/web/{f}" for f in sorted(mine or ()) if f in everything]


if __name__ == "__main__":
    print(" ".join(shard(int(sys.argv[1]))))
