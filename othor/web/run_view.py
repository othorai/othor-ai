# SPDX-License-Identifier: Apache-2.0
"""What the First run screen (2o) shows: dry run #0001's steps, the headline numbers it computed,
and its provenance line. Every figure comes from `run`, `run_step`, `run_query` rows and the
run's outputs; nothing is written here. Detectors, forecasts and the brief appear only when their
capability ships (othor.core.features, decided 5 Nov 2026)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.connectors.models import RunQuery
from othor.core import features, fmt
from othor.core.config import Settings
from othor.core.localtime import local_now
from othor.core.models import Company
from othor.engine.models import Run, RunStep
from othor.engine.sources import metrics_on
from othor.engine.store import RunFiles, read_json
from othor.jobs.models import Job
from othor.ontology.models import Spec
from othor.setup.confirm import FIRST_RUN_KEY
from othor.web.setup_view import value_text

SOURCE_STEPS = ("read", "materialise", "backfill", "carry")
KIND_LABEL = {
    "dry": "dry run",
    "nightly": "nightly run",
    "manual": "read now",
    "rerun": "run again",
    "fixture": "fixture run",
}
STEP_LABEL = {
    "read": "reading",
    "materialise": "querying",
    "backfill": "reconstructing history from",
    "derive": "computing baselines",
    "questions": "raising questions",
    "seal": "sealing the run",
}


@dataclass
class Column:
    name: str
    dot: str  # ok · warn · muted · neg
    line: str
    state: str = ""  # done · now · next · skipped · failed; empty: from the dot

    @property
    def stage(self) -> str:
        return self.state or {"ok": "done", "warn": "now", "neg": "failed"}.get(self.dot, "next")


@dataclass
class Headline:
    name: str
    value: str
    period: str
    note: str


@dataclass
class FirstRun:
    state: str  # starting · running · done · partial · failed
    number: str = "#0001"
    kind: str = "dry run"
    started: str | None = None
    elapsed: str | None = None
    progress: tuple[int, int] = (0, 1)
    columns: list[Column] = field(default_factory=list)
    headlines: list[Headline] = field(default_factory=list)
    provenance: str = ""
    failed: str | None = None
    stale_note: str | None = None
    forecast_note: str | None = None
    brief_note: str | None = None
    brief_ready: bool = False

    @property
    def stages(self) -> list[dict[str, str]]:
        """2o (M11c, O3): the run's stages as the owner reads them, with computed notes."""
        label = {
            "Queries": "Read your sources",
            "Baselines": "Learn what’s normal",
            "Detectors": "Check for anything unusual",
            "Forecasts": "Forecasts",
            "Brief": "Write the brief",
        }
        out = []
        cols = list(self.columns)
        have = {c.name for c in cols}
        # every stage holds its place from the start (no jump as the run fills in, M12)
        wanted = ["Queries", "Baselines"]
        wanted += ["Detectors"] if features.DETECTORS else []
        wanted += ["Forecasts"] if features.FORECASTS else []
        wanted += ["Brief"] if features.BRIEFS else []
        cols += [Column(n, "muted", "waiting") for n in wanted if n not in have]
        for c in cols:
            note = c.line
            if c.stage == "skipped" and c.name == "Forecasts":
                note = "Skipped · added once there’s more history"
            elif c.stage == "next":
                note = ""
            elif c.stage == "now" and c.name == "Brief":
                note = "Writing…"
            out.append({"label": label.get(c.name, c.name), "state": c.stage, "note": note})
        return out

    @property
    def stages_done(self) -> int:
        return sum(1 for x in self.stages if x["state"] in ("done", "skipped"))

    @property
    def polling(self) -> bool:
        return self.state in ("starting", "running")

    @property
    def finished(self) -> bool:
        return self.state in ("done", "partial")


def planned_queries(
    spec: dict[str, Any], steps: list[str], sent_by: dict[str, int] | None = None
) -> int:
    """How many statements the run's source steps send: known from the spec before they run.
    A back-fill reads its span in 13-Sunday chunks (M15.1), so how many it sends depends on how
    far back the data goes: once its step has finished (`sent_by`, step name → statements it
    recorded), that count is the plan; before, one statement per query is the estimate."""
    n = 0
    for name in steps:
        if name.startswith("backfill:") and sent_by and name in sent_by:
            n += sent_by[name]
            continue
        kind, _, src = name.partition(":")
        if kind == "read" and spec["sources"][src]["kind"] != "gsheets":
            n += sum(1 for t in spec.get("read", {}).values() if t["source"] == src)
        elif kind == "materialise":
            for _, m in metrics_on(spec, src):
                n += sum(
                    1
                    for k in m.get("queries", {})
                    if not k.startswith("backfill:") and k != "history_start"
                )
                n += 1 if (m.get("unmapped") or {}).get("sql") else 0
        elif kind == "backfill":
            for _, m in metrics_on(spec, src):
                q = m.get("queries", {})
                if (m.get("backfill") or {}).get("possible") and "history_start" in q:
                    n += 1 + sum(1 for k in q if k.startswith("backfill:"))
    return n


def first_run(
    s: Session, settings: Settings, company: Company, now: dt.datetime
) -> FirstRun | None:
    """None when there is no dry run (setup confirmed before M5): the page says so plainly."""
    job = s.scalars(sa.select(Job).where(Job.dedupe_key == FIRST_RUN_KEY)).first()
    run = None
    if job is not None:
        run = s.scalars(sa.select(Run).where(Run.job_id == job.id)).first()
    if run is None:  # confirmed before M5: the company's first run, whatever its kind
        run = s.scalars(sa.select(Run).order_by(Run.number)).first()
    if run is None:
        if job is None:
            return None
        failed = "The run couldn't start." if job.status == "failed" else None
        return FirstRun(
            state="failed" if failed else "starting",
            failed=failed,
            columns=[
                Column("Queries", "muted", "waiting"),
                Column("Baselines", "muted", "waiting"),
            ],
        )
    return progress(s, settings, company, run, job, now, first=True)


def starting(job: Any) -> FirstRun:
    """A run job not yet started (no run row): waiting, or failed before it could start."""
    failed = "The run couldn't start." if job is not None and job.status == "failed" else None
    return FirstRun(
        state="failed" if failed else "starting",
        number="",
        kind="",
        failed=failed,
        columns=[Column("Queries", "muted", "waiting"), Column("Baselines", "muted", "waiting")],
    )


def progress(
    s: Session,
    settings: Settings,
    company: Company,
    run: Run,
    job: Any,
    now: dt.datetime,
    *,
    first: bool = False,
) -> FirstRun:
    """Any run's progress as 2o shows it: the first run, and Run again now (v0.11)."""
    spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one()
    steps = s.scalars(
        sa.select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.ordinal)
    ).all()
    sent, ms = s.execute(
        sa.select(sa.func.count(), sa.func.coalesce(sa.func.sum(RunQuery.duration_ms), 0)).where(
            RunQuery.run_id == run.id,
            RunQuery.purpose == "run",  # not the helpful questions' counts (v0.12)
        )
    ).one()
    state = run.status
    if state == "running" and job is not None and job.status == "failed":
        state = "failed"
    view = FirstRun(state=state, number=f"#{run.number:04d}", kind=KIND_LABEL[run.kind])
    start = local_now(company, run.started_at)
    view.started = fmt.time_hm(start.time(), seconds=True)
    end = run.ended_at or now
    view.elapsed = f"{fmt.num(max(0, int((end - run.started_at).total_seconds())))} s elapsed"
    done = sum(1 for st in steps if st.status in ("done", "stale"))
    view.progress = (done, max(1, len(steps)))
    view.columns = _columns(spec.compiled, steps, int(sent), int(ms))
    view.forecast_note = forecast_note(steps) if features.FORECASTS else None
    if features.BRIEFS and first:
        view.brief_note, view.brief_ready = _brief_note(s, company, steps)
    if state == "failed":
        st = next((x for x in steps if x.status in ("failed", "running")), None)
        view.failed = _failed_line(st)
    stale = sorted(
        st.name.split(":", 1)[1] for st in steps if st.status == "stale" and ":" in st.name
    )
    if stale:
        names = list(dict.fromkeys(stale))
        view.stale_note = (
            f"{' and '.join(names)} couldn't be read. "
            f"{'Its' if len(names) == 1 else 'Their'} numbers wait for the next run."
        )
    files = RunFiles(settings.runs_dir(company.id), run.id)
    outputs = read_json(files.outputs_json) if view.finished or _derived(steps) else None
    if outputs:
        view.headlines = _headlines(spec.compiled, outputs)
    view.provenance = " · ".join(
        [
            view.number,
            f"spec v{spec.ontology_version}",
            f"image {run.image_version}",
            f"sha {_short(spec.hash)}",
            f"inputs {fmt.num(len(spec.compiled['sources']))} sources",
            f"hash {_short(run.run_hash) if run.run_hash else 'pending'}",
        ]
    )
    return view


def phases(s: Session, run: Run) -> list[Column]:
    """Any run's steps as the owner reads them (4f3, 4e1): Read sources, then the same columns as
    the first run (2o). The raw step names stay in the run log."""
    spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one()
    steps = s.scalars(
        sa.select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.ordinal)
    ).all()
    sent, ms = s.execute(
        sa.select(sa.func.count(), sa.func.coalesce(sa.func.sum(RunQuery.duration_ms), 0)).where(
            RunQuery.run_id == run.id,
            RunQuery.purpose == "run",  # not the helpful questions' counts (v0.12)
        )
    ).one()
    reads = [st for st in steps if st.name.startswith("read:")]
    bad = [st for st in reads if st.status in ("failed", "stale")]
    ok = sum(1 for st in reads if st.status == "done")
    if not reads:
        read = Column("Read sources", "muted", "waiting")
    elif bad:
        names = ", ".join(st.name.split(":", 1)[1] for st in bad)
        dot = "neg" if any(st.status == "failed" for st in bad) else "warn"
        read = Column(
            "Read sources", dot, f"{fmt.num(ok)} of {fmt.num(len(reads))} · {names} not read"
        )
    elif all(st.status == "done" for st in reads):
        read = Column("Read sources", "ok", f"{fmt.num(ok)} of {fmt.num(len(reads))}")
    else:
        read = Column("Read sources", "warn", f"{fmt.num(ok)} of {fmt.num(len(reads))} · reading")
    return [read, *_columns(spec.compiled, steps, int(sent), int(ms))]


def _derived(steps: Any) -> bool:
    return any(st.name == "derive" and st.status == "done" for st in steps)


def _short(h: str) -> str:
    return f"{h[:4]}…{h[-2:]}"


def _columns(spec: dict[str, Any], steps: Any, sent: int, ms: int) -> list[Column]:
    src_steps = [st for st in steps if st.name.split(":")[0] in SOURCE_STEPS]
    finished = {
        st.name: int((st.detail or {}).get("queries") or 0)
        for st in steps
        if st.status == "done" and st.name.startswith("backfill:")
    }
    planned = planned_queries(spec, [st.name for st in steps], finished)
    if all(st.status in ("done", "stale") for st in src_steps):
        dot = "warn" if any(st.status == "stale" for st in src_steps) else "ok"
    elif any(st.status in ("running", "done") for st in src_steps):
        dot = "warn"  # in progress (status dots are never accent, docs/07)
    else:
        dot = "muted"
    secs = fmt.num(Decimal(ms) / 1000, decimals=1)
    read_done = all(st.status in ("done", "stale") for st in src_steps) and bool(src_steps)
    queries = Column(
        "Queries",
        dot,
        f"{fmt.num(sent)} of {fmt.num(planned)} · {secs} s",
        "done" if read_done else "",  # a stale read is done (amber), not still running
    )
    derive = next((st for st in steps if st.name == "derive"), None)
    if derive is not None and derive.status == "done":
        d = derive.detail
        baselines = Column(
            "Baselines",
            "ok",
            f"{fmt.num(d.get('metrics', 0))} metrics · {fmt.num(d.get('history_days', 0))} days",
        )
    elif derive is not None and derive.status == "running":
        baselines = Column("Baselines", "warn", "computing")
    else:
        baselines = Column("Baselines", "muted", "waiting")
    # Forecasts (M7) and Brief (M8) join here, each behind its flag in othor.core.features;
    # until then the screen leaves them out (5 Nov 2026)
    cols = [queries, baselines]
    if features.DETECTORS:
        cols.append(_detectors(spec, steps))
    if features.FORECASTS:
        cols.append(_forecasts(steps))
    if features.BRIEFS:
        cols.append(_brief(steps))
    return cols


def _brief(steps: Any) -> Column:
    """2o's Brief column: the briefs the run wrote (M8)."""
    st = next((x for x in steps if x.name == "brief"), None)
    if st is not None and st.status == "done":
        n = int(st.detail.get("issues", 0))
        return Column("Brief", "ok", f"{fmt.num(n)} written" if n != 1 else "written")
    if st is not None and st.status == "running":
        return Column("Brief", "warn", "writing")
    return Column("Brief", "muted", "waiting")


def _forecasts(steps: Any) -> Column:
    """2o's Forecasts column: tonight's headline forecasts with a band, of all made."""
    st = next((x for x in steps if x.name == "forecast"), None)
    if st is not None and st.status == "done":
        d = st.detail
        if not d.get("series"):  # no series long enough (M12b)
            return Column("Forecasts", "muted", "None yet", "skipped")
        dot = "ok" if d.get("banded") == d.get("series") else "warn"
        return Column(
            "Forecasts", dot, f"{fmt.num(d.get('banded', 0))} of {fmt.num(d.get('series', 0))}"
        )
    if st is not None and st.status == "running":
        return Column("Forecasts", "warn", "fitting")
    return Column("Forecasts", "muted", "waiting")


def _brief_note(s: Any, company: Any, steps: Any) -> tuple[str | None, bool]:
    """'First brief tomorrow at 06:00 IST to <owner's email>. …' once the brief is written."""
    st = next((x for x in steps if x.name == "brief" and x.status == "done"), None)
    if st is None:
        return None, False
    from othor.briefs.models import Recipient

    to = sorted(
        {r.email for r in s.scalars(sa.select(Recipient).where(Recipient.status == "active"))}
    )
    tz = fmt.tz_abbr(company.timezone or "Asia/Kolkata", dt.date.today())
    who = f" to {', '.join(to)}" if to else ""
    return (
        f"First brief tomorrow at {fmt.time_hm(company.brief_time or '06:00')} {tz}{who}. "
        "Add recipients and more briefs from Morning Brief.",
        True,
    )


def forecast_note(steps: Any) -> str | None:
    """2o's callout: the forecasts that start once 8 weeks of history exist."""
    st = next((x for x in steps if x.name == "forecast" and x.status == "done"), None)
    if st is None:
        return None
    n = int(st.detail.get("series", 0)) - int(st.detail.get("banded", 0))
    if n <= 0:
        return None
    return f"{fmt.num(n)} forecast{'s' if n != 1 else ''} start once 8 weeks of history exist."


def _detectors(spec: dict[str, Any], steps: Any) -> Column:
    """2o's Detectors column: the detectors shipped tonight (Forecast breach from M7) and the
    run's tests, from the detect step."""
    from othor.engine.detectors import REGISTRY

    shipped = [
        d
        for d in REGISTRY.values()
        if d.enabled or (d.name == "forecast_breach" and features.FORECASTS)
    ]
    det = next((st for st in steps if st.name == "detect"), None)
    n = len(shipped)
    if det is not None and det.status == "done":
        tests = fmt.num(det.detail.get("tests", 0))
        return Column("Detectors", "ok", f"{fmt.num(n)} of {fmt.num(n)} · {tests} tests")
    if det is not None and det.status == "running":
        return Column("Detectors", "warn", f"{fmt.num(n)} detectors · running")
    return Column("Detectors", "muted", "waiting")


def _failed_line(st: Any) -> str:
    if st is None:
        return "The run stopped."
    kind, _, src = st.name.partition(":")
    what = STEP_LABEL.get(kind, kind)
    return f"The run stopped while {what}{' ' + src if src else ''}."


PREVIEW_ITEMS = 4


def _headlines(spec: dict[str, Any], outputs: dict[str, Any]) -> list[Headline]:
    """The headline numbers (goals and metrics marked headline); without any, the first
    confirmed metrics with a computed value, in the Ontology's order."""
    heads = [h for h in outputs.get("headline", []) if h.get("baseline")]
    if not heads:
        for key in spec.get("order", []):
            m = spec["metrics"].get(key, {})
            grain = m.get("grain") or "month"
            if m.get("listed") is False or f"{key}|{grain}" not in outputs["baselines"]:
                continue
            heads.append(
                {
                    "metric": key,
                    "name": m.get("name") or key,
                    "grain": grain,
                    "baseline": f"{key}|{grain}",
                }
            )
            if len(heads) == PREVIEW_ITEMS:
                break
    out = []
    for h in heads:
        b = outputs["baselines"].get(h["baseline"] or "")
        if not b:
            continue
        m = spec["metrics"].get(h["metric"], {})
        unit = m.get("unit") or "inr"
        last = b["last"]
        period = _period(dt.date.fromisoformat(last["period"]), h["grain"])
        notes = []
        prev = b.get("prev")
        if prev and Decimal(prev["value"]) != 0:
            change = (Decimal(last["value"]) - Decimal(prev["value"])) / abs(Decimal(prev["value"]))
            notes.append(
                f"{fmt.pct(change, decimals=1, sign=True)} vs "
                f"{_period(dt.date.fromisoformat(prev['period']), h['grain'])}"
            )
        if b.get("reconstructed_to"):
            notes.append(
                f"History reconstructed to {_long(dt.date.fromisoformat(b['reconstructed_to']))} "
                f"from {b['reconstructed_from']}"
            )
        else:
            notes.append(f"{fmt.num(b['history_days'])} days of history")
        out.append(Headline(h["name"], value_text(last["value"], unit), period, " · ".join(notes)))
    return out


def _period(d: dt.date, grain: str) -> str:
    if grain == "month":
        return f"{d:%b} {d.year}"
    if grain == "week":
        return f"week of {fmt.day_month(d)}"
    return fmt.date_short(d)


def _long(d: dt.date) -> str:
    return f"{d.day} {d:%b} {d.year}"
