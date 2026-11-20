# SPDX-License-Identifier: Apache-2.0
"""Per-source steps of a run: read, materialise, back-fill, carry (docs/02 steps 1–2, docs/04 §2).

SQL sources: the compiled queries run in place through the read-only role and only aggregates
come back. Google Sheets: tabs are read whole into DuckDB and the queries run there. Every
statement goes through `QueryLog` (the personal-column guard) and is stored in `run_query`.

Each source's points are written to `inputs/series_<source>.parquet`: re-read periods from this
run, earlier periods carried from the previous run's inputs, snapshots never overwritten once
captured. A source that can't be read carries its previous points, marked stale.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlglot import exp

from othor.connectors import credentials, kinds
from othor.connectors.models import RunQuery, Source
from othor.connectors.sheets.read import read_tabs, record_read
from othor.connectors.sql.engine import creds_from_source, friendly_error, make_engine, ping
from othor.connectors.sql.query_log import QueryLog, guard_sql
from othor.engine import periods
from othor.engine.context import EngineContext
from othor.engine.store import (
    Point,
    RunFiles,
    dec,
    read_json,
    read_points,
    write_json,
    write_points,
)
from othor.ontology.compiler.catalog import load_catalog

DIALECT_SQLGLOT = {"postgres": "postgres", "mysql": "mysql"}


@dataclass
class RunInfo:
    """What a step needs to know about its run."""

    id: uuid.UUID
    company_id: uuid.UUID
    number: int
    kind: str
    run_date: dt.date
    spec: dict[str, Any]
    files: RunFiles
    prev: RunFiles | None = None  # the previous sealed run's files (carry-over)
    prev_run_date: dt.date | None = None
    fy_start_month: int = 4
    scope: dict[str, Any] | None = None


@dataclass
class SourceOutcome:
    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)


def metrics_on(spec: dict[str, Any], source: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (k, m)
        for k, m in sorted(spec["metrics"].items())
        if m.get("type") == "base" and m.get("status") == "active" and m.get("source") == source
    ]


# --- executing a compiled query ---------------------------------------------------------------


class Executor:
    """One source's connection for one step: SQL in place, or DuckDB over the extracted tabs."""

    def __init__(
        self, ctx: EngineContext, info: RunInfo, source: str, *, purpose: str = "run"
    ) -> None:
        self.ctx, self.info, self.source = ctx, info, source
        # "run": the night's own queries; "help": the helpful questions' counts (not the run's
        # planned queries, kept apart in the run summary)
        self.purpose = purpose
        meta = info.spec["sources"][source]
        self.kind = meta["kind"]
        self.source_id = uuid.UUID(meta["id"])
        with ctx.sf() as s:
            cat = load_catalog(s)
            personal = {t.name.lower(): set(t.personal) for t in cat.tables.values() if t.personal}
            src = s.scalars(sa.select(Source).where(Source.id == self.source_id)).one()
            secret = credentials.load(s, ctx.key, src.company_id, src.id)
            s.expunge(src)
        self.log = QueryLog(personal=personal)
        self._engine: sa.Engine | None = None
        self._conn: sa.Connection | None = None
        self._src, self._secret = src, secret

    def __enter__(self) -> Executor:
        return self

    def __exit__(self, *exc: object) -> None:
        self._close()
        if self._engine is not None:
            self._engine.dispose()

    def _close(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            with contextlib.suppress(Exception):
                conn.close()

    def run(self, sql: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
        params = params or {}
        if kinds.is_local(self.kind):  # fetched into DuckDB (Sheets, a plug-in kind)
            return self._duck(sql, params)
        if self._engine is None:
            self._engine = make_engine(creds_from_source(self._src, self._secret or {}))
        used = {k: v for k, v in params.items() if f":{k}" in sql}
        # one connection for the step's statements (M15.1: not a TLS handshake per statement);
        # each statement is still its own read-only transaction, and a failure closes it
        if self._conn is None:
            self._conn = self._engine.connect()
        try:
            rows = [tuple(r) for r in self.log.run(self._conn, sql, used)]
            self._conn.rollback()
        except BaseException:
            self._close()
            raise
        return rows

    def _duck(self, sql: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
        import duckdb

        path = self.ctx.settings.analytics_path(self.info.company_id)
        guard_sql(sql, self.log.personal)  # the same guard as a SQL source
        p = {k: v.isoformat() if isinstance(v, dt.date) else v for k, v in params.items()}
        used = {k: v for k, v in p.items() if f"${k}" in sql}
        t0 = time.perf_counter()
        con = duckdb.connect(str(path), read_only=True)
        try:
            rows = [tuple(r) for r in con.execute(sql, used).fetchall()]
        finally:
            con.close()
        self.log.record(sql, used, rows=len(rows), ms=int((time.perf_counter() - t0) * 1000))
        return rows

    def connection_error(self) -> str | None:
        """None when the source answers; else a reason without row values or secrets."""
        if kinds.is_local(self.kind):
            return None
        try:
            if self._engine is None:
                self._engine = make_engine(creds_from_source(self._src, self._secret or {}))
            ping(self._engine)
            return None
        except Exception as e:
            return friendly_error(e, creds_from_source(self._src, self._secret or {}))[:300]

    def save_log(self) -> tuple[int, int]:
        """Store what ran in `run_query`; returns (queries, total ms)."""
        with self.ctx.sf() as s:
            for e in self.log.entries:
                s.add(
                    RunQuery(
                        run_id=self.info.id,
                        purpose=self.purpose,
                        source_id=self.source_id,
                        sql=e.sql,
                        params={k: _jsonable(v) for k, v in e.params.items()},
                        rows=e.rows,
                        duration_ms=e.duration_ms,
                        hash=e.hash,
                    )
                )
            s.commit()
        n, ms = len(self.log.entries), sum(e.duration_ms for e in self.log.entries)
        self.log.entries.clear()
        return n, ms


def _jsonable(v: Any) -> Any:
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


# --- step 1: read ----------------------------------------------------------------------------


def read(ctx: EngineContext, info: RunInfo, source: str) -> SourceOutcome:
    """Sheets: whole-tab read into DuckDB. SQL: each read table's watermark and row count."""
    meta = info.spec["sources"][source]
    if kinds.is_local(meta["kind"]):
        fetch = kinds.get(meta["kind"]).fetch or read_tabs
        res = fetch(ctx.settings, ctx.sf, ctx.key, uuid.UUID(meta["id"]), ctx.clock)
        detail = {"rows": res.rows, "tabs": res.tabs, "skipped": sorted(res.skipped)}
        if not res.ok:
            return SourceOutcome(False, {**detail, "reason": res.reason})
        write_json(info.files.source_meta(source), {"watermarks": {}, "read": detail})
        return SourceOutcome(True, detail)
    t0 = time.perf_counter()
    with Executor(ctx, info, source) as ex:
        err = ex.connection_error()
        if err:
            return SourceOutcome(False, {"reason": err})
        marks: dict[str, Any] = {}
        rows = 0
        for name, t in sorted(info.spec.get("read", {}).items()):
            if t["source"] != source:
                continue
            sql = _watermark_sql(t, meta["dialect"])
            r = ex.run(sql)[0]
            n = int(r[-1] or 0)
            rows += n
            marks[name] = {
                "watermark": t.get("watermark"),
                "max": _jsonable(r[0]) if t.get("watermark") else None,
                "rows": n,
            }
        queries, ms = ex.save_log()
    ms_total = int((time.perf_counter() - t0) * 1000)
    record_read(
        ctx.sf,
        uuid.UUID(meta["id"]),
        ctx.clock,
        rows=rows,
        tables=len(marks),
        ms=ms_total,
        run_id=info.id,
    )
    write_json(info.files.source_meta(source), {"watermarks": marks, "read": {"rows": rows}})
    return SourceOutcome(True, {"tables": len(marks), "rows": rows, "queries": queries, "ms": ms})


def _watermark_sql(t: dict[str, Any], dialect: str) -> str:
    table = exp.Table(
        this=exp.to_identifier(t["table"], quoted=True),
        db=exp.to_identifier(t["schema"], quoted=True),
    )
    cols: list[exp.Expr] = []
    if t.get("watermark"):
        cols.append(
            exp.alias_(exp.Max(this=exp.column(t["watermark"], quoted=True)), "wm", quoted=True)
        )
    cols.append(exp.alias_(exp.Count(this=exp.Star()), "n", quoted=True))
    return exp.select(*cols).from_(table).sql(dialect=DIALECT_SQLGLOT.get(dialect, dialect))


# --- step 2: materialise --------------------------------------------------------------------


def previous_points(info: RunInfo, source: str) -> list[Point]:
    if info.prev is None:
        return []
    return [
        *read_points(info.prev.source_points(source)),
        *read_points(info.prev.backfill_points(source)),
    ]


def previous_archive(info: RunInfo, source: str) -> list[Point]:
    return read_points(info.prev.archive_points(source)) if info.prev is not None else []


def definitions(spec: dict[str, Any], source: str) -> dict[str, dict[str, Any]]:
    """Each metric's definition hash and scale on this source (series versions, 5 Nov 2026)."""
    return {
        key: {
            "hash": m.get("definition_hash", ""),
            "scale": str(m.get("scale") or "1"),
            **({"meaning": dict(m["meaning"])} if m.get("meaning") else {}),
        }
        for key, m in metrics_on(spec, source)
    }


def backfilled_before(info: RunInfo, source: str) -> dict[str, str]:
    """metric -> definition hash of the back-fills earlier nights finished (M15.1: kept in the
    source meta, so a back-fill that failed is due again the next night). A run from before
    M15.1 recorded only its definitions, which then stand for its back-fills."""
    prev_meta = (read_json(info.prev.source_meta(source)) if info.prev else None) or {}
    if "backfilled" in prev_meta:
        return {k: str(v) for k, v in prev_meta["backfilled"].items()}
    return {k: str(v["hash"]) for k, v in (prev_meta.get("definitions") or {}).items()}


def due_backfill(info: RunInfo, source: str) -> list[str]:
    """The snapshot metrics to back-fill tonight: on the company's first run, when a metric
    first becomes active, when its definition changes (a new series version), and again after a
    back-fill that didn't finish (M15.1); each only when its definition can be computed as of
    past dates (5 Nov 2026)."""
    done = backfilled_before(info, source)
    out = []
    for key, m in metrics_on(info.spec, source):
        if not (m.get("backfill") or {}).get("possible"):
            continue
        if info.prev is None or done.get(key) != m.get("definition_hash", ""):
            out.append(key)
    return sorted(out)


PAISA = Decimal("0.01")


def _scaled(p: Point, m: dict[str, Any]) -> Point:
    """The value as stored: times the metric's scale; rupees rounded half-even to paise only
    here, when the value is stored (computed at 38 digits, decided 5 Nov 2026)."""
    from decimal import ROUND_HALF_EVEN

    scale = Decimal(str(m.get("scale") or "1"))
    if p.value is None:
        return p
    v = p.value * scale if scale != 1 else p.value
    if m.get("unit") == "inr":
        v = v.quantize(PAISA, rounding=ROUND_HALF_EVEN)
    return p if v == p.value else p._replace(value=v)


def _qhash(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()


REREAD = "reread.json"  # a request to read every series again in full (0.28.3, admin reread)


def reread_request(ctx: EngineContext, company_id: Any) -> dict[str, Any] | None:
    """`othor admin reread` asks the next run to read the company's series again in full, from
    the read-only sources: {"metrics": [...]} or {"metrics": ["*"]}. Cleared when a run seals."""
    got = read_json(ctx.settings.company_dir(company_id) / REREAD)
    return got if isinstance(got, dict) else None


def _changed(before: dict[str, Any], d: dict[str, Any]) -> tuple[bool, set[str]]:
    """(the whole metric's meaning changed, the breakdowns whose own meaning changed). With both
    nights' meanings (0.28.3) only a real change of what a series means counts, and a breakdown
    added or changed restarts that breakdown alone; a night before that compares the old hash."""
    bm, dm = before.get("meaning") or {}, d.get("meaning") or {}
    if bm and dm:
        if bm.get("") != dm.get(""):
            return True, set()
        return False, {b for b in (set(bm) | set(dm)) - {""} if bm.get(b) != dm.get(b)}
    return before["hash"] != d["hash"], set()


def materialise(ctx: EngineContext, info: RunInfo, source: str, *, read_ok: bool) -> SourceOutcome:
    prev_meta = (read_json(info.prev.source_meta(source)) if info.prev else None) or {}
    prev = previous_points(info, source)
    if not read_ok:
        return carry(info, source, stale=True, prev_meta=prev_meta, prev=prev)
    run_date, as_of = info.run_date, periods.as_of(info.run_date)
    window = periods.history_start(run_date, info.fy_start_month)
    prev_hashes: dict[str, str] = prev_meta.get("query_hashes", {})
    prev_defs: dict[str, dict[str, Any]] = prev_meta.get("definitions", {})
    by_series: dict[tuple[str, str, str], list[Point]] = {}
    for p in prev:
        by_series.setdefault((p.metric, p.grain, p.breakdown), []).append(p)
    archive = previous_archive(info, source)
    versions: list[dict[str, Any]] = list(prev_meta.get("versions", []))
    conversions: list[dict[str, Any]] = list(prev_meta.get("conversions", []))
    defs = definitions(info.spec, source)
    # series to read again in full tonight (metric, breakdown; "*" = all of the metric's): never
    # keep nothing and read only the refresh window (0.28.3: that lost years of history)
    refetch: set[tuple[str, str]] = set()
    asked = (reread_request(ctx, info.company_id) or {}).get("metrics") or []
    for key in defs:
        if "*" in asked or key in asked:
            refetch.add((key, "*"))
    for key, d in sorted(defs.items()):
        before = prev_defs.get(key)
        if before is None:
            continue
        whole, parts = _changed(before, d)
        if not whole and parts:
            # one breakdown means something new: only its series start again, read in full
            for b in sorted(parts):
                old = [p for k, ps in by_series.items() if k[0] == key and k[2] == b for p in ps]
                if old and info.spec["metrics"][key].get("kind") == "snapshot":
                    tag = (before.get("meaning") or {}).get(b) or before["hash"]
                    archive += [p._replace(metric=f"{key}@{tag}") for p in old]
                for k in [k for k in by_series if k[0] == key and k[2] == b]:
                    del by_series[k]
                refetch.add((key, b))
            continue
        if whole:
            # a new series version: the old points are kept, apart, and never mixed
            old = sorted(
                (p for k, ps in by_series.items() if k[0] == key for p in ps),
                key=Point.sort_key,
            )
            if old and info.spec["metrics"][key].get("kind") == "snapshot":
                archive += [p._replace(metric=f"{key}@{before['hash']}") for p in old]
                versions.append(
                    {
                        "metric": key,
                        "definition": before["hash"],
                        "until": as_of.isoformat(),
                        "from": min(p.period for p in old).isoformat(),
                        "to": max(p.period for p in old).isoformat(),
                        "points": len(old),
                    }
                )
            for k in [k for k in by_series if k[0] == key]:
                del by_series[k]
            refetch.add((key, "*"))
            # back to a definition seen before (a Restore or an Undo, M10b): its archived points
            # are the series again, so history continues instead of restarting
            back = f"{key}@{d['hash']}"
            again = [p for p in archive if p.metric == back]
            if again:
                archive = [p for p in archive if p.metric != back]
                for p in again:
                    q = p._replace(metric=key)
                    by_series.setdefault((key, q.grain, q.breakdown), []).append(q)
                versions.append(
                    {"metric": key, "definition": d["hash"], "reinstated": as_of.isoformat()}
                )
        elif Decimal(before["scale"]) != Decimal(d["scale"]):
            # a pure scale change: one series, the stored points converted exactly
            ratio = Decimal(d["scale"]) / Decimal(before["scale"])
            for k in [k for k in by_series if k[0] == key]:
                by_series[k] = [
                    p if p.value is None else p._replace(value=p.value * ratio)
                    for p in by_series[k]
                ]
            conversions.append(
                {"metric": key, "on": as_of.isoformat(), "from": before["scale"], "to": d["scale"]}
            )
    out: list[Point] = []
    hashes: dict[str, str] = {}
    unmapped: list[list[str]] = []
    with Executor(ctx, info, source) as ex:
        for key, m in metrics_on(info.spec, source):
            flow = m.get("kind", "flow") == "flow"
            for qkey, sql in sorted(m.get("queries", {}).items()):
                grain, _, bkey = qkey.partition(":")
                if grain in ("backfill", "history_start"):
                    continue
                hk = f"{key}|{qkey}"
                hashes[hk] = _qhash(sql)
                same = (
                    prev_hashes.get(hk) == hashes[hk]
                    and info.prev_run_date is not None
                    and (key, "*") not in refetch
                    and (key, bkey) not in refetch
                )
                if flow:
                    start = (
                        periods.refresh_from(info.prev_run_date, run_date, grain)  # type: ignore[arg-type]
                        if same
                        else window
                    )
                    kept = (
                        [p for p in by_series.get((key, grain, bkey), []) if p.period < start]
                        if same
                        else []
                    )
                    rows = ex.run(sql, {"from": start, "to": run_date})
                    fresh = [_scaled(_flow_point(key, grain, bkey, r), m) for r in rows]
                    out += kept + [p for p in fresh if p.period < run_date and p.period >= start]
                else:
                    # a snapshot keeps its history unless its definition (meaning) changed: that
                    # case was archived above as an earlier version. A change in the rendered
                    # SQL alone (a compiler fix) never restarts it (decided 5 Nov 2026).
                    kept = by_series.get((key, "day", bkey), [])
                    if any(p.period == as_of for p in kept):  # captured once, never overwritten
                        out += kept
                        continue
                    rows = ex.run(sql, {"as_of": as_of})
                    out += [p for p in kept if p.period != as_of]
                    out += [_scaled(_snap_point(key, bkey, as_of, r), m) for r in rows]
            um = m.get("unmapped")
            if um and um.get("sql"):
                start = periods.period_start(
                    as_of - dt.timedelta(days=periods.LOOKBACK_DAYS), "month"
                )
                for r in ex.run(um["sql"], {"from": start, "to": run_date}):
                    unmapped.append([key, _iso(r[0]), str(r[1]), str(dec(r[2]))])
        queries, ms = ex.save_log()
    write_points(info.files.source_points(source), out)
    write_points(info.files.archive_points(source), archive)
    write_json(
        info.files.source_meta(source),
        {
            **(read_json(info.files.source_meta(source)) or {}),
            "status": "read",
            "last_good": as_of.isoformat(),
            "query_hashes": hashes,
            "definitions": defs,
            "backfilled": backfilled_before(info, source),  # the back-fill step adds tonight's
            "versions": versions,
            "conversions": conversions,
            "unmapped": sorted(unmapped),
        },
    )
    return SourceOutcome(True, {"points": len(out), "queries": queries, "ms": ms})


def carry(
    info: RunInfo,
    source: str,
    *,
    stale: bool,
    prev_meta: dict[str, Any] | None = None,
    prev: list[Point] | None = None,
) -> SourceOutcome:
    """The previous run's points for a source not read in this run: stale when it failed, plain
    carried when a manual run is scoped to another source."""
    if prev_meta is None:
        prev_meta = (read_json(info.prev.source_meta(source)) if info.prev else None) or {}
    if prev is None:
        prev = previous_points(info, source)
    write_points(info.files.source_points(source), prev)
    write_points(info.files.archive_points(source), previous_archive(info, source))
    write_json(
        info.files.source_meta(source),
        {
            "status": "stale" if stale else "carried",
            "last_good": prev_meta.get("last_good"),
            "query_hashes": prev_meta.get("query_hashes", {}),
            "definitions": prev_meta.get("definitions", {}),
            "versions": prev_meta.get("versions", []),
            "conversions": prev_meta.get("conversions", []),
            "unmapped": prev_meta.get("unmapped", []) if not stale else [],
            "watermarks": prev_meta.get("watermarks", {}),
        },
    )
    return SourceOutcome(not stale, {"points": len(prev), "carried": True})


def _iso(v: Any) -> str:
    return v.isoformat()[:10] if hasattr(v, "isoformat") else str(v)[:10]


def _as_date(v: Any) -> dt.date:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def _flow_point(metric: str, grain: str, bkey: str, r: tuple[Any, ...]) -> Point:
    if bkey:
        return Point(metric, grain, bkey, _bv(r[1]), _as_date(r[0]), dec(r[2]))
    return Point(metric, grain, "", None, _as_date(r[0]), dec(r[1]))


def _snap_point(metric: str, bkey: str, as_of: dt.date, r: tuple[Any, ...]) -> Point:
    if bkey:
        return Point(metric, "day", bkey, _bv(r[0]), as_of, dec(r[1]))
    return Point(metric, "day", "", None, as_of, dec(r[0]))


def _bv(v: Any) -> str:
    return "" if v is None else str(v)


# --- step 2b: back-fill (first run, activation, a new version) ------------------------------


def backfill(ctx: EngineContext, info: RunInfo, source: str) -> SourceOutcome:
    """Weekly as-of points for snapshot metrics computable from event dates: one grouped query
    per metric and breakdown, stored once with reconstructed=true (5 Nov 2026)."""
    out: list[Point] = []
    done: dict[str, Any] = {}
    finished: dict[str, str] = {}  # metric -> definition hash, recorded only once written
    due = set(due_backfill(info, source))
    if not due:  # recorded in the run's steps (the ledger): nothing due tonight
        write_points(info.files.backfill_points(source), out)
        return SourceOutcome(True, {"metrics": done, "queries": 0, "ms": 0, "note": "nothing due"})
    with Executor(ctx, info, source) as ex:
        for key, m in metrics_on(info.spec, source):
            bf = m.get("backfill") or {}
            q = m.get("queries", {})
            if key not in due or not bf.get("possible") or "history_start" not in q:
                continue
            starts = [_as_date(v) for v in ex.run(q["history_start"])[0] if v is not None]
            first, last = periods.backfill_points(info.run_date, min(starts) if starts else None)
            if first > last:
                finished[key] = str(m.get("definition_hash", ""))  # nothing to reconstruct
                continue
            n = 0
            for qkey, sql in sorted(q.items()):
                if not qkey.startswith("backfill:"):
                    continue
                bkey = qkey.split(":", 1)[1]
                # M15.1: a quarter of Sundays per statement; points are written sorted, so the
                # file is the same as one statement's
                for c_from, c_to in periods.backfill_chunks(first, last):
                    for r in ex.run(sql, {"from": c_from, "to": c_to}):
                        if bkey:
                            p = Point(key, "day", bkey, _bv(r[1]), _as_date(r[0]), dec(r[2]), True)
                        else:
                            p = Point(key, "day", "", None, _as_date(r[0]), dec(r[1]), True)
                        out.append(_scaled(p, m))
                        n += 1
            finished[key] = str(m.get("definition_hash", ""))
            done[key] = {
                "from": first.isoformat(),
                "to": last.isoformat(),
                "columns": bf.get("as_of_columns", []),
                "points": n,
            }
        queries, ms = ex.save_log()
    write_points(info.files.backfill_points(source), out)
    meta = read_json(info.files.source_meta(source)) or {}
    write_json(
        info.files.source_meta(source),
        {**meta, "backfilled": {**(meta.get("backfilled") or {}), **finished}},
    )
    return SourceOutcome(True, {"metrics": done, "queries": queries, "ms": ms})


def files_exist(info: RunInfo, source: str) -> bool:
    return Path(info.files.source_points(source)).exists()
