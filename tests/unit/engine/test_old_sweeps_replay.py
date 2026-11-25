# SPDX-License-Identifier: Apache-2.0
"""Runs made by older code replay with today's code (non-negotiable 2, #138). Each fixture in
tests/fixtures/old_sweeps was written by an older commit's code on the same small world
(`world.py`, made with `make.py`):

- changepoint1_rising1.json: 12a258a8^ (before 24 Nov: a non-₹ step has no money; the spread's
  start is the search's first week)
- changepoint2_rising1.json: 65097ec1 (0.37.0: the linked ₹ in full; the old start)
- changepoint2_rising2.json: 264bf041 (rising variance v2, before #138)

Their contexts named no versions, so replay reads them from the stored results
(`sweep.stored_versions`), and today's code must give the same bytes."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from othor.engine.sweep import current_versions, stored_versions
from othor.ontology.compiler.api import canonical

HERE = Path(__file__).resolve().parents[2] / "fixtures" / "old_sweeps"
_spec = importlib.util.spec_from_file_location("old_sweeps_world", HERE / "world.py")
assert _spec is not None and _spec.loader is not None
world = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(world)

MADE = {
    "changepoint1_rising1": {"changepoint": "1", "rising_variance": "1"},
    "changepoint2_rising1": {"changepoint": "2", "rising_variance": "1"},
    "changepoint2_rising2": {"changepoint": "2", "rising_variance": "2"},
}


@pytest.mark.parametrize("name", sorted(MADE))
def test_a_sweep_made_by_older_code_replays_byte_for_byte(name):
    stored = (HERE / f"{name}.json").read_text()
    versions = stored_versions(json.loads(stored)["results"])
    assert versions == MADE[name]
    assert canonical(world.sweep({"detector_versions": versions})) + "\n" == stored
    # with today's versions it would not: the bump is real, and replay must not use them
    assert canonical(world.sweep({})) + "\n" != stored


def test_a_new_run_names_its_versions_and_runs_them():
    now = world.sweep({"detector_versions": current_versions()})
    assert now == world.sweep({})
    assert stored_versions(now["results"]) == current_versions()


def test_stored_versions_reads_only_the_kept_detectors():
    rs = [
        {"detector": "changepoint", "version": "2"},
        {"detector": "changepoint", "version": "2"},
        {"detector": "net_hiding_gross", "version": "1"},
    ]
    assert stored_versions(rs) == {"changepoint": "2"}  # rising variance: none stored, current


def test_a_new_run_carries_both_rules_and_an_old_one_neither():
    """0.39: a run made tonight names its detector versions (#138) and the bar to stay open
    (#142); the two live side by side in one context, and a run sealed before both (neither key)
    still replays byte for byte (the parametrised test above)."""
    both = {"detector_versions": current_versions(), "fdr_keep": {"q": "0.05"}}
    once, again = world.sweep(dict(both)), world.sweep(dict(both))
    assert canonical(once) == canonical(again)
    assert stored_versions(once["results"]) == current_versions()
    tested = [r for r in once["results"] if (r.get("params") or {}).get("fdr_q")]
    assert tested and all(r["params"].get("keep_q") == "0.05" for r in tested)
    neither = world.sweep({})
    assert all("keep_q" not in (r.get("params") or {}) for r in neither["results"])
