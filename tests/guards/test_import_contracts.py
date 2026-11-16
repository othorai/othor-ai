# SPDX-License-Identifier: Apache-2.0
"""The engine and scheduler never import othor.ai (non-negotiable 4, docs/06 guard 5)."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_import_contracts_hold() -> None:
    exe = Path(sys.executable).with_name("lint-imports")
    out = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "Engine, scheduler and charts never import the AI module" in out.stdout


def test_contract_catches_a_violation(tmp_path: Path) -> None:
    """Prove the contract is live: a temporary engine module importing othor.ai must fail."""
    bad = ROOT / "othor" / "engine" / "_contract_probe.py"
    bad.write_text("import othor.ai  # noqa: F401\n")
    try:
        exe = Path(sys.executable).with_name("lint-imports")
        out = subprocess.run(
            [str(exe), "--no-cache"], cwd=ROOT, capture_output=True, text=True, check=False
        )
        assert out.returncode != 0
        assert "othor.engine._contract_probe -> othor.ai" in out.stdout
    finally:
        bad.unlink()


def test_a_run_loads_no_model_code() -> None:
    """At runtime too: importing everything a run uses (orchestrator, replay, scheduler) never
    loads othor.ai or the job handlers that use it (non-negotiable 4)."""
    code = (
        "import sys, othor.engine.orchestrator, othor.engine.replay, othor.engine.scheduler;"
        "bad = sorted(m for m in sys.modules if m.startswith(('othor.ai', 'othor.setup', "
        "'othor.jobs.runner', 'othor.jobs.label_job')));"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
