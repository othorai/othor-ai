# SPDX-License-Identifier: Apache-2.0
"""The `detect` step (M6a): detectors → FDR → filters → states → findings → watches → counts.
Pure: the run and replay both call `run_sweep` on the same inputs and get the same bytes.

Its inputs: the run's outputs (series), its raw inputs (each entity's daily activity), its probe
files, and its context: the previous detecting run's findings and watch statuses, and the watches
active when the run started. The context holds only what tonight's results depend on (no change
tags, no run numbers), so two runs on the same inputs read the same context.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from othor.engine import detect, findings, matching, watches
from othor.engine.detectors.base import Context, SeriesIndex
from othor.engine.probes import assemble
from othor.engine.store import Point


@dataclass
class SweepOut:
    results: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    watches: list[dict[str, Any]]
    doc: dict[str, Any]


def run_sweep(
    spec: dict[str, Any],
    spec_hash: str,
    run_date: dt.date,
    inputs: list[Point],
    outputs: list[Point],
    probe_files: dict[str, dict[str, Any]],
    context: dict[str, Any],
) -> SweepOut:
    from othor.engine.ledger import sha
    from othor.ontology.compiler.api import canonical

    probes = assemble(spec.get("probes") or {}, probe_files, inputs, outputs)
    record = context.get("forecasts") or {}
    ctx = Context(
        spec,
        spec_hash,
        run_date,
        SeriesIndex(outputs),
        probes,
        context.get("holds") or {},
        {**record, "goals": context.get("goals") or []},
        context.get("run_inputs") or {},
    )
    det = detect.run(ctx)
    fnd = findings.build(det.results, context.get("previous_findings", []), run_date)
    # a finding that reads a record Othor AI decided (Let Othor AI decide, v0.12) is at most
    # Assessed · Medium until the owner confirms the record (non-negotiable 6)
    decided = set(spec.get("decided_by_ai") or [])
    for f in fnd:
        if f.get("family") in decided:
            f["decided_by_ai"] = True
            if f.get("confidence") == "high":
                f["confidence"] = "medium"
    from othor.engine import finding_charts, sweep_text

    for f in fnd:  # words and chart from computed values only; replay covers both
        f["text"] = sweep_text.words(f, spec)
        if f["status"] in ("open", *findings.HELD):
            f["chart"] = finding_charts.build(f, ctx.series, spec, ctx.probes)
    ws = watches.evaluate(
        context.get("watches", []),
        ctx.series,
        det.results,
        {w["id"]: w for w in context.get("watch_status", [])},
        run_date,
    )
    counts = findings.counts(fnd, ws, det.counts()["suppressed"])
    results = [r.as_json() for r in det.results]
    doc = {
        "tests": det.counts()["tests"],
        "counts": counts,
        "below_materiality": det.counts()["below_materiality"],
        "coverage": [c.as_json() for c in det.coverage],
        "results_hash": sha(canonical(results).encode()),
        "findings_hash": sha(canonical(fnd).encode()),
        "watches": ws,
        "matches": matching.compute(spec, probe_files),
    }
    return SweepOut(results, fnd, ws, doc)


def carry_context(
    previous: list[dict[str, Any]],
    watch_rows: list[dict[str, Any]],
    status: list[dict[str, Any]],
    holds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The context for tonight from the previous detecting run's outputs, the watch table and
    the owner's snoozes and mutes active when the run started."""
    holds = holds or {}
    # a finding's own body, without what only describes last night (its tag and day count)
    drop = ("change", "days_open", "text", "chart")
    return {
        "previous_findings": sorted(
            ({k: v for k, v in f.items() if k not in drop} for f in previous),
            key=lambda f: f["fingerprint"],
        ),
        "watches": watch_rows,
        "watch_status": sorted(
            (
                {"id": w["id"], "status": w["status"], "breached_since": w.get("breached_since")}
                for w in status
            ),
            key=lambda w: w["id"],
        ),
        "holds": {
            "snoozes": sorted(
                (
                    {"fingerprint": s["fingerprint"], "until": s["until"]}
                    for s in holds.get("snoozes", [])
                ),
                key=lambda s: (s["fingerprint"], s["until"]),
            ),
            "mutes": sorted(
                ({"pattern": m["pattern"]} for m in holds.get("mutes", [])),
                key=lambda m: m["pattern"],
            ),
        },
    }
