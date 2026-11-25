# SPDX-License-Identifier: Apache-2.0
"""The run orchestrator (docs/02 "The nightly run"): a list of steps, each one `run_step` row,
idempotent and resumable. Done steps are skipped; a step left running by a killed worker, or a
failed one, is re-run from scratch (it writes only its own files under `runs/<run_id>/`).

Steps, in order:
  read:<source>, materialise:<source>   every source in scope (stale when it can't be read)
  backfill:<source>                     first run; activation or a new definition (5 Nov 2026)
  carry:<source>                        a source-scoped manual run: the other sources' points
  derive                                inputs manifest → outputs (othor.engine.compute)
  questions                             Home questions from rule checks (not on manual runs)
  seal                                  hashes, ledger, the company's series table

Kinds: `nightly` (02:00), `dry` (the first run), `rerun` (Run again now, v0.11: the nightly
steps on demand, the day's issues as new versions), `manual` (Read now: series only) and
`fixture` (tests only).

M6–M8 add their steps between `derive` and `seal`. Never imports othor.ai.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core.events import SYSTEM_ACTOR, record_event
from othor.core.models import Company
from othor.engine import periods
from othor.engine.compute import Inputs, compute
from othor.engine.context import EngineContext
from othor.engine.ledger import last_sealed, run_hash, sha
from othor.engine.models import Run, RunStep, SeriesMeta
from othor.engine.sources import (
    RunInfo,
    SourceOutcome,
    backfill,
    carry,
    materialise,
    metrics_on,
    read,
)
from othor.engine.store import (
    Point,
    RunFiles,
    hash_points,
    publish_series,
    read_json,
    read_points,
    run_input_files,
    run_input_path,
    with_run_inputs,
    write_json,
    write_points,
)
from othor.ontology import service as ontology
from othor.ontology.compiler import compile_version
from othor.ontology.models import Spec
from othor.ontology.questions import raise_questions

log = logging.getLogger("othor.engine")
MANIFEST_FORMAT = 1
SOFT = "stale"  # a source step that couldn't read: the run goes on, its series carry over


class NoSpec(RuntimeError):
    """No confirmed Ontology yet: nothing to run."""


def _scope_source(spec: dict[str, Any], scope: dict[str, Any] | None) -> str | None:
    if not scope or not scope.get("source_id"):
        return None
    for name, meta in spec["sources"].items():
        if meta["id"] == scope["source_id"]:
            return str(name)
    raise NoSpec("the source isn't used by any confirmed metric")


def plan(spec: dict[str, Any], kind: str, scope_source: str | None, first: bool) -> list[str]:
    from othor.engine.probes import sources_with_probes

    names: list[str] = []
    for src in sorted(spec["sources"]):
        if scope_source and src != scope_source:
            names.append(f"carry:{src}")
            continue
        names += [f"read:{src}", f"materialise:{src}"]
        # the first run; later nightly runs too, for a snapshot metric that becomes active or
        # gets a new definition (the step finds which, and reads nothing when none is due)
        if (first or kind != "manual") and any(
            (m.get("backfill") or {}).get("possible") for _, m in metrics_on(spec, src)
        ):
            names.append(f"backfill:{src}")
    if kind == "fixture":  # earlier nights' snapshots for a test history; no Sweep
        names += ["fixture", "derive", "forecast", "seal"]
        return names
    if kind != "manual":  # Read now refreshes series only: Sweep stays on the nightly run
        names += [f"probes:{src}" for src in sources_with_probes(spec)]
    names.append("derive")
    if kind != "manual":
        names += ["forecast", "detect", "questions", "brief"]
    names.append("seal")
    return names


class FixtureRefused(RuntimeError):
    """A fixture run outside the test environment."""


def start_fixture(
    ctx: EngineContext,
    s: Session,
    company: Company,
    *,
    run_date: dt.date,
    points: dict[str, list[Point]],
    image_version: str,
) -> Run:
    """A run of kind `fixture` (test environment only): a normal read of the sources, plus the
    generator's earlier nightly snapshots (`reconstructed=false`) for a steady-state history.
    Its kind is in its run hash (marked in the ledger); replay re-derives it from its inputs."""
    if ctx.settings.env != "test":
        raise FixtureRefused("a fixture run can only be created in the test environment")
    run = start(
        s,
        company,
        kind="fixture",
        run_date=run_date,
        now=ctx.clock(),
        image_version=image_version,
        _fixture_ok=True,
    )
    files = RunFiles(ctx.settings.runs_dir(company.id), run.id)
    for source, pts in sorted(points.items()):
        if source not in run_spec_sources(s, run):
            raise FixtureRefused(f"{source} isn't a source of this run")
        write_points(files.inputs / f"fixture_{_safe(source)}.parquet", pts)
    return run


def run_spec_sources(s: Session, run: Run) -> list[str]:
    spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one()
    return sorted(spec.compiled["sources"])


def fixture_step(info: RunInfo) -> SourceOutcome:
    """Merge the injected snapshots into each source's back-fill points: an injected point
    replaces a reconstructed one for the same series and day; a day the run read itself wins."""
    added = 0
    for source in sorted(info.spec["sources"]):
        f = info.files.inputs / f"fixture_{_safe(source)}.parquet"
        injected = read_points(f)
        if not injected:
            continue
        read_now = {p.key() for p in read_points(info.files.source_points(source))}
        mine = {p.key(): p for p in injected if p.key() not in read_now}
        kept = [p for p in read_points(info.files.backfill_points(source)) if p.key() not in mine]
        merged = sorted([*kept, *mine.values()], key=Point.sort_key)
        write_points(info.files.backfill_points(source), merged)
        added += len(mine)
    return SourceOutcome(True, {"points": added})


def start(
    s: Session,
    company: Company,
    *,
    kind: str,
    run_date: dt.date,
    now: dt.datetime,
    image_version: str,
    scope: dict[str, Any] | None = None,
    job_id: uuid.UUID | None = None,
    _fixture_ok: bool = False,
) -> Run:
    """Create the run and its steps, inside the company's tenant scope."""
    if kind == "fixture" and not _fixture_ok:
        raise FixtureRefused("a fixture run is created by start_fixture, in tests only")
    if not ontology.current_number(s):
        raise NoSpec("no confirmed Ontology")
    spec = compile_version(s, company)
    scope_source = _scope_source(spec.compiled, scope)
    prev = last_sealed(s)
    number = (s.scalar(sa.select(sa.func.max(Run.number))) or 0) + 1
    run = Run(
        number=number,
        kind=kind,
        scope=scope,
        run_date=run_date,
        spec_id=spec.id,
        image_version=image_version,
        started_at=now,
        status="running",
        job_id=job_id,
        summary={},
    )
    s.add(run)
    s.flush()
    for i, name in enumerate(plan(spec.compiled, kind, scope_source, first=prev is None)):
        s.add(RunStep(run_id=run.id, name=name, ordinal=i, status="queued", detail={}))
    record_event(
        s,
        actor=SYSTEM_ACTOR,
        action="run.started",
        object_type="run",
        object_id=run.id,
        after={
            "number": number,
            "kind": kind,
            "run_date": run_date.isoformat(),
            "spec": spec.hash,
            "scope": scope,
        },
    )
    s.flush()
    return run


def _info(ctx: EngineContext, s: Session, run: Run) -> tuple[RunInfo, Spec]:
    spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one()
    company = s.scalars(sa.select(Company).where(Company.id == run.company_id)).one()
    prev = last_sealed(s, before=run.number)
    runs = ctx.settings.runs_dir(run.company_id)
    info = RunInfo(
        id=run.id,
        company_id=run.company_id,
        number=run.number,
        kind=run.kind,
        run_date=run.run_date,
        spec=spec.compiled,
        files=RunFiles(runs, run.id),
        prev=RunFiles(runs, prev.id) if prev else None,
        prev_run_date=prev.run_date if prev else None,
        fy_start_month=company.fiscal_year_start_month,
        scope=run.scope,
    )
    return info, spec


def run_currency(ctx: EngineContext, run_id: uuid.UUID) -> str:
    """The currency a run writes its words in: its spec's company settings (M15.6), so the night
    and its replay format money the same way."""
    with ctx.sf() as s:
        spec_id = s.scalar(sa.select(Run.spec_id).where(Run.id == run_id))
        compiled = s.scalar(sa.select(Spec.compiled).where(Spec.id == spec_id)) if spec_id else None
    return str(((compiled or {}).get("company") or {}).get("currency") or "INR")


def execute(ctx: EngineContext, run_id: uuid.UUID) -> str:
    """Run (or resume) every step not done. Inside the company's tenant scope; money is written
    in the run's currency."""
    from othor.core import fmt

    with fmt.money_style(run_currency(ctx, run_id)):
        return _execute(ctx, run_id)


def _execute(ctx: EngineContext, run_id: uuid.UUID) -> str:
    with ctx.sf() as s:
        run = s.scalars(sa.select(Run).where(Run.id == run_id)).one()
        if run.status in ("done", "partial"):
            return run.status
        run.status = "running"
        info, spec = _info(ctx, s, run)
        names = [
            (st.name, st.status)
            for st in s.scalars(
                sa.select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.ordinal)
            )
        ]
        s.commit()
    status = {n: st for n, st in names}
    for name, st in names:
        if st in ("done", SOFT):
            continue
        if ctx.fault:
            ctx.fault(name)
        _mark(ctx, run_id, name, "running")
        try:
            outcome = _step(ctx, info, spec, name, status)
        except Exception as e:
            log.exception("run %s step %s failed", run_id, name)
            _fail(ctx, run_id, name, e)
            raise
        status[name] = "done" if outcome.ok else SOFT
        _mark(ctx, run_id, name, status[name], outcome.detail)
        if ctx.renew:
            ctx.renew()
    with ctx.sf() as s:
        return s.scalars(sa.select(Run.status).where(Run.id == run_id)).one()


def _mark(
    ctx: EngineContext,
    run_id: uuid.UUID,
    name: str,
    status: str,
    detail: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with ctx.sf() as s:
        st = s.scalars(
            sa.select(RunStep).where(RunStep.run_id == run_id, RunStep.name == name)
        ).one()
        st.status = status
        if status == "running":
            st.started_at, st.ended_at, st.error = ctx.clock(), None, None
        else:
            st.ended_at = ctx.clock()
        if detail is not None:
            st.detail = detail
        if error is not None:
            st.error = error[:300]
        s.commit()


def _fail(ctx: EngineContext, run_id: uuid.UUID, name: str, e: BaseException) -> None:
    reason = f"{type(e).__name__}"
    _mark(ctx, run_id, name, "failed", error=reason)
    with ctx.sf() as s:
        run = s.scalars(sa.select(Run).where(Run.id == run_id)).one()
        run.status = "failed"
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="run.step_failed",
            object_type="run",
            object_id=run_id,
            after={"step": name, "error": reason},
        )
        s.commit()


def _step(
    ctx: EngineContext, info: RunInfo, spec: Spec, name: str, status: dict[str, str]
) -> SourceOutcome:
    kind, _, source = name.partition(":")
    if kind == "read":
        out = _soft(lambda: read(ctx, info, source))
        if not out.ok:
            _stale_event(ctx, info, source, out.detail.get("reason"))
        return out
    if kind == "materialise":
        ok = status.get(f"read:{source}") == "done"
        out = _soft(lambda: materialise(ctx, info, source, read_ok=ok))
        if not out.ok and ok:  # the read worked but a query didn't: carry, stale
            carry(info, source, stale=True)
            _stale_event(ctx, info, source, out.detail.get("reason"))
        elif out.ok and ok:
            _version_events(ctx, info, source)
        return out
    if kind == "backfill":
        if status.get(f"materialise:{source}") != "done":
            return SourceOutcome(False, {"reason": "the source wasn't read"})
        out = _soft(lambda: backfill(ctx, info, source))
        if out.ok:
            with ctx.sf() as s:
                for metric, d in sorted(out.detail.get("metrics", {}).items()):
                    record_event(
                        s,
                        actor=SYSTEM_ACTOR,
                        action="snapshot.backfilled",
                        object_type="run",
                        object_id=info.id,
                        after={"metric": metric, **d},
                    )
                s.commit()
        return out
    if kind == "carry":
        return carry(info, source, stale=False)
    if kind == "probes":
        from othor.engine import probes

        if status.get(f"materialise:{source}") not in ("done", None):
            return SourceOutcome(False, {"reason": "the source wasn't read"})
        return _soft(lambda: probes.run(ctx, info, source))
    if kind == "fixture":
        if ctx.settings.env != "test":
            raise FixtureRefused("a fixture run can only run in the test environment")
        return fixture_step(info)
    if kind == "derive":
        _write_context(ctx, info)
        _write_run_inputs(ctx, info)
        return derive(info, spec)
    if kind == "forecast":
        return forecast_step(info)
    if kind == "detect":
        return detect_step(info, spec)
    if kind == "questions":
        return questions(ctx, info)
    if kind == "brief":
        return brief_step(ctx, info)
    if kind == "seal":
        return seal(ctx, info, spec)
    raise RuntimeError(f"unknown step {name}")


def _version_events(ctx: EngineContext, info: RunInfo, source: str) -> None:
    """A new series version or a scale conversion made tonight is a system event."""
    meta = read_json(info.files.source_meta(source)) or {}
    on = periods.as_of(info.run_date).isoformat()
    with ctx.sf() as s:
        for v in meta.get("versions", []):
            if v.get("reinstated") == on:  # back to an earlier definition (M10b)
                record_event(
                    s,
                    actor=SYSTEM_ACTOR,
                    action="series.reinstated",
                    object_type="run",
                    object_id=info.id,
                    after={"source": source, **v},
                )
            if v.get("until") == on:
                record_event(
                    s,
                    actor=SYSTEM_ACTOR,
                    action="series.versioned",
                    object_type="run",
                    object_id=info.id,
                    after={"source": source, **v},
                )
        for c in meta.get("conversions", []):
            if c["on"] == on:
                record_event(
                    s,
                    actor=SYSTEM_ACTOR,
                    action="series.converted",
                    object_type="run",
                    object_id=info.id,
                    after={"source": source, **c},
                )
        s.commit()


def _soft(fn: Any) -> SourceOutcome:
    """A source that can't be reached or answer is a stale source, never a failed run."""
    import duckdb

    try:
        out: SourceOutcome = fn()
        return out
    except (sa.exc.DBAPIError, duckdb.Error, OSError, TimeoutError) as e:
        detail = {"reason": type(e).__name__}
        code = _driver_code(e)
        if code:
            detail["code"] = code
        return SourceOutcome(False, detail)


def _driver_code(e: BaseException) -> str | None:
    """The driver's error code (MySQL's number, PostgreSQL's SQLSTATE), never its message, which
    can quote the SQL (M15.1: the ×35 first night said only "OperationalError")."""
    orig = getattr(e, "orig", None)
    if orig is None:
        return None
    state = getattr(orig, "sqlstate", None)
    if isinstance(state, str) and state:
        return state
    args = getattr(orig, "args", ())
    if args and isinstance(args[0], int):
        return str(args[0])
    return None


def _stale_event(ctx: EngineContext, info: RunInfo, source: str, reason: str | None) -> None:
    with ctx.sf() as s:
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="source.stale",
            object_type="run",
            object_id=info.id,
            after={"source": source, "reason": (reason or "")[:300]},
        )
        s.commit()


# --- inputs → outputs -------------------------------------------------------------------------


CONTEXT = "context.json"
BRIEF_KINDS = ("nightly", "dry", "rerun")  # the runs that write brief issues (M8, v0.11)


def _previous_detecting(s: Session, run: Run) -> Run | None:
    """The run tonight's Sweep compares with (New, Worse, first seen). A Run again now (kind
    `rerun`) compares with the previous day's last run, never an earlier run the same day, so
    "Since yesterday" still means yesterday (v0.11, 11 Nov 2026)."""
    q = sa.select(Run).where(
        Run.status.in_(("done", "partial")),
        Run.number < run.number,
        Run.kind.not_in(("manual", "fixture")),
    )
    if run.kind == "rerun":
        q = q.where(Run.run_date < run.run_date)
    return s.scalars(q.order_by(Run.number.desc()).limit(1)).first()


def _write_context(ctx: EngineContext, info: RunInfo) -> None:
    """What tonight's Sweep depends on besides the data: the previous detecting run's findings
    and watch statuses, and the watches active now. Stored in the inputs (replay reads it)."""
    from othor.engine import watches
    from othor.engine.sweep import carry_context

    with ctx.sf() as s:
        run = s.scalars(sa.select(Run).where(Run.id == info.id)).one()
        number = run.number
        prev = _previous_detecting(s, run)
        rows = watches.snapshot(s)
        holds = active_holds(s, info.run_date)
        record = forecasts_of_record(s, run, info.run_date)
    previous: list[dict[str, Any]] = []
    status: list[dict[str, Any]] = []
    tests_before: int | None = None
    if prev is not None:
        files = RunFiles(ctx.settings.runs_dir(info.company_id), prev.id)
        previous = read_json(files.outputs / "findings.json") or []
        status = read_json(files.outputs / "watches.json") or []
        # #142: how many tests it ran, so a finding that fell under the bar can say why
        got = ((read_json(files.outputs_json) or {}).get("sweep") or {}).get("tests")
        tests_before = int(got) if got is not None else None
    ctx_doc = carry_context(previous, rows, status, holds, tests_before=tests_before)
    ctx_doc["forecasts"] = record
    # #138: the detector versions tonight's Sweep runs, sealed with the inputs so replay runs the
    # same ones (a context stored before has none: replay reads them from the stored results)
    from othor.engine.sweep import current_versions

    ctx_doc["detector_versions"] = current_versions()
    # 0.28.3: a full re-read asked for this run (othor admin reread), sealed with the inputs so a
    # replay forecasts the same; only when there is one, so earlier runs' contexts are unchanged
    from othor.engine.sources import reread_request

    asked = (reread_request(ctx, info.company_id) or {}).get("metrics")
    if asked:
        ctx_doc["reread"] = sorted(str(m) for m in asked)
    if info.kind in BRIEF_KINDS:  # the briefs tonight's run makes (M8), as inputs
        from othor.briefs import service as briefs

        # a rerun ranks against the previous day's issue, as the 06:00 one did (v0.11)
        before = info.run_date if info.kind == "rerun" else None
        with ctx.sf() as s:
            briefs.ensure_default(s, info.spec)
            s.commit()
            ctx_doc["briefs"] = briefs.snapshot(s, before=before, day=info.run_date)
            # 0.28.5: the stored nights each brief's "Since last week" reads; only when there
            # are some, so a first run's context is as before
            from othor.briefs import history

            nights = history.collect(s, number, info.run_date)
            if nights:
                ctx_doc["history"] = nights
    write_json(info.files.inputs / CONTEXT, ctx_doc)


# --- plug-in run inputs (Advanced Intelligence Phase 0 §3) -----------------------------------


@dataclass(frozen=True)
class RunInput:
    """A plug-in's stored input: `collect(ctx, info)` returns a JSON document from what is
    already stored (never a fetch), or None for none tonight. It is written into the run's
    inputs before the manifest, hashed there, and read back as `Context.run_inputs[name]` (and
    the brief step's context); replay reads the file and never collects again."""

    name: str
    collect: Callable[[EngineContext, RunInfo], Any]


RUN_INPUTS: dict[str, RunInput] = {}


def register_run_input(name: str, collect: Callable[[EngineContext, RunInfo], Any]) -> RunInput:
    if _safe(name) != name:
        raise ValueError(f"run input name {name!r}: letters, digits, - and _ only")
    if name in RUN_INPUTS and RUN_INPUTS[name].collect is not collect:
        raise ValueError(f"run input {name!r} is already taken")
    RUN_INPUTS[name] = RunInput(name, collect)
    return RUN_INPUTS[name]


def _write_run_inputs(ctx: EngineContext, info: RunInfo) -> None:
    """Each registered input, as canonical JSON. One that fails is logged and left out: the run
    goes on without it (a plug-in never blocks the night)."""
    for name, ri in sorted(RUN_INPUTS.items()):
        path = run_input_path(info.files, name)
        try:
            doc = ri.collect(ctx, info)
        except Exception:
            log.exception("run input %s failed; the run goes on without it", name)
            path.unlink(missing_ok=True)
            continue
        if doc is None:
            path.unlink(missing_ok=True)
        else:
            write_json(path, doc)


LOOKBACK = {"week": dt.timedelta(weeks=13), "month": dt.timedelta(days=92)}


def forecasts_of_record(s: Session, run: Run, run_date: dt.date) -> dict[str, Any]:
    """For each recent period, the forecast made by the latest earlier run before the period
    started (docs/04 §5): what Forecast breach compares the actual with, never a refit. Also
    the date of the company's first stored forecast (Coverage, 5 Nov 2026)."""
    from othor.engine.models import Forecast

    as_of = run_date - dt.timedelta(days=1)
    earlier = sa.select(Run.id).where(Run.number < run.number, Run.status.in_(("done", "partial")))
    got = s.scalars(
        sa.select(Forecast).where(
            Forecast.run_id.in_(earlier),
            Forecast.period >= as_of - LOOKBACK["week"] - dt.timedelta(days=92),
        )
    ).all()
    best: dict[tuple[str, dt.date], Any] = {}
    for f in got:
        if f.as_of >= f.period or f.period > as_of:
            continue
        if int((f.params or {}).get("horizon") or 1) > 1:  # the outlook's weeks (v0.12): never
            continue  # a forecast of record

        if f.period < as_of - LOOKBACK.get(f.grain, LOOKBACK["month"]):
            continue
        k = (f.series, f.period)
        if k not in best or f.as_of > best[k].as_of:
            best[k] = f
    first = s.scalar(
        sa.select(sa.func.min(Forecast.as_of)).where(
            Forecast.run_id.in_(earlier), Forecast.reconstructed.is_(False)
        )
    )
    return {
        "of_record": [
            {
                "series": f.series,
                "metric": f.metric,
                "grain": f.grain,
                "period": f.period.isoformat(),
                "as_of": f.as_of.isoformat(),
                "point": f.point,
                "lower": f.lower80,
                "upper": f.upper80,
                "method": f.method,
                "reconstructed": f.reconstructed,
            }
            for _, f in sorted(best.items(), key=lambda kv: (kv[0][0], kv[0][1]))
        ],
        "first": first.isoformat() if first else None,
    }


def active_holds(s: Session, day: dt.date) -> dict[str, Any]:
    """The owner's snoozes (until on or after `day`) and mutes, not undone. The run reads them
    as inputs; the pages read the same rows for the overlay."""
    from othor.engine.models import Mute, Snooze

    snoozes = s.scalars(
        sa.select(Snooze).where(Snooze.ended_at.is_(None), Snooze.until >= day)
    ).all()
    mutes = s.scalars(sa.select(Mute).where(Mute.ended_at.is_(None))).all()
    return {
        "snoozes": [{"fingerprint": x.fingerprint, "until": x.until.isoformat()} for x in snoozes],
        "mutes": [{"pattern": x.pattern} for x in mutes],
    }


def manifest(info: RunInfo, spec: Spec) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    for name in sorted(info.spec["sources"]):
        meta = read_json(info.files.source_meta(name)) or {"status": "missing"}
        pts = [
            *read_points(info.files.source_points(name)),
            *read_points(info.files.backfill_points(name)),
        ]
        sources[name] = {
            "status": meta.get("status", "missing"),
            "last_good": meta.get("last_good"),
            "watermarks": meta.get("watermarks", {}),
            "query_hashes": meta.get("query_hashes", {}),
            "unmapped": meta.get("unmapped", []),
            "points": len(pts),
            "points_hash": hash_points(pts),
            "archive_hash": hash_points(read_points(info.files.archive_points(name))),
        }
    from othor.engine.probes import sources_with_probes

    probes = {}
    for name in sources_with_probes(info.spec):
        f = info.files.inputs / f"probes_{_safe(name)}.json"
        probes[name] = sha(f.read_bytes()) if f.exists() else None
    ctxf = info.files.inputs / CONTEXT
    doc = {
        "format": MANIFEST_FORMAT,
        "run_date": info.run_date.isoformat(),
        "kind": info.kind,
        "scope": info.scope,
        "spec_hash": spec.hash,
        "sources": sources,
        "probes": probes,
        "context": sha(ctxf.read_bytes()) if ctxf.exists() else None,
    }
    extra = {name: sha(p.read_bytes()) for name, p in run_input_files(info.files)}
    if extra:  # only when a plug-in stored one: a run without keeps its bytes
        doc["run_inputs"] = extra
    return doc


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def sweep_inputs(files: RunFiles, spec_doc: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    from othor.engine.probes import sources_with_probes

    return {
        "probe_files": {
            s: read_json(files.inputs / f"probes_{_safe(s)}.json") or {}
            for s in sources_with_probes(spec_doc)
        },
        "context": with_run_inputs(read_json(files.inputs / CONTEXT) or {}, files),
    }


FORECASTS = "forecasts.json"
AHEAD = "forecasts_ahead.json"  # the outlook's weeks (v0.12), next to forecasts.json


def forecast_doc(info: RunInfo) -> tuple[dict[str, Any], dict[str, Any]]:
    from othor.engine.forecast import store as fstore

    points = read_points(info.files.series)
    return fstore.tonight(
        info.spec,
        info.run_date,
        points,
        kind=info.kind,
        first=info.prev is None,
        prev=prev_forecasts(info.prev),
        also=brief_headlines(read_json(info.files.inputs / CONTEXT) or {}, info.spec, points),
        reread=tuple((read_json(info.files.inputs / CONTEXT) or {}).get("reread") or ()),
    )


def brief_headlines(
    context: dict[str, Any], spec: dict[str, Any], points: list[Point]
) -> tuple[str, ...]:
    """The numbers every active brief's tiles show, from the run's context (sealed): its chosen
    headline numbers, or its first confirmed numbers when none are chosen. The forecast step
    forecasts them too, so What's ahead has a forecast for each (v0.19 fix, 15 Nov 2026)."""
    from othor.briefs.assemble import tile_metrics

    return tuple(
        sorted({m for b in context.get("briefs") or [] for m in tile_metrics(spec, b, points)})
    )


def prev_forecasts(prev: RunFiles | None) -> dict[str, Any] | None:
    """The previous sealed run's forecasts document (v0.19, M19.2: what changed since the last
    brief, and when a definition changed). Sealed, so replay reads the same bytes."""
    if prev is None:
        return None
    path = prev.outputs / FORECASTS
    return read_json(path) if path.exists() else None


def forecast_step(info: RunInfo) -> SourceOutcome:
    """Forecasts stored before their actuals (M7): tonight's, plus the goal projections; and the
    weeks ahead for the outlook (v0.12), in their own file: the run's outputs stay as before."""
    doc, ahead = forecast_doc(info)
    write_json(info.files.outputs / FORECASTS, doc)
    write_json(info.files.outputs / AHEAD, ahead)
    outputs_doc = read_json(info.files.outputs_json)
    outputs_doc["forecast"] = forecast_summary(doc)
    write_json(info.files.outputs_json, outputs_doc)
    tonight = [r for r in doc["rows"] if not r["reconstructed"]]
    series = sorted({r["series"] for r in tonight})
    banded = sorted({r["series"] for r in tonight if r["lower"] is not None})
    return SourceOutcome(
        True,
        {
            "rows": len(doc["rows"]),
            "goals": len(doc["goals"]),
            "series": len(series),
            "banded": len(banded),
            "waiting": doc["waiting"],
        },
    )


def forecast_summary(doc: dict[str, Any]) -> dict[str, Any]:
    from othor.ontology.compiler.api import canonical

    return {
        "rows": len(doc["rows"]),
        "hash": sha(canonical(doc).encode()),
        "goals": doc["goals"],
        "waiting": doc["waiting"],
    }


def detect_step(info: RunInfo, spec: Spec) -> SourceOutcome:
    from othor.engine.sweep import run_sweep

    doc = read_json(info.files.manifest)
    inputs = load_inputs(info.files, info.spec, doc)
    outputs = read_points(info.files.series)
    extra = sweep_inputs(info.files, info.spec, doc)
    tonight = read_json(info.files.outputs / FORECASTS) or {}
    out = run_sweep(
        info.spec,
        spec.hash,
        info.run_date,
        inputs.points,
        outputs,
        extra["probe_files"],
        {**extra["context"], "goals": tonight.get("goals", [])},
    )
    write_json(info.files.outputs / "detection.json", out.results)
    write_json(info.files.outputs / "findings.json", out.findings)
    write_json(info.files.outputs / "watches.json", out.watches)
    outputs_doc = read_json(info.files.outputs_json)
    outputs_doc["sweep"] = out.doc
    write_json(info.files.outputs_json, outputs_doc)
    return SourceOutcome(True, {"tests": out.doc["tests"], **out.doc["counts"]})


def first_brief(s: Session, number: int) -> bool:
    """No earlier run made a brief: tonight's weather is Sunrise (v0.12). Replay asks the same."""
    return previous_brief(s, number) is None


def previous_brief(s: Session, number: int) -> Run | None:
    """The latest earlier sealed run that made a brief (None: tonight's is the first brief). The
    weather reads its sources (a new source connected → Sunrise) and its goals (one back on
    track → Recovering), v0.12 part 1b. Replay asks the same."""
    q = sa.select(Run).where(
        Run.number < number,
        Run.kind.in_(BRIEF_KINDS),
        Run.status.in_(("done", "partial")),
        Run.run_hash.is_not(None),
    )
    return s.scalars(q.order_by(Run.number.desc()).limit(1)).first()


def brief_step(ctx: EngineContext, info: RunInfo) -> SourceOutcome:
    """One issue per active brief (M8), and the company's weather with its outlook (v0.12), from
    the run's files: replay reproduces both."""
    from othor.briefs import step, weather

    doc = step.from_files(info.files, info.spec, info.run_date)
    write_json(info.files.outputs / step.BRIEFS, doc)
    with ctx.sf() as s:
        prev = previous_brief(s, info.number)
        prev_files = (
            None if prev is None else RunFiles(ctx.settings.runs_dir(info.company_id), prev.id)
        )
        # 0.27.4: the rule of the version this run is sealed under, exactly as replay reads it
        sealed = s.scalar(sa.select(Run.image_version).where(Run.number == info.number))
    previous = None if prev_files is None else weather.previous_of(prev_files)
    strict = weather.strict_for(sealed)
    write_json(
        info.files.outputs / weather.WEATHER,
        weather.from_files(
            info.files,
            info.spec,
            info.run_date,
            kind=info.kind,
            first_brief=prev is None,
            previous=previous,
            strict=strict,
        ),
    )
    # each brief's own weather (part 1c), in its own file: weather.json stays the company's
    write_json(
        info.files.outputs / weather.BRIEFS,
        weather.briefs_from_files(
            info.files,
            info.spec,
            info.run_date,
            kind=info.kind,
            first_brief=prev is None,
            previous=previous,
            strict=strict,
        ),
    )
    outputs_doc = read_json(info.files.outputs_json)
    outputs_doc["briefs"] = step.summary(doc)
    write_json(info.files.outputs_json, outputs_doc)
    return SourceOutcome(True, outputs_doc["briefs"])


def load_inputs(files: RunFiles, spec_doc: dict[str, Any], doc: dict[str, Any]) -> Inputs:
    points = []
    for name in sorted(doc["sources"]):
        points += read_points(files.source_points(name))
        points += read_points(files.backfill_points(name))
    return Inputs(
        run_date=dt.date.fromisoformat(doc["run_date"]),
        kind=doc["kind"],
        scope=doc.get("scope"),
        spec=spec_doc,
        spec_hash=doc["spec_hash"],
        sources=doc["sources"],
        points=points,
    )


def derive(info: RunInfo, spec: Spec) -> SourceOutcome:
    doc = manifest(info, spec)
    write_json(info.files.manifest, doc)
    out = compute(load_inputs(info.files, info.spec, doc))
    write_points(info.files.series, out.points)
    write_json(info.files.baselines, out.baselines)
    write_json(info.files.questions, out.questions)
    write_json(info.files.outputs_json, out.doc)
    days = max((b["history_days"] for b in out.baselines.values()), default=0)
    return SourceOutcome(
        True,
        {
            "metrics": out.doc["series"]["metrics"],
            "series": out.doc["series"]["series"],
            "points": len(out.points),
            "history_days": days,
        },
    )


def helpful_answers(
    ctx: EngineContext, info: RunInfo, coverage: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Suggested answers for tonight's needs questions (v0.12, docs/plans/helpful-questions.md):
    code only, from the spec, the stored series, the catalog and read-only counts. A failure
    leaves the questions as they were (no cards tonight); the run never depends on them."""
    from othor.engine import suggest_facts
    from othor.ontology import service
    from othor.ontology import suggest as sg
    from othor.ontology.compiler.catalog import load_catalog
    from othor.ontology.questions import needs_asked

    asked = set(needs_asked(coverage))
    if not asked:
        return {}
    try:
        with ctx.sf() as s:
            cat = load_catalog(s)
            records = {(r.type, r.key): dict(r.body) for r in service.active(s)}
        facts = suggest_facts.facts_for(ctx, info, asked - {"pair"}, cat, records)
        found: dict[str, Any] = {}
        if "pair" in asked:
            found["pair"] = sg.pairs(info.spec, read_points(info.files.series), info.run_date)
        if "reconcile" in asked:
            found["reconcile"] = sg.reconciles(cat, info.spec, facts.get("reconcile") or {})
        if "entity" in asked:
            found["entity"] = sg.entities(cat, info.spec, records, facts.get("entity") or {})
        if "distribution" in asked:
            found["distribution"] = sg.distributions(
                cat, info.spec, records, facts.get("distribution") or {}
            )
        if "threshold" in asked:
            found["threshold"] = sg.thresholds(cat, info.spec, facts.get("threshold") or {})
        return {need: f.as_subject() for need, f in found.items()}
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning("helpful answers skipped: %s", type(e).__name__)
        return {}


def questions(ctx: EngineContext, info: RunInfo) -> SourceOutcome:
    from othor.engine.asks import ask

    cands = list(read_json(info.files.questions) or [])
    for f in read_json(info.files.outputs / "findings.json") or []:
        q = ask(info.spec, f)
        if q is not None:
            cands.append(q)
    from othor.engine import proposals
    from othor.ontology.questions import needs_candidates, pattern_candidates, refresh_needs

    sweep_doc = (read_json(info.files.outputs_json) or {}).get("sweep") or {}
    coverage = sweep_doc.get("coverage") or []
    cands += pattern_candidates(info.spec)
    needs = needs_candidates(coverage, helpful_answers(ctx, info, coverage))
    cands += needs
    # 0.28 (M28.2): a goal suggested by code while the company has none (no model needed)
    from othor.ontology.goal_suggest import question_candidate
    from othor.ontology.questions import refresh_goal_question

    goal_q = question_candidate(
        info.spec,
        read_points(info.files.series),
        (read_json(info.files.outputs / FORECASTS) or {}).get("rows") or [],
        info.fy_start_month,
        info.run_date,
    )
    if goal_q is not None:
        cands.append(goal_q)
    with ctx.sf() as s:
        made = raise_questions(s, cands)
        refresh_needs(s, needs)
        refresh_goal_question(s, goal_q, bool((info.spec.get("forecast") or {}).get("goals")))
        run = s.scalars(sa.select(Run).where(Run.id == info.id)).one()
        props = proposals.suppression(
            s, run, info.spec, read_json(info.files.outputs / "findings.json") or []
        )
        props += proposals.join(s, run, info.spec, sweep_doc.get("matches") or [])
        props += proposals.goal(
            s, run, info.spec, read_json(info.files.outputs / "detection.json") or []
        )
        props += proposals.definition(s, run, info.spec)
        props += proposals.duplicate(s, run, info.spec, read_points(info.files.series))
        props += _size_numbers(s, run, info)
        s.commit()
        return SourceOutcome(
            True, {"candidates": len(cands), "raised": len(made), "proposals": len(props)}
        )


def _size_numbers(s: Session, run: Run, info: RunInfo) -> list[Any]:
    """#76: the numbers suggested for the company's size and goals, from the run's `size`, the
    scanned catalog (names and kinds) and the goals' states tonight. Never fails the night."""
    from othor.ontology import goal_link, size_numbers
    from othor.ontology.compiler.catalog import load_catalog

    try:
        size = (read_json(info.files.outputs_json) or {}).get("size")
        goals = goal_link.states(
            info.spec,
            (read_json(info.files.outputs / FORECASTS) or {}).get("goals") or [],
            goal_link.at_risk_keys(read_json(info.files.outputs / "detection.json") or []),
        )
        with s.begin_nested():
            return size_numbers.propose(s, run, info.spec, size, load_catalog(s), goals)
    except Exception as e:
        log.warning("size suggestions skipped: %s", type(e).__name__)
        return []


def seal(ctx: EngineContext, info: RunInfo, spec: Spec) -> SourceOutcome:
    input_hash = sha(info.files.manifest.read_bytes())
    output_hash = sha(info.files.outputs_json.read_bytes())
    outputs = read_json(info.files.outputs_json)
    with ctx.sf() as s:
        run = s.scalars(sa.select(Run).where(Run.id == info.id)).one()
        prev = last_sealed(s, before=run.number)
        steps = s.scalars(sa.select(RunStep).where(RunStep.run_id == run.id)).all()
        stale = sorted(
            st.name.split(":", 1)[1] for st in steps if st.status == SOFT and ":" in st.name
        )
        run.input_hash, run.output_hash = input_hash, output_hash
        run.prev_run_hash = prev.run_hash if prev else None
        run.run_hash = run_hash(
            run.prev_run_hash,
            run.number,
            run.kind,
            spec.hash,
            run.image_version,
            input_hash,
            output_hash,
        )
        run.status = "partial" if stale else "done"
        run.ended_at = ctx.clock()
        run.summary = _summary(s, run, steps, outputs, sorted(set(stale)))
        _series_meta(s, run, outputs, info)
        _sweep_rows(s, run, info)
        _forecast_rows(s, run, info)
        _brief_rows(s, run, info)
        record_event(
            s,
            actor=SYSTEM_ACTOR,
            action="run.finished",
            object_type="run",
            object_id=run.id,
            after={
                "number": run.number,
                "status": run.status,
                "input_hash": input_hash,
                "output_hash": output_hash,
                "run_hash": run.run_hash,
                "stale_sources": sorted(set(stale)),
            },
        )
        s.commit()
    publish_series(ctx.settings.analytics_path(info.company_id), info.files.series)
    # 0.28.3: a full re-read asked for is done once a run has read and sealed with it
    from othor.engine.sources import REREAD

    (ctx.settings.company_dir(info.company_id) / REREAD).unlink(missing_ok=True)
    return SourceOutcome(True, {"run_hash": run.run_hash})


def _summary(
    s: Session, run: Run, steps: Any, outputs: dict[str, Any], stale: list[str]
) -> dict[str, Any]:
    from othor.connectors.models import RunQuery

    n, ms = s.execute(
        sa.select(sa.func.count(), sa.func.coalesce(sa.func.sum(RunQuery.duration_ms), 0)).where(
            RunQuery.run_id == run.id, RunQuery.purpose == "run"
        )
    ).one()
    # the helpful questions' read-only counts (v0.12), kept apart from the night's own queries
    help_n = s.scalar(
        sa.select(sa.func.count()).where(RunQuery.run_id == run.id, RunQuery.purpose == "help")
    )
    days = max((b["history_days"] for b in outputs["baselines"].values()), default=0)
    out = {
        "queries": int(n),
        "query_ms": int(ms),
        "help_queries": int(help_n or 0),
        "sources": len(outputs["coverage"]["sources"]),
        "stale_sources": stale,
        "metrics": outputs["series"]["metrics"],
        "history_days": days,
    }
    sweep = outputs.get("sweep")
    if sweep:
        out["sweep"] = {
            "tests": sweep["tests"],
            **sweep["counts"],
            "could_not_run": sum(1 for c in sweep["coverage"] if c["status"] == "could_not_run"),
        }
    return out


def _brief_rows(s: Session, run: Run, info: RunInfo) -> None:
    """The run's brief issues as rows (the page, the archive and delivery read them)."""
    import uuid as _uuid

    from othor.briefs.models import BriefIssue
    from othor.briefs.step import BRIEFS
    from othor.ontology.compiler.api import canonical

    doc = read_json(info.files.outputs / BRIEFS)
    if doc is None:
        return
    # a brief's issue made at its creation from this run's files (0.29.4) isn't the run's own
    s.execute(
        sa.delete(BriefIssue).where(BriefIssue.run_id == run.id, BriefIssue.status != "created")
    )
    for i in doc["issues"]:
        body = i["issue"]
        s.add(
            BriefIssue(
                brief_id=_uuid.UUID(i["brief_id"]),
                brief_version=int(i["version"]),
                run_id=run.id,
                date=run.run_date,
                # Run again now: a new version of the day's issue; the 06:00 one stays (v0.11)
                status="rerun" if run.kind == "rerun" else "ready",
                title=body["title"],
                quiet=bool(body["quiet"]),
                body=body,
                issue_hash=sha(canonical(body).encode()),
                prose={"source": "template"},
            )
        )


def _forecast_rows(s: Session, run: Run, info: RunInfo) -> None:
    """The run's forecasts as rows, before any of their periods has an actual."""
    from othor.engine.models import Forecast

    doc = read_json(info.files.outputs / FORECASTS)
    if doc is None:
        return
    s.execute(sa.delete(Forecast).where(Forecast.run_id == run.id))
    # the outlook's later weeks (v0.12): stored before their actuals too, tagged with their
    # horizon (the first week is tonight's row already); never a forecast of record
    later = [
        {**r, "params": {**r["params"], "horizon": r["horizon"]}}
        for r in (read_json(info.files.outputs / AHEAD) or {}).get("rows") or []
        if int(r["horizon"]) > 1
    ]
    for r in [*doc["rows"], *later]:
        s.add(
            Forecast(
                run_id=run.id,
                series=r["series"],
                metric=r["metric"],
                grain=r["grain"],
                period=dt.date.fromisoformat(r["period"]),
                as_of=dt.date.fromisoformat(r["as_of"]),
                method=r["method"],
                params={**r["params"], "aicc": r["aicc"]},
                point=r["point"],
                lower80=r["lower"],
                upper80=r["upper"],
                reconstructed=bool(r["reconstructed"]),
                written_at=run.ended_at or run.started_at,
            )
        )


def _sweep_rows(s: Session, run: Run, info: RunInfo) -> None:
    """`finding` and `detector_result` rows for this run, from its outputs (counts agree)."""
    from othor.engine.models import DetectorResult, Finding, Watch

    found = read_json(info.files.outputs / "findings.json")
    if found is None:
        return
    # seal may run again on a resumed run: this run's rows are rewritten, never doubled
    s.execute(sa.delete(Finding).where(Finding.run_id == run.id))
    s.execute(sa.delete(DetectorResult).where(DetectorResult.run_id == run.id))
    prev = _previous_detecting(s, run)
    first: dict[str, int] = {}
    if prev is not None:
        first = {
            f.fingerprint: f.first_seen_run
            for f in s.scalars(sa.select(Finding).where(Finding.run_id == prev.id))
        }
    for f in found:
        s.add(
            Finding(
                run_id=run.id,
                fingerprint=f["fingerprint"],
                detector=f["detector"],
                metric=f["metric"],
                breakdown=f["breakdown"],
                breakdown_value=f["breakdown_value"],
                state=f["state"],
                confidence=f["confidence"],
                money=f["money"],
                status=f["status"],
                change=f["change"],
                first_seen_run=first.get(f["fingerprint"], run.number),
                days_open=f.get("days_open", 0),
                series_version=None,
                body=f,
            )
        )
    for r in read_json(info.files.outputs / "detection.json") or []:
        s.add(
            DetectorResult(
                run_id=run.id,
                detector=r["detector"],
                version=r["version"],
                series=r["series"],
                statistic=r["statistic"],
                p_value=r["p_value"],
                q_value=r["q_value"],
                effect=r["effect"],
                money=r["money"],
                passed=r["passed"],
                reason_if_not=(r["reason"] or "")[:200] or None,
                params=r["params"],
            )
        )
    for w in read_json(info.files.outputs / "watches.json") or []:
        row = s.scalars(sa.select(Watch).where(Watch.id == uuid.UUID(w["id"]))).first()
        if row is not None:
            row.status = w["status"]
            row.last_value = w["value"]
            row.breached_since = (
                dt.date.fromisoformat(w["breached_since"]) if w["breached_since"] else None
            )


def _series_meta(s: Session, run: Run, outputs: dict[str, Any], info: RunInfo) -> None:
    pts = read_points(info.files.series)
    groups: dict[tuple[str, str, str], list[Any]] = {}
    for p in pts:
        groups.setdefault((p.metric, p.grain, p.breakdown), []).append(p)
    existing = {(r.metric, r.grain, r.breakdown): r for r in s.scalars(sa.select(SeriesMeta)).all()}
    as_of = periods.as_of(run.run_date)
    for k, ps in sorted(groups.items()):
        good = [p for p in ps if p.complete and p.value is not None]
        recon = [p for p in good if p.reconstructed]
        row = existing.get(k) or SeriesMeta(metric=k[0], grain=k[1], breakdown=k[2])
        stale = any(p.stale for p in ps)
        row.stale = stale
        if not stale:
            row.last_good_at = as_of
        start = min((p.period for p in good), default=None)
        row.history_start = start
        row.history_days = (as_of - start).days + 1 if start else 0
        row.reconstructed_to = min((p.period for p in recon), default=None)
        b = outputs["baselines"].get(f"{k[0]}|{k[1]}") or {}
        row.reconstructed_from = b.get("reconstructed_from") if recon else None
        row.last_run_id = run.id
        if k not in existing:
            s.add(row)
    s.flush()
