# SPDX-License-Identifier: Apache-2.0
"""`othor replay <run_id>` (docs/02): re-execute a run from its stored inputs and compare the
outputs byte for byte (canonical JSON). Writes nothing; a model's stored text is never
regenerated (from M8, a brief's prose rewrite sits outside replay; its issues are replayed)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from othor.engine.compute import compute
from othor.engine.context import EngineContext
from othor.engine.ledger import sha
from othor.engine.models import Run
from othor.engine.orchestrator import AHEAD, BRIEF_KINDS, load_inputs
from othor.engine.store import RunFiles, read_json
from othor.ontology.compiler.api import canonical
from othor.ontology.models import Spec


@dataclass
class Replay:
    number: int
    ok: bool
    output_hash: str | None
    replayed_hash: str
    diff: list[str] = field(default_factory=list)


def replay(ctx: EngineContext, run_id: uuid.UUID) -> Replay:
    """Inside the run's company scope; money in the run's currency, as the night wrote it."""
    from othor.core import fmt
    from othor.engine.orchestrator import run_currency

    with fmt.money_style(run_currency(ctx, run_id)):
        return _replay(ctx, run_id)


def _replay(ctx: EngineContext, run_id: uuid.UUID) -> Replay:
    with ctx.sf() as s:
        run = s.scalars(sa.select(Run).where(Run.id == run_id)).one()
        spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one()
        s.expunge_all()
    if run.output_hash is None:
        raise ValueError(f"run #{run.number:04d} hasn't sealed: nothing to replay")
    files = RunFiles(ctx.settings.runs_dir(run.company_id), run.id)
    raw = files.manifest.read_bytes()
    diff: list[str] = []
    if sha(raw) != run.input_hash:
        diff.append("inputs: the stored snapshot doesn't match the run's input hash")
    doc = json.loads(raw)
    inputs = load_inputs(files, spec.compiled, doc)
    out = compute(inputs)
    goals: list[Any] = []
    fdoc: dict[str, Any] = {"goals": goals}
    if run.kind != "manual":  # the forecast step, from the same outputs (M7)
        from othor.engine.forecast import store as fstore
        from othor.engine.ledger import last_sealed
        from othor.engine.orchestrator import (
            CONTEXT,
            brief_headlines,
            forecast_summary,
            prev_forecasts,
        )

        with ctx.sf() as s:
            before = last_sealed(s, before=run.number)
        first = before is None
        prev = (
            prev_forecasts(RunFiles(ctx.settings.runs_dir(run.company_id), before.id))
            if before
            else None
        )
        fdoc, ahead = fstore.tonight(
            spec.compiled,
            inputs.run_date,
            out.points,
            kind=run.kind,
            first=first,
            prev=prev,
            also=brief_headlines(
                read_json(files.inputs / CONTEXT) or {}, spec.compiled, out.points
            ),
        )
        out.doc["forecast"] = forecast_summary(fdoc)
        goals = fdoc["goals"]
        diff += _same_file(files.outputs / AHEAD, ahead, "forecasts ahead")
    if run.kind not in ("manual", "fixture"):  # neither runs Sweep
        from othor.engine.orchestrator import sweep_inputs
        from othor.engine.sweep import run_sweep

        extra = sweep_inputs(files, spec.compiled, doc)
        sw = run_sweep(
            spec.compiled,
            spec.hash,
            inputs.run_date,
            inputs.points,
            out.points,
            extra["probe_files"],
            {**extra["context"], "goals": goals},
        )
        out.doc["sweep"] = sw.doc
        if run.kind in BRIEF_KINDS:  # the brief issues (M8), from the same outputs
            from othor.briefs import step as brief_step

            bdoc = brief_step.make(
                spec.compiled,
                inputs.run_date,
                extra["context"],
                sw.findings,
                sw.watches,
                fdoc,  # the whole forecasts doc, as the run reads forecasts.json (M19.4)
                out.points,
                out.doc,
            )
            out.doc["briefs"] = brief_step.summary(bdoc)
            diff += _weather(
                ctx,
                run,
                spec.compiled,
                inputs.run_date,
                sw.findings,
                goals,
                out,
                ahead=ahead,
                watches=extra["context"].get("watches") or [],
                status=sw.watches,
                briefs=extra["context"].get("briefs") or [],
            )
    new = canonical(out.doc).encode()
    stored = files.outputs_json.read_bytes()
    if new != stored:
        diff += _diff(json.loads(stored), json.loads(new))
    h = sha(new)
    return Replay(run.number, not diff and h == run.output_hash, run.output_hash, h, diff)


def _weather(
    ctx: EngineContext,
    run: Run,
    spec: dict[str, Any],
    run_date: Any,
    findings: list[dict[str, Any]],
    goals: list[Any],
    out: Any,
    *,
    ahead: dict[str, Any] | None,
    watches: list[dict[str, Any]],
    status: list[dict[str, Any]],
    briefs: list[dict[str, Any]],
) -> list[str]:
    """The run's weather (v0.12) and its outlook, recomputed from the same outputs (and the
    previous brief run's sealed files), byte for byte against its stored `weather.json`; each
    brief's own (part 1c) against `weather_briefs.json`. A run sealed before either has none,
    and nothing to compare."""
    from othor.briefs import weather
    from othor.engine.orchestrator import previous_brief

    outputs = RunFiles(ctx.settings.runs_dir(run.company_id), run.id).outputs
    path, per_brief = outputs / weather.WEATHER, outputs / weather.BRIEFS
    if not path.exists() and not per_brief.exists():
        return []
    with ctx.sf() as s:
        prev = previous_brief(s, run.number)
        s.expunge_all()
    same: dict[str, Any] = {
        "kind": run.kind,
        "first_brief": prev is None,
        "findings": findings,
        "goals": goals,
        "outputs_doc": out.doc,
        "points": out.points,
        "ahead": ahead,
        "watches": watches,
        "watch_status": status,
        "previous": None
        if prev is None
        else weather.previous_of(RunFiles(ctx.settings.runs_dir(run.company_id), prev.id)),
    }
    diff = _same_file(path, weather.make(spec, run_date, **same), "weather")
    if per_brief.exists():
        doc = weather.for_briefs(spec, run_date, briefs, **same)
        diff += _same_file(per_brief, doc, "brief weather")
    return diff


def _same_file(path: Any, doc: Any, name: str) -> list[str]:
    """A document written by the run, recomputed: byte for byte against the stored file. A run
    sealed before the file existed has nothing to compare."""
    if not path.exists():
        return []
    if canonical(doc).encode() == path.read_bytes():
        return []
    return [f"{name}: " + d for d in _diff(json.loads(path.read_bytes()), doc)] or [name]


def _diff(a: Any, b: Any, path: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b)):
            out += _diff(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        return out
    if a != b:
        return [f"{path}: stored {canonical(a)[:120]} · replayed {canonical(b)[:120]}"]
    return []
