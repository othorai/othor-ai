# SPDX-License-Identifier: Apache-2.0
"""Today's value for each drafted metric (docs/01 §4: "the engine computes today's value").

Every draft is compiled with the M3 compiler, together with the records in effect, and its SQL
runs read-only on its source for the metric's last complete period (October on 4 Nov for a monthly
metric; last week; yesterday). Snapshot metrics are taken as of the period's last day. Derived
metrics are computed in an in-memory DuckDB from their components' values for the same period.
No number here comes from a model.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from othor.connectors.sql.engine import SqlCreds, make_engine
from othor.connectors.sql.query_log import QueryLog
from othor.ontology.compiler.build import compile_records
from othor.ontology.compiler.catalog import Catalog
from othor.ontology.compiler.render import CompileError

Records = dict[tuple[str, str], dict[str, Any]]


class NotRead(RuntimeError):
    """A Google Sheets tab not yet read into the analytics store: no value yet."""


def last_period(grain: str, today: dt.date) -> tuple[dt.date, dt.date]:
    """[start, end) of the last complete period before `today`."""
    if grain == "day":
        return today - dt.timedelta(days=1), today
    if grain == "week":
        this_monday = today - dt.timedelta(days=today.weekday())
        return this_monday - dt.timedelta(days=7), this_monday
    first = today.replace(day=1)
    prev = (first - dt.timedelta(days=1)).replace(day=1)
    return prev, first


@dataclass
class Computed:
    value: Decimal | None
    state: str  # ok · no_data · not_determinable · error
    start: dt.date
    end: dt.date
    grain: str
    sql_hash: str | None = None
    note: str | None = None
    sources: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {
            "value": None if self.value is None else str(self.value),
            "state": self.state,
            "period": {
                "start": self.start.isoformat(),
                "end": self.end.isoformat(),
                "grain": self.grain,
            },
            "as_of": (self.end - dt.timedelta(days=1)).isoformat(),
            "sql_hash": self.sql_hash,
            "note": self.note,
            "sources": self.sources,
        }


def compile_drafts(
    base: Records, drafts: Records, cat: Catalog, company: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Compile each draft metric with what it needs; return (spec of the good ones, errors)."""
    errors: dict[str, str] = {}
    good: Records = {}
    metrics = {k: b for (t, k), b in drafts.items() if t == "metric"}
    others = {(t, k): b for (t, k), b in drafts.items() if t != "metric"}
    for key in sorted(metrics):
        trial = {**base, **others, **{("metric", k): b for k, b in metrics.items()}}
        # compile only this metric and the metrics it refers to
        keep = _deps(key, metrics)
        trial = {tk: b for tk, b in trial.items() if tk[0] != "metric" or tk[1] in keep}
        try:
            compile_records(trial, cat, company, 0)
            good[("metric", key)] = metrics[key]
        except CompileError as e:
            errors[key] = str(e)
    spec = compile_records({**base, **others, **good}, cat, company, 0)
    return spec, errors


# the order breakdowns are offered in, and how many a metric gets (Sweep tests each one)
BREAKDOWN_ORDER = ("plant", "region", "segment", "customer", "product_family", "channel")
MAX_BREAKDOWNS = 4


def attach_breakdowns(
    base: Records, drafts: Records, cat: Catalog, company: dict[str, Any]
) -> dict[str, list[str]]:
    """For each active metric drafted without breakdowns, the Ontology's breakdowns its tables can
    reach (up to 4), as the compiler decides: each is tried and kept only where the metric still
    compiles (12 Nov 2026; Indigo Thread's metrics had none, so Sweep was nearly blind)."""
    every = {**base, **drafts}
    bkeys = sorted(
        {k for (t, k) in every if t == "breakdown"},
        key=lambda k: (BREAKDOWN_ORDER.index(k) if k in BREAKDOWN_ORDER else 99, k),
    )
    metrics = {
        k: b
        for (t, k), b in drafts.items()
        if t == "metric"
        and b.get("status") == "active"
        and not b.get("breakdowns")
        and b.get("measure")
    }
    got: dict[str, list[str]] = {k: [] for k in metrics}
    for bk in bkeys:
        trial = {
            k: {**b, "breakdowns": [*got[k], bk]}
            for k, b in metrics.items()
            if len(got[k]) < MAX_BREAKDOWNS
        }
        if not trial:
            break
        _, errors = compile_drafts(
            base, {**drafts, **{("metric", k): b for k, b in trial.items()}}, cat, company
        )
        for k in trial:
            if k not in errors:
                got[k].append(bk)
    return {k: v for k, v in got.items() if v}


def _deps(key: str, metrics: dict[str, dict[str, Any]], seen: set[str] | None = None) -> set[str]:
    from othor.ontology.types import walk

    seen = seen or set()
    if key in seen or key not in metrics:
        return seen
    seen.add(key)
    body = metrics[key]
    for f in [body.get("formula"), *body.get("breakdown_formulas", {}).values()]:
        if f:
            for n in walk(f):
                if "metric" in n:
                    _deps(n["metric"], metrics, seen)
    return seen


class Runner:
    """Runs base-metric SQL on its source (read-only) and derived SQL in DuckDB, memoised."""

    def __init__(
        self,
        spec: dict[str, Any],
        cat: Catalog,
        creds_for: Callable[[str], SqlCreds | None],
        log: QueryLog,
        duck_path: Any = None,
        timeout_s: int | None = None,
    ) -> None:
        self.spec, self.cat, self.creds_for, self.log = spec, cat, creds_for, log
        self.timeout_s = timeout_s
        self.duck_path = duck_path
        self.memo: dict[tuple[str, dt.date, dt.date], Computed] = {}
        self.engines: dict[str, sa.Engine] = {}
        self.ran: list[tuple[str, int]] = []  # (source name, index into log.entries)

    def close(self) -> None:
        for e in self.engines.values():
            e.dispose()

    def value(self, key: str, today: dt.date) -> Computed:
        m = self.spec["metrics"][key]
        start, end = last_period(m.get("grain") or "month", today)
        return self.at(key, start, end, m.get("grain") or "month")

    def at(self, key: str, start: dt.date, end: dt.date, grain: str) -> Computed:
        k = (key, start, end)
        if k not in self.memo:
            self.memo[k] = self._compute(key, start, end, grain)
        return self.memo[k]

    def _compute(self, key: str, start: dt.date, end: dt.date, grain: str) -> Computed:
        m = self.spec["metrics"].get(key)
        if m is None or m.get("status") != "active":
            return Computed(None, "error", start, end, grain, note="needs a source")
        if m.get("type") == "derived":
            return self._derived(key, m, start, end, grain)
        flow = m.get("kind", "flow") == "flow"
        q = m["queries"].get(f"{grain}:" if flow else "as_of:")
        if q is None:  # a flow metric asked at another grain than its own
            q = m["queries"].get(f"{grain}:")
        as_of = end - dt.timedelta(days=1)
        params = {"from": start, "to": end} if flow else {"as_of": as_of}
        h = hashlib.sha256(q.encode()).hexdigest()
        rows = self._run(m, q, params)
        vals = [r[-1] for r in rows if not flow or _as_date(r[0]) == start]
        v = vals[0] if vals else None
        if v is None:
            if flow and m.get("agg") in ("sum", "count"):  # nothing in the period: zero
                return Computed(
                    Decimal(0), "ok", start, end, grain, h, sources=list(m.get("sources", []))
                )
            return Computed(
                None, "no_data", start, end, grain, h, sources=list(m.get("sources", []))
            )
        return Computed(
            Decimal(str(v)), "ok", start, end, grain, h, sources=list(m.get("sources", []))
        )

    def _run(self, m: dict[str, Any], sql: str, params: dict[str, Any]) -> list[Any]:
        if m["dialect"] == "duckdb":
            import duckdb

            if not self.duck_path:
                raise NotRead(m.get("source", ""))
            con = (
                duckdb.connect(str(self.duck_path), read_only=True)
                if self.duck_path
                else duckdb.connect()
            )
            try:
                p = {k: v.isoformat() if isinstance(v, dt.date) else v for k, v in params.items()}
                used = {k: v for k, v in p.items() if f"${k}" in sql}
                return list(con.execute(sql, used).fetchall())
            finally:
                con.close()
        source = m["source"]
        if source not in self.engines:
            creds = self.creds_for(source)
            if creds is None:
                raise CompileError(f"no credentials for {source}")
            self.engines[source] = (
                make_engine(creds, self.timeout_s) if self.timeout_s else make_engine(creds)
            )
        with self.engines[source].connect() as c:
            used = {k: v for k, v in params.items() if f":{k}" in sql}
            rows = self.log.run(c, sql, used)
        self.ran.append((source, len(self.log.entries) - 1))
        return rows

    def _derived(
        self, key: str, m: dict[str, Any], start: dt.date, end: dt.date, grain: str
    ) -> Computed:
        import duckdb

        q = m["queries"].get(f"{grain}:")
        if q is None:
            return Computed(None, "error", start, end, grain, note=f"no {grain} series")
        con = duckdb.connect()
        try:
            con.execute(
                "CREATE TABLE series (metric TEXT, grain TEXT, breakdown TEXT, "
                "breakdown_value TEXT, period DATE, value DECIMAL(24,6))"
            )
            for comp in m["components"]:
                cm = self.spec["metrics"][comp]
                snapshot = cm.get("kind") == "snapshot"
                c = self.at(comp, start, end, grain)
                if c.value is None:
                    continue
                period = (end - dt.timedelta(days=1)) if snapshot else start
                con.execute(
                    "INSERT INTO series VALUES (?, ?, '', NULL, ?, ?)",
                    [comp, "day" if snapshot else grain, period.isoformat(), str(c.value)],
                )
            rows = con.execute(q).fetchall()
        finally:
            con.close()
        row = next((r for r in rows if _as_date(r[0]) == start and r[1] is None), None)
        h = hashlib.sha256(q.encode()).hexdigest()
        if row is None:
            return Computed(None, "no_data", start, end, grain, h)
        if row[3] == "not_determinable" or row[2] is None:
            return Computed(None, "not_determinable", start, end, grain, h)
        return Computed(
            Decimal(str(row[2])), "ok", start, end, grain, h, sources=list(m.get("sources", []))
        )


def _as_date(v: Any) -> dt.date | None:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        return dt.date.fromisoformat(v[:10])
    return None
