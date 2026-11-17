# SPDX-License-Identifier: Apache-2.0
"""The run's `brief` step (M8): one issue per active brief, from the run's own files only. The
run and replay call the same function, so replay reproduces every issue byte for byte. No
model: prose rewrites happen after seal, outside the run (`othor.briefs.rewrite`)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from othor.briefs import sections
from othor.briefs.assemble import assemble
from othor.engine.ledger import sha
from othor.engine.store import Point, RunFiles, read_json, read_points, with_run_inputs
from othor.ontology.compiler.api import canonical

BRIEFS = "briefs.json"


def stale_as_of(spec: dict[str, Any], coverage: dict[str, Any]) -> dict[str, dt.date]:
    """A metric whose source wasn't read tonight, with the last good read of that source."""
    sources = coverage.get("sources") or {}
    out: dict[str, dt.date] = {}
    for m in coverage.get("stale_metrics") or []:
        days = [
            dt.date.fromisoformat(str(sources[s]["last_good"]))
            for s in (spec.get("metrics", {}).get(m, {}).get("sources") or [])
            if s in sources and sources[s].get("status") != "read" and sources[s].get("last_good")
        ]
        if days:
            out[m] = min(days)
    return out


def make(
    spec: dict[str, Any],
    run_date: dt.date,
    context: dict[str, Any],
    findings: list[dict[str, Any]],
    watches: list[dict[str, Any]],
    forecast: dict[str, Any],
    points: list[Point],
    outputs_doc: dict[str, Any],
) -> dict[str, Any]:
    sweep = outputs_doc.get("sweep") or {}
    ind = {w["id"]: w for w in context.get("watches") or []}
    ws = [{**w, "indicator": (ind.get(w["id"]) or {}).get("indicator")} for w in watches]
    stale = stale_as_of(spec, outputs_doc.get("coverage") or {})
    issues = []
    for v in context.get("briefs") or []:
        issue = assemble(
            spec,
            v,
            run_date=run_date,
            findings=[dict(f) for f in findings],
            watches=ws,
            goals=forecast.get("goals") or [],
            record=(context.get("forecasts") or {}).get("of_record") or [],
            points=points,
            sweep_counts=sweep.get("counts") or {},
            tests=int(sweep.get("tests") or 0),
            stale_as_of=stale,
            forecast=forecast,
        )
        extra = sections.build_all(spec, v, run_date, findings, ws, points, context, outputs_doc)
        if extra:  # only then, so an issue without a plug-in section keeps its bytes
            issue["sections"] = extra
        issues.append({"brief_id": v["id"], "version": v["number"], "issue": issue})
    return {"issues": issues}


def from_files(files: RunFiles, spec: dict[str, Any], run_date: dt.date) -> dict[str, Any]:
    outputs_doc = read_json(files.outputs_json) or {}
    return make(
        spec,
        run_date,
        with_run_inputs(read_json(files.inputs / "context.json") or {}, files),
        read_json(files.outputs / "findings.json") or [],
        read_json(files.outputs / "watches.json") or [],
        read_json(files.outputs / "forecasts.json") or {},
        read_points(files.series),
        outputs_doc,
    )


def summary(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "issues": len(doc["issues"]),
        "quiet": sum(1 for i in doc["issues"] if i["issue"]["quiet"]),
        "hash": sha(canonical(doc).encode()),
    }


def preview(
    files: RunFiles, spec: dict[str, Any], run_date: dt.date, version: dict[str, Any]
) -> dict[str, Any]:
    """A brief version against a sealed run's own files: the same function the run calls, so
    Edit brief's "Today with this change" and New brief's "tomorrow's brief" are what the next
    run would make from the same data (4c1, 4d2–4d6)."""
    outputs_doc = read_json(files.outputs_json) or {}
    context = {
        **with_run_inputs(read_json(files.inputs / "context.json") or {}, files),
        "briefs": [version],
    }
    doc = make(
        spec,
        run_date,
        context,
        read_json(files.outputs / "findings.json") or [],
        read_json(files.outputs / "watches.json") or [],
        read_json(files.outputs / "forecasts.json") or {},
        read_points(files.series),
        outputs_doc,
    )
    return dict(doc["issues"][0]["issue"]) if doc["issues"] else {}
