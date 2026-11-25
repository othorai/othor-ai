# SPDX-License-Identifier: Apache-2.0
"""The run's `brief` step (M8): one issue per active brief, from the run's own files only. The
run and replay call the same function, so replay reproduces every issue byte for byte. No
model: prose rewrites happen after seal, outside the run (`othor.briefs.rewrite`)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from othor.briefs import scope, sections, since_week
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
    reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Every active brief's issue. `reference`: the default brief's version, for a preview of
    one brief (its count goes in a focused brief's quiet line)."""
    sweep = outputs_doc.get("sweep") or {}
    ind = {w["id"]: w for w in context.get("watches") or []}
    ws = [
        {
            **w,
            "indicator": (ind.get(w["id"]) or {}).get("indicator"),
            "from_finding": (ind.get(w["id"]) or {}).get("from_finding"),
        }
        for w in watches
    ]
    stale = stale_as_of(spec, outputs_doc.get("coverage") or {})
    issues = []

    def one(v: dict[str, Any]) -> dict[str, Any]:
        return assemble(
            spec,
            v,
            run_date=run_date,
            findings=[dict(f) for f in findings],
            watches=ws,
            goals=forecast.get("goals_shown") or forecast.get("goals") or [],
            record=(context.get("forecasts") or {}).get("of_record") or [],
            points=points,
            sweep_counts=sweep.get("counts") or {},
            tests=int(sweep.get("tests") or 0),
            stale_as_of=stale,
            forecast=forecast,
        )

    briefs = list(context.get("briefs") or [])
    made = [one(v) for v in briefs]
    by_default = next((i for v, i in zip(briefs, made, strict=True) if v.get("default")), None)
    for v, issue in zip(briefs, made, strict=True):
        if scope.focus(spec, v) is not None and not issue.get("items"):
            # a focused brief with nothing new tonight says where the rest is (Option A, owner
            # 20 Nov 2026): the default brief's count tonight, else the same ranking unscoped
            ref = by_default if by_default is not None else one(reference or _unscoped(spec))
            issue["focus_note"] = focus_note(str(v.get("name") or ""), ref)
    for v, issue in zip(briefs, made, strict=True):
        # 0.28.5: "Since last week", from tonight's findings and the stored nights; only when
        # there's something to say, so an issue without it keeps its bytes
        week = since_week.build(
            spec,
            v,
            run_date,
            [dict(f) for f in findings],
            context.get("history"),
            [i["fingerprint"] for i in issue.get("items") or []],
        )
        if week:
            issue["week"] = week
        extra = sections.build_all(spec, v, run_date, findings, ws, points, context, outputs_doc)
        if extra:  # only then, so an issue without a plug-in section keeps its bytes
            issue["sections"] = extra
        issues.append({"brief_id": v["id"], "version": v["number"], "issue": issue})
    return {"issues": issues}


FOCUS_NOTE = "Nothing new on {name}"


def _unscoped(spec: dict[str, Any]) -> dict[str, Any]:
    """The default brief as the first run makes it, with no picked numbers: the whole company's
    ranking, for a run whose briefs don't say which one is the default."""
    from othor.briefs.assemble import default_version

    return {**default_version(spec), "id": "default", "number": 0, "headline": []}


def focus_note(name: str, ref: dict[str, Any]) -> str:
    """ "Nothing new on Dispatch · Leadership has 3 items": the count is the reference issue's
    own items, from code."""
    from othor.core import fmt

    n = len(ref.get("items") or [])
    line = FOCUS_NOTE.format(name=name)
    if n:
        other = str((ref.get("brief") or {}).get("name") or "")
        line += f" · {other} has {fmt.num(n)} item{'s' if n != 1 else ''}"
    return line


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
    files: RunFiles,
    spec: dict[str, Any],
    run_date: dt.date,
    version: dict[str, Any],
    points: list[Point] | None = None,
) -> dict[str, Any]:
    """A brief version against a sealed run's own files: the same function the run calls, so
    Edit brief's "Today with this change" and New brief's "tomorrow's brief" are what the next
    run would make from the same data (4c1, 4d2–4d6). `points`: the run's points as a page
    already holds them (`run_cache.points`, the same points in the same order); else read."""
    outputs_doc = read_json(files.outputs_json) or {}
    night = with_run_inputs(read_json(files.inputs / "context.json") or {}, files)
    ref = next((b for b in night.get("briefs") or [] if b.get("default")), None)
    context = {**night, "briefs": [version]}
    doc = make(
        spec,
        run_date,
        context,
        read_json(files.outputs / "findings.json") or [],
        read_json(files.outputs / "watches.json") or [],
        read_json(files.outputs / "forecasts.json") or {},
        read_points(files.series) if points is None else points,
        outputs_doc,
        reference=None if version.get("default") else ref,
    )
    return dict(doc["issues"][0]["issue"]) if doc["issues"] else {}
