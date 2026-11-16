# SPDX-License-Identifier: Apache-2.0
"""The counts behind helpful questions' suggested answers (v0.12, docs/plans/helpful-questions.md):
how many order numbers on one side are on the other, a status column's values and counts, the
value a limit column holds, how many rows and keys a table has a day. Built with sqlglot from
scanned names (`Scope.column` refuses personal columns, `recheck` refuses anything but one
read-only SELECT), run read-only through each source's query log, after the run's own steps.

Only counts and shares leave this module, plus a status column's values (needed to say which
status means active) and a limit column's values (the limit itself): never a personal column,
never a row to a model. Keys read to count the overlap are counted in memory and dropped.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping
from typing import Any

from sqlglot import exp

from othor.engine.context import EngineContext
from othor.engine.sources import Executor, RunInfo
from othor.ontology import suggest as sg
from othor.ontology.compiler.build import _from
from othor.ontology.compiler.catalog import Catalog, TableInfo
from othor.ontology.compiler.guard import recheck
from othor.ontology.compiler.render import Rules, Scope, placeholder, to_sql

log = logging.getLogger(__name__)
KEYS_CAP = 200_000  # distinct keys read per side, at most
YEAR = 365


def _scope(name: str, cat: Catalog, t: TableInfo) -> Scope:
    return Scope(name, cat, t, Rules(), t.dialect)


def _sql(name: str, scope: Scope, sel: exp.Select) -> str:
    sql = to_sql(sel, scope.dialect)
    recheck(name, sql, scope.dialect, scope)
    return sql


def _since(scope: Scope, date: str, sel: exp.Select) -> exp.Select:
    d = exp.cast(scope.column({"col": date}, where="date", convert=False), "DATE")
    return sel.where(exp.GTE(this=d, expression=exp.cast(placeholder("from"), "DATE")))


def _distinct_keys(cat: Catalog, t: TableInfo, col: str, date: str | None) -> str:
    sc = _scope("suggest reconcile", cat, t)
    k = sc.column({"col": col}, where="key", convert=False)
    sel = _from(sc, exp.select(exp.alias_(k, "key", quoted=True)).distinct())
    sel = sel.where(exp.Not(this=exp.Is(this=k.copy(), expression=exp.Null())))
    if date:
        sel = _since(sc, date, sel)
    return _sql("suggest reconcile", sc, sel.limit(KEYS_CAP))


def _value_counts(cat: Catalog, t: TableInfo, col: str, where: str) -> str:
    sc = _scope(f"suggest {where}", cat, t)
    c = sc.column({"col": col}, where=where, convert=False)
    n = exp.Count(this=exp.Star())
    sel = _from(sc, exp.select(exp.alias_(c, "v", quoted=True), exp.alias_(n, "n", quoted=True)))
    sel = sel.group_by(c.copy()).order_by(exp.Ordered(this=n.copy(), desc=True)).limit(50)
    return _sql(f"suggest {where}", sc, sel)


def _rows_keys(cat: Catalog, t: TableInfo, col: str, date: str) -> str:
    sc = _scope("suggest distribution", cat, t)
    k = sc.column({"col": col}, where="key", convert=False)
    d = exp.cast(sc.column({"col": date}, where="date", convert=False), "DATE")
    sel = _from(
        sc,
        exp.select(
            exp.alias_(exp.Count(this=exp.Star()), "n", quoted=True),
            exp.alias_(exp.Count(this=exp.Distinct(expressions=[k])), "k", quoted=True),
            exp.alias_(exp.Min(this=d.copy()), "lo", quoted=True),
            exp.alias_(exp.Max(this=d.copy()), "hi", quoted=True),
        ),
    )
    return _sql("suggest distribution", sc, _since(sc, date, sel))


def _date(v: Any) -> dt.date | None:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


class _Runner:
    """One executor per source, opened on first use; every query logged on the run."""

    def __init__(self, ctx: EngineContext, info: RunInfo) -> None:
        self.ctx, self.info = ctx, info
        self.open: dict[str, Executor] = {}

    def run(self, source: str, sql: str, params: Mapping[str, Any] | None = None) -> list[Any]:
        if source not in self.open:
            self.open[source] = Executor(self.ctx, self.info, source, purpose="help")
        return self.open[source].run(sql, dict(params or {}))

    def close(self) -> None:
        for ex in self.open.values():
            try:
                ex.save_log()
            finally:
                ex.__exit__()


def facts_for(
    ctx: EngineContext,
    info: RunInfo,
    needs: set[str],
    cat: Catalog,
    records: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """{need: {ask key: counts}} for the needs asked tonight. A source that can't be read
    leaves its asks out (the question then says what it can)."""
    spec = info.spec
    since = {"from": info.run_date - dt.timedelta(days=YEAR)}
    r = _Runner(ctx, info)
    out: dict[str, dict[str, Any]] = {n: {} for n in needs}
    try:
        if "reconcile" in needs:
            for kp in sg.key_pairs(cat, spec):
                try:
                    left = {
                        str(x[0]).strip()
                        for x in r.run(
                            kp.left.source_name,
                            _distinct_keys(cat, kp.left, kp.lcol, kp.date),
                            since,
                        )
                    }
                    right = {
                        str(x[0]).strip()
                        for x in r.run(
                            kp.right.source_name, _distinct_keys(cat, kp.right, kp.rcol, None)
                        )
                    }
                except Exception as e:
                    log.warning("suggest reconcile skipped: %s", type(e).__name__)
                    continue
                left.discard("")
                out["reconcile"][kp.key] = {"left": len(left), "found": len(left & right)}
        if "entity" in needs:
            for ea in sg.entity_asks(cat, spec, records):
                try:
                    rows = r.run(
                        ea.table.source_name, _value_counts(cat, ea.table, ea.status, "status")
                    )
                except Exception as e:
                    log.warning("suggest entity skipped: %s", type(e).__name__)
                    continue
                out["entity"][ea.key] = _counted(rows)
        if "distribution" in needs:
            for da in sg.distribution_asks(cat, spec, records):
                try:
                    (n, k, lo, hi), *_ = r.run(
                        da.table.source_name,
                        _rows_keys(cat, da.table, da.column, da.date),
                        since,
                    )
                except Exception as e:
                    log.warning("suggest distribution skipped: %s", type(e).__name__)
                    continue
                a, b = _date(lo), _date(hi)
                days = (b - a).days + 1 if a and b else 0
                out["distribution"][da.key] = {
                    "rows": int(n or 0),
                    "keys": int(k or 0),
                    "days": days,
                }
        if "threshold" in needs:
            for ta in sg.threshold_asks(cat, spec):
                try:
                    rows = r.run(
                        ta.table.source_name, _value_counts(cat, ta.table, ta.limit, "limit")
                    )
                except Exception as e:
                    log.warning("suggest threshold skipped: %s", type(e).__name__)
                    continue
                out["threshold"][ta.key] = _counted(rows)
    finally:
        r.close()
    return out


def _counted(rows: list[Any]) -> dict[str, Any]:
    """{"rows": total, "values": [[value, count], ...]}, most common first, then by value."""
    pairs: list[tuple[str, int]] = [(str(v), int(n)) for v, n in rows if v is not None]
    pairs.sort(key=lambda x: (-x[1], x[0]))
    return {"rows": sum(int(n) for _v, n in rows), "values": [list(p) for p in pairs]}
