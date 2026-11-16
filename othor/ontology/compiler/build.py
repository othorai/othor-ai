# SPDX-License-Identifier: Apache-2.0
"""Ontology records + scanned catalog → spec document (docs/04 §1).

The spec is plain JSON. Base metrics carry SQL for their source's dialect (they run in place on
SQL sources, docs/02); Sheets metrics and derived metrics carry DuckDB SQL over the extracted
tabs and the `series` table. Everything is ordered by key so the canonical JSON, and its hash,
depends only on the records and the catalog.
"""

from __future__ import annotations

import importlib.metadata
import re
from typing import Any

from sqlglot import exp

from othor.ontology.compiler.catalog import KIND_NAME, Catalog, TableInfo
from othor.ontology.compiler.guard import recheck
from othor.ontology.compiler.render import (
    FACT,
    CompileError,
    PersonalColumnRejected,
    Rules,
    Scope,
    aggregate,
    check_use,
    ident,
    lit,
    period,
    placeholder,
    prefix_case,
    render,
    table_expr,
    to_sql,
)
from othor.ontology.compiler.terms import terms as signed_terms
from othor.ontology.types import walk

COMPILER_VERSION = "m16.2"  # M16.1: area and signed terms; M16.2: per_period
SPEC_FORMAT = 1
GRAINS = ("day", "week", "month")
EVIDENCE_LIMIT = 50
CALENDAR = "cal"  # the back-fill calendar CTE: one row per as-of date
BACKFILL_STEP_DAYS = 7  # weekly as-of points (Sundays), decided 5 Nov 2026
BACKFILL_MAX_WEEKS = 104
DEFAULT_LOOKBACK_DAYS = 35
SERIES_TABLE = "series"  # metric, grain, breakdown ('' = total), breakdown_value, period, value
UNMAPPED_THRESHOLD = "0.01"
UNMAPPED_QUESTION = "Which plant does item code {prefix} belong to?"
DETECTOR_DEFAULTS: dict[str, Any] = {
    "fdr_q": "0.01",
    "materiality_inr": "500000",
    "breakdown_cap": 50,
    "net_hiding_gross": {"min_periods": 2, "offset_ratio": "3"},
    "changepoint": {"min_days": 28, "model": "l2"},
    "forecast_breach": {"min_weeks": 8, "interval": "0.8"},
    "rising_variance": {"min_weeks": 16, "recent_weeks": 4, "prior_weeks": 12},
    "broken_relationship": {"min_weeks": 20},
    "lead_lag": {"min_weeks": 26, "max_lag_weeks": 8},
    "concentration": {"min_periods": 3, "top_n": 4, "share": "0.5"},
    "missing_records": {"min_events": 6, "gap_over_median": "2", "quantile": "0.95"},
    "process_artefact": {"min_events": 200},
    "cross_source": {"tolerance": "0.005"},
    "bimodal_average": {"min_values": 100},
}
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _rules(records: dict[tuple[str, str], dict[str, Any]], cat: Catalog) -> Rules:
    rules = Rules()
    for (typ, key), body in sorted(records.items()):
        if typ == "vocabulary" and body.get("kind") == "prefix_map":
            rules.vocabularies[key] = body
        if typ == "alias" and body.get("source"):
            rules.aliases.setdefault(body["source"], {})[body["term"]] = body["means"]
        if typ != "rule":
            continue
        t = None
        if body.get("table"):
            ref = body["table"]
            t = cat.table(ref["source"], ref["schema"], ref["table"])
            if t is None:
                continue  # a rule on a table that isn't scanned (yet) doesn't apply
        kind = body["kind"]
        if kind == "allocation":
            rules.allocations[(body["metric"], body["breakdown"])] = body
            continue
        if kind == "unit_conversion" and t is not None:
            rules.conversions.setdefault((t.stable_key, body["column"].lower()), []).append(body)
        elif kind == "filter" and t is not None:
            rules.filters.setdefault(t.stable_key, []).append(body["expr"])
        elif kind == "join" and t is not None:
            if body.get("vocabulary"):
                rules.vocab_joins.setdefault(t.stable_key, []).append(body)
            elif body.get("to"):
                rules.joins[(t.stable_key, body["to"]["table"])] = body
    return rules


def _table(cat: Catalog, ref: dict[str, Any], metric: str) -> TableInfo:
    t = cat.table(ref["source"], ref["schema"], ref["table"])
    if t is None:
        raise CompileError(
            f"{metric}: {ref['schema']}.{ref['table']} on {ref['source']} isn't scanned"
        )
    return t


def _breakdown(
    scope: Scope, key: str, bd: dict[str, Any], vocabs: dict[str, Any]
) -> tuple[exp.Expr, dict[str, Any] | None]:
    """The SQL for one breakdown on this metric's tables, and the prefix map it used (if any)."""
    where = f"breakdown {key}"
    if bd.get("vocabulary"):
        if scope.fact.column(bd["column"]) is not None:  # the table has the value itself
            return scope.column({"col": bd["column"]}, where=where), None
        for rule in scope.rules.vocab_joins.get(scope.fact.stable_key, []):
            if rule["vocabulary"] == bd["vocabulary"]:
                vocab = vocabs.get(bd["vocabulary"])
                if vocab is None:
                    raise CompileError(f"{scope.metric}: vocabulary {bd['vocabulary']} is missing")
                code = scope.column({"col": rule["column"]}, where=where, convert=False)
                return prefix_case(code, vocab), {
                    **vocab,
                    "column": rule["column"],
                    "key": bd["vocabulary"],
                }
        raise CompileError(
            f"{scope.metric}: {scope.fact.name} has no {bd['column']} and no join rule "
            f"through vocabulary {bd['vocabulary']}"
        )
    if scope.fact.column(bd["column"]) is not None:  # the metric's own table has it
        return scope.column({"col": bd["column"]}, where=where), None
    candidates = [{"table": bd.get("table"), "column": bd["column"]}, *bd.get("alternatives", [])]
    for c in candidates:
        if c.get("table") in (None, scope.fact.name):
            if scope.fact.column(c["column"]) is not None:
                return scope.column({"col": c["column"]}, where=where), None
            continue
        if (scope.fact.stable_key, c["table"]) in scope.rules.joins:
            return scope.column({"col": c["column"], "table": c["table"]}, where=where), None
    raise CompileError(f"{scope.metric}: breakdown {key} can't be reached from {scope.fact.name}")


def _where(scope: Scope, metric: dict[str, Any]) -> list[exp.Expr]:
    conds: list[exp.Expr] = []
    for f in scope.rules.filters.get(scope.fact.stable_key, []):
        conds.append(render(f, scope, where="table rule"))
    for i, f in enumerate(metric.get("filters", [])):
        conds.append(render(f, scope, where=f"filter {i + 1}"))
    return conds


def _from(scope: Scope, sel: exp.Select) -> exp.Select:
    sel = sel.from_(table_expr(scope.fact, FACT))
    for alias, t in scope.joined.items():
        sel = sel.join(table_expr(t, alias), on=scope.join_on[alias], join_type="left")
        for f in scope.rules.filters.get(t.stable_key, []):  # a joined table's own rules
            sel = sel.where(render(_qualify(f, alias), scope, where=f"rule on {t.name}"))
    return sel


def _qualify(node: Any, table: str) -> Any:
    if isinstance(node, dict):
        if "col" in node and "table" not in node:
            return {**node, "table": table}
        return {k: _qualify(v, table) for k, v in node.items()}
    if isinstance(node, list):
        return [_qualify(x, table) for x in node]
    return node


def _base_sql(
    m: str,
    body: dict[str, Any],
    t: TableInfo,
    cat: Catalog,
    rules: Rules,
    bds: dict[str, Any],
    vocabs: dict[str, Any],
    dialect: str,
) -> dict[str, Any]:
    out: dict[str, Any] = {"queries": {}, "unmapped": None}
    measure = body["measure"]
    flow = body.get("kind", "flow") == "flow"
    keys: list[str | None] = [None, *sorted(body.get("breakdowns", []))]
    grains = GRAINS if flow else ("as_of",)
    for grain in grains:
        for bkey in keys:
            scope = Scope(m, cat, t, rules, dialect)
            value = render(measure["expr"], scope, where="measure") if measure.get("expr") else None
            cols: list[exp.Expr] = []
            group: list[exp.Expr] = []
            if flow:
                date = scope.column(_date_ref(body["date"]), where="date", convert=False)
                p = period(date, grain, dialect)
                cols.append(exp.alias_(p, "period", quoted=True))
                group.append(p.copy())
            bexpr = None
            if bkey is not None and flow and (m, bkey) in rules.allocations:
                sql = _allocation_sql(m, body, t, cat, rules, bds, vocabs, dialect, grain, bkey)
                out["queries"][f"{grain}:{bkey}"] = sql
                continue
            if bkey is not None:
                if bkey not in bds:
                    raise CompileError(f"{m}: breakdown {bkey} isn't in the Ontology")
                bexpr, vmap = _breakdown(scope, bkey, bds[bkey], vocabs)
                cols.append(exp.alias_(bexpr, "breakdown_value", quoted=True))
                group.append(bexpr.copy())
                if vmap is not None and out["unmapped"] is None and flow:
                    out["unmapped"] = {
                        "breakdown": bkey,
                        "vocabulary": vmap["key"],
                        "column": vmap["column"],
                        "entries": vmap["entries"],
                        "other": vmap.get("other", "Unmapped"),
                    }
            conds = _where(scope, body)
            if flow:
                conds += [
                    exp.GTE(this=date.copy(), expression=exp.cast(placeholder("from"), "DATE")),
                    exp.LT(this=date.copy(), expression=exp.cast(placeholder("to"), "DATE")),
                ]
            if measure["agg"] == "last":
                sel = _last_query(scope, body, value, bexpr, conds)
            else:
                sel = exp.select(
                    *cols, exp.alias_(aggregate(measure["agg"], value), "value", quoted=True)
                )
                sel = _from(scope, sel)
                for c in conds:
                    sel = sel.where(c)
                if group:
                    sel = sel.group_by(*group)
            sql = to_sql(sel, dialect)
            recheck(m, sql, dialect, scope)
            out["queries"][f"{grain}:{bkey or ''}"] = sql
    # evidence: bounded rows, explicit non-personal columns only
    scope = Scope(m, cat, t, rules, dialect)
    ev_cols = [
        exp.alias_(scope.column({"col": c}, where=f"evidence {c}", convert=False), c, quoted=True)
        for c in body.get("evidence", [])
    ]
    if ev_cols:
        ev = _from(scope, exp.select(*ev_cols))
        for c in _where(scope, body):
            ev = ev.where(c)
        if flow:
            date = scope.column(_date_ref(body["date"]), where="date", convert=False)
            ev = ev.where(exp.GTE(this=date, expression=exp.cast(placeholder("from"), "DATE")))
            ev = ev.order_by(exp.Ordered(this=date.copy(), desc=True))
        sql = to_sql(ev.limit(EVIDENCE_LIMIT), dialect)
        recheck(m, sql, dialect, scope)
        out["evidence"] = sql
    if out["unmapped"]:
        out["unmapped"]["sql"] = _unmapped_sql(m, body, t, cat, rules, out["unmapped"], dialect)
    bf = _backfill(body, t)
    if bf and bf.get("possible"):
        for bkey in keys:
            out["queries"][f"backfill:{bkey or ''}"] = _backfill_sql(
                m, body, t, cat, rules, bds, vocabs, dialect, bkey
            )
        out["queries"]["history_start"] = _history_start_sql(m, t, cat, rules, bf, dialect)
    return out


ALLOC_W, ALLOC_T = "alloc_w", "alloc_t"


def _allocation_sql(
    m: str,
    body: dict[str, Any],
    t: TableInfo,
    cat: Catalog,
    rules: Rules,
    bds: dict[str, Any],
    vocabs: dict[str, Any],
    dialect: str,
    grain: str,
    bkey: str,
) -> str:
    """A flow metric spread over a breakdown it doesn't carry (decided 4 Nov): each row's value
    × the weight of each breakdown value among the rows of the weight table it's linked to
    (a credit note × its invoice's lines by plant), per period when the rule has a grain (a
    rebate × the customer's line revenue by plant in that month). No weight → "Unmapped"."""
    rule = rules.allocations[(m, bkey)]
    if bkey not in bds:
        raise CompileError(f"{m}: breakdown {bkey} isn't in the Ontology")
    wt = _table(cat, rule["to"], f"{m} allocation")
    ws = Scope(f"{m} allocation", cat, wt, rules, wt.dialect)
    (fkey, wkey), *_ = rule["on"]
    wk = ws.column(_date_ref(wkey), where="allocation key", convert=False)
    wb, _vm = _breakdown(ws, bkey, bds[bkey], vocabs)
    weight = ws.column({"col": rule["weight"]}, where="allocation weight")
    per = rule.get("grain")
    wcols: list[exp.Expr] = [exp.alias_(wk, "k", quoted=True)]
    wgroup: list[exp.Expr] = [wk.copy()]
    if per:
        wd = ws.column(_date_ref(rule["date"]), where="allocation date", convert=False)
        wp = period(wd, per, dialect)
        wcols.append(exp.alias_(wp, "p", quoted=True))
        wgroup.append(wp.copy())
    wcols += [exp.alias_(wb, "b", quoted=True), exp.alias_(exp.Sum(this=weight), "wt", quoted=True)]
    wgroup.append(wb.copy())
    wsel = _from(ws, exp.select(*wcols))
    for f in rules.filters.get(wt.stable_key, []):
        wsel = wsel.where(render(f, ws, where="table rule"))
    wsel = wsel.group_by(*wgroup)
    tkeys = [exp.column("k", quoted=True)] + ([exp.column("p", quoted=True)] if per else [])
    tsel = (
        exp.select(
            *[k.copy() for k in tkeys],
            exp.alias_(exp.Sum(this=exp.column("wt", quoted=True)), "tot", quoted=True),
        )
        .from_(exp.Table(this=ident(ALLOC_W)))
        .group_by(*[k.copy() for k in tkeys])
    )
    scope = Scope(m, cat, t, rules, dialect)
    value = render(body["measure"]["expr"], scope, where="measure")
    date = scope.column(_date_ref(body["date"]), where="date", convert=False)
    p = period(date, grain, dialect)
    fk = scope.column(_date_ref(fkey), where="allocation key", convert=False)
    on_w: exp.Expr = exp.EQ(this=fk.copy(), expression=exp.column("k", table=ALLOC_W, quoted=True))
    on_t: exp.Expr = exp.EQ(
        this=exp.column("k", table=ALLOC_W, quoted=True),
        expression=exp.column("k", table=ALLOC_T, quoted=True),
    )
    if per:
        fp = period(date.copy(), per, dialect)
        on_w = exp.and_(
            on_w, exp.EQ(this=fp, expression=exp.column("p", table=ALLOC_W, quoted=True))
        )
        on_t = exp.and_(
            on_t,
            exp.EQ(
                this=exp.column("p", table=ALLOC_W, quoted=True),
                expression=exp.column("p", table=ALLOC_T, quoted=True),
            ),
        )
    share = exp.Coalesce(
        this=exp.Div(
            this=exp.column("wt", table=ALLOC_W, quoted=True),
            expression=exp.Nullif(
                this=exp.column("tot", table=ALLOC_T, quoted=True), expression=lit(0)
            ),
        ),
        expressions=[lit(1)],
    )
    bv = exp.Coalesce(
        this=exp.column("b", table=ALLOC_W, quoted=True),
        expressions=[exp.Literal.string(_UNMAPPED)],
    )
    sel = exp.select(
        exp.alias_(p, "period", quoted=True),
        exp.alias_(bv, "breakdown_value", quoted=True),
        exp.alias_(exp.Sum(this=exp.Mul(this=value, expression=share)), "value", quoted=True),
    )
    sel = _from(scope, sel)
    sel = sel.join(exp.Table(this=ident(ALLOC_W)), on=on_w, join_type="left")
    sel = sel.join(exp.Table(this=ident(ALLOC_T)), on=on_t, join_type="left")
    for c in _where(scope, body):
        sel = sel.where(c)
    sel = sel.where(exp.GTE(this=date.copy(), expression=exp.cast(placeholder("from"), "DATE")))
    sel = sel.where(exp.LT(this=date.copy(), expression=exp.cast(placeholder("to"), "DATE")))
    sel = sel.group_by(p.copy(), bv.copy())
    sel = sel.with_(ALLOC_W, as_=wsel).with_(ALLOC_T, as_=tsel)
    sql = to_sql(sel, dialect)
    recheck(m, sql, dialect, scope)
    recheck(m, sql, dialect, ws)
    return sql


_UNMAPPED = "Unmapped"


def _calendar(dialect: str) -> exp.Expr:
    """Every as-of date from :from to :to (inclusive) in steps of 7 days, as a recursive CTE
    (PostgreSQL, MySQL 8 and DuckDB all run it)."""
    as_of = exp.column("as_of", table=CALENDAR, quoted=True)
    nxt = exp.cast(
        exp.DateAdd(
            this=as_of.copy(),
            expression=exp.Literal.number(BACKFILL_STEP_DAYS),
            unit=exp.var("DAY"),
        ),
        "DATE",
    )
    anchor = exp.select(exp.alias_(exp.cast(placeholder("from"), "DATE"), "as_of", quoted=True))
    step = (
        exp.select(exp.alias_(nxt, "as_of", quoted=True))
        .from_(exp.Table(this=ident(CALENDAR)))
        .where(exp.LTE(this=nxt.copy(), expression=exp.cast(placeholder("to"), "DATE")))
    )
    return exp.union(anchor, step, distinct=False)


def _backfill_sql(
    m: str,
    body: dict[str, Any],
    t: TableInfo,
    cat: Catalog,
    rules: Rules,
    bds: dict[str, Any],
    vocabs: dict[str, Any],
    dialect: str,
    bkey: str | None,
) -> str:
    """The snapshot query for every as-of date at once (docs/OPEN-QUESTIONS 5 Nov): the fact
    table crossed with the calendar, :as_of read from the calendar row, grouped by as-of date.
    Aggregates only; the same filters, rules and personal-column guard as the as-of query."""
    scope = Scope(m, cat, t, rules, dialect)
    as_of = exp.column("as_of", table=CALENDAR, quoted=True)
    scope.params = {"as_of": as_of}
    measure = body["measure"]
    value = render(measure["expr"], scope, where="measure") if measure.get("expr") else None
    bexpr = None
    if bkey is not None:
        bexpr, _ = _breakdown(scope, bkey, bds[bkey], vocabs)
    conds = _where(scope, body)
    if measure["agg"] == "last":
        sel = _last_query(scope, body, value, bexpr, conds, as_of=as_of)
    else:
        cols: list[exp.Expr] = [exp.alias_(as_of.copy(), "as_of", quoted=True)]
        group: list[exp.Expr] = [as_of.copy()]
        if bexpr is not None:
            cols.append(exp.alias_(bexpr, "breakdown_value", quoted=True))
            group.append(bexpr.copy())
        sel = exp.select(*cols, exp.alias_(aggregate(measure["agg"], value), "value", quoted=True))
        sel = _from(scope, sel).join(exp.Table(this=ident(CALENDAR)), join_type="cross")
        for c in conds:
            sel = sel.where(c)
        sel = sel.group_by(*group)
    sel = sel.with_(CALENDAR, as_=_calendar(dialect), recursive=True)
    sql = to_sql(sel, dialect)
    recheck(m, sql, dialect, scope)
    return sql


def _history_start_sql(
    m: str, t: TableInfo, cat: Catalog, rules: Rules, bf: dict[str, Any], dialect: str
) -> str:
    """The earliest date in the as-of columns: where usable history starts in the source."""
    scope = Scope(m, cat, t, rules, dialect)
    cols = [
        exp.alias_(
            exp.Min(
                this=exp.cast(scope.column({"col": c}, where="history", convert=False), "DATE")
            ),
            c,
            quoted=True,
        )
        for c in (bf.get("history_columns") or bf["as_of_columns"])
    ]
    sel = _from(scope, exp.select(*cols))
    for f in scope.rules.filters.get(scope.fact.stable_key, []):  # table rules, not :as_of
        sel = sel.where(render(f, scope, where="table rule"))
    sql = to_sql(sel, dialect)
    recheck(m, sql, dialect, scope)
    return sql


def _unmapped_sql(
    m: str,
    body: dict[str, Any],
    t: TableInfo,
    cat: Catalog,
    rules: Rules,
    um: dict[str, Any],
    dialect: str,
) -> str:
    """Per month: value of rows whose code matches no known prefix, by the code's prefix."""
    scope = Scope(m, cat, t, rules, dialect)
    code = scope.column({"col": um["column"]}, where="unmapped check", convert=False)
    n = max(len(p) for p in um["entries"])
    value = (
        render(body["measure"]["expr"], scope, where="measure")
        if body["measure"].get("expr")
        else None
    )
    date = scope.column(_date_ref(body["date"]), where="date", convert=False)
    pfx = exp.Substring(this=code.copy(), start=exp.Literal.number(1), length=exp.Literal.number(n))
    p = period(date, "month", dialect)
    sel = exp.select(
        exp.alias_(p, "period", quoted=True),
        exp.alias_(pfx, "prefix", quoted=True),
        exp.alias_(aggregate(body["measure"]["agg"], value), "value", quoted=True),
    )
    sel = _from(scope, sel)
    for c in _where(scope, body):
        sel = sel.where(c)
    for prefix in sorted(um["entries"]):
        sel = sel.where(
            exp.Not(this=exp.Like(this=code.copy(), expression=exp.Literal.string(prefix + "%")))
        )
    sel = sel.where(exp.GTE(this=date.copy(), expression=exp.cast(placeholder("from"), "DATE")))
    sel = sel.where(exp.LT(this=date.copy(), expression=exp.cast(placeholder("to"), "DATE")))
    sql = to_sql(sel.group_by(p.copy(), pfx.copy()), dialect)
    recheck(m, sql, dialect, scope)
    return sql


def _last_query(
    scope: Scope,
    body: dict[str, Any],
    value: exp.Expr | None,
    bexpr: exp.Expr | None,
    conds: list[exp.Expr],
    *,
    as_of: exp.Expr | None = None,
) -> exp.Select:
    """Snapshot of the latest row per partition (e.g. closing balance per bank as of a date)."""
    lb = body["last_by"]
    part = [scope.column({"col": c}, where="last_by", convert=False) for c in lb["partition"]]
    if as_of is not None:  # back-fill: the latest row per partition for each as-of date
        part.insert(0, as_of.copy())
    order = [
        exp.Ordered(this=scope.column({"col": c}, where="last_by", convert=False), desc=True)
        for c in lb["order"]
    ]
    rn = exp.Window(this=exp.RowNumber(), partition_by=part, order=exp.Order(expressions=order))
    assert value is not None
    inner_cols = [exp.alias_(value, "v", quoted=True), exp.alias_(rn, "rn", quoted=True)]
    if bexpr is not None:
        inner_cols.insert(0, exp.alias_(bexpr, "breakdown_value", quoted=True))
    if as_of is not None:
        inner_cols.insert(0, exp.alias_(as_of.copy(), "as_of", quoted=True))
    inner = _from(scope, exp.select(*inner_cols))
    if as_of is not None:
        inner = inner.join(exp.Table(this=ident(CALENDAR)), join_type="cross")
    for c in conds:
        inner = inner.where(c)
    sub = exp.Subquery(this=inner, alias=exp.TableAlias(this=ident("last_rows")))
    outer_cols: list[exp.Expr] = [
        exp.alias_(exp.Sum(this=exp.column("v", quoted=True)), "value", quoted=True)
    ]
    keys = [exp.column("as_of", quoted=True)] if as_of is not None else []
    if bexpr is not None:
        keys.append(exp.column("breakdown_value", quoted=True))
    sel = exp.select(*[k.copy() for k in keys], *outer_cols)
    sel = sel.from_(sub).where(exp.EQ(this=exp.column("rn", quoted=True), expression=lit(1)))
    if keys:
        sel = sel.group_by(*keys)
    return sel


def _date_ref(date: str) -> dict[str, Any]:
    if "." in date:
        table, col = date.split(".", 1)
        return {"col": col, "table": table}
    return {"col": date}


# --- derived metrics: DuckDB over the series table ------------------------------------------


def _refs(formula: dict[str, Any]) -> list[dict[str, Any]]:
    return [n for n in walk(formula) if "metric" in n]


def _derived_sql(
    m: str, formula: dict[str, Any], grain: str, bkey: str | None, kinds: dict[str, str]
) -> str:
    refs = _refs(formula)
    names = sorted({r["metric"] for r in refs})
    alias = {name: f"c{i}" for i, name in enumerate(names)}
    ctes: list[tuple[str, exp.Expr]] = []
    for name in names:
        base = (
            exp.select()
            .from_(exp.Table(this=ident(SERIES_TABLE)))
            .where(exp.EQ(this=exp.column("metric", quoted=True), expression=lit(name)))
            .where(exp.EQ(this=exp.column("breakdown", quoted=True), expression=lit(bkey or "")))
        )
        if kinds.get(name) == "snapshot":  # snapshot: the last daily value in each period
            p = period(exp.column("period", quoted=True), grain, "duckdb")
            sel = base.select(
                exp.alias_(p, "period", quoted=True),
                exp.column("breakdown_value", quoted=True),
                exp.alias_(
                    exp.ArgMax(
                        this=exp.column("value", quoted=True),
                        expression=exp.column("period", quoted=True),
                    ),
                    "value",
                    quoted=True,
                ),
            )
            sel = sel.where(exp.EQ(this=exp.column("grain", quoted=True), expression=lit("day")))
            sel = sel.group_by(p.copy(), exp.column("breakdown_value", quoted=True))
        else:
            sel = base.select(
                exp.column("period", quoted=True),
                exp.column("breakdown_value", quoted=True),
                exp.column("value", quoted=True),
            )
            sel = sel.where(exp.EQ(this=exp.column("grain", quoted=True), expression=lit(grain)))
        ctes.append((alias[name], sel))
    keys: exp.Expr | None = None
    for name in names:
        k = exp.select(
            exp.column("period", quoted=True), exp.column("breakdown_value", quoted=True)
        ).from_(alias[name])
        keys = k if keys is None else exp.union(keys, k, distinct=True)
    assert keys is not None

    def value_of(node: dict[str, Any]) -> exp.Expr:
        if "metric" in node:
            v = exp.column("value", table=alias[node["metric"]], quoted=True)
            return (
                exp.Coalesce(this=v, expressions=[lit(0)]) if node.get("missing") == "zero" else v
            )
        if "lit" in node:
            return lit(node["lit"])
        if node.get("fn") == "days_in_period":
            if grain == "day":
                return lit(1)
            if grain == "week":
                return lit(7)
            k = exp.column("period", table="k", quoted=True)
            return exp.Anonymous(
                this="DATE_DIFF",
                expressions=[
                    exp.Literal.string("day"),
                    k,
                    exp.Add(
                        this=k.copy(),
                        expression=exp.Interval(
                            this=exp.Literal.string("1"), unit=exp.var("MONTH")
                        ),
                    ),
                ],
            )
        if "op" in node:
            a = [value_of(x) for x in node["args"]]
            ops = {"+": exp.Add, "-": exp.Sub, "*": exp.Mul}
            if node["op"] in ops:  # folded left over every argument (a + b + c)
                acc = a[0]
                for x in a[1:]:
                    acc = ops[node["op"]](this=acc, expression=x)
                return exp.Paren(this=acc)
            if node["op"] == "/":
                return exp.Paren(
                    this=exp.Div(this=a[0], expression=exp.Nullif(this=a[1], expression=lit(0)))
                )
        if node.get("fn") == "abs":
            return exp.Abs(this=value_of(node["args"][0]))
        raise CompileError(
            f"{m}: a derived formula supports + − × ÷, abs, numbers and days_in_period"
        )

    nd = [
        exp.Is(
            this=exp.column("value", table=alias[r["metric"]], quoted=True), expression=exp.Null()
        )
        for r in refs
        if r.get("missing", "not_determinable") != "zero"
    ]
    missing: exp.Expr | None = None
    for c in nd:
        missing = c if missing is None else exp.Or(this=missing, expression=c)
    val = value_of(formula)
    if missing is not None:
        val = exp.Case(ifs=[exp.If(this=missing.copy(), true=exp.Null())], default=val)
        state: exp.Expr = exp.Case(
            ifs=[exp.If(this=missing, true=lit("not_determinable"))], default=lit("ok")
        )
    else:
        state = lit("ok")
    sel = exp.select(
        exp.column("period", table="k", quoted=True),
        exp.column("breakdown_value", table="k", quoted=True),
        exp.alias_(val, "value", quoted=True),
        exp.alias_(state, "state", quoted=True),
    )
    sel = sel.from_(exp.Subquery(this=keys, alias=exp.TableAlias(this=ident("k"))))
    for name in names:
        a = alias[name]
        on = exp.And(
            this=exp.EQ(
                this=exp.column("period", table=a, quoted=True),
                expression=exp.column("period", table="k", quoted=True),
            ),
            expression=exp.NullSafeEQ(
                this=exp.column("breakdown_value", table=a, quoted=True),
                expression=exp.column("breakdown_value", table="k", quoted=True),
            ),
        )
        sel = sel.join(exp.Table(this=ident(a)), on=on, join_type="left")
    for a_name, cte in ctes:
        sel = sel.with_(a_name, as_=cte)
    sel = sel.order_by(
        exp.column("period", table="k", quoted=True),
        exp.column("breakdown_value", table="k", quoted=True),
    )
    return to_sql(sel, "duckdb")


# --- the spec -------------------------------------------------------------------------------


def _label(t: TableInfo) -> str:
    return f"{t.source_name} ({KIND_NAME[t.source_kind]})"


# the floor without a `materiality` record, per company currency (M15.6): ₹5 L, $5,000
DEFAULT_FLOOR = {"INR": "500000", "USD": "5000"}


def compile_records(
    records: dict[tuple[str, str], dict[str, Any]],
    cat: Catalog,
    company: dict[str, Any],
    ontology_version: int,
) -> dict[str, Any]:
    """Pure: the same records, catalog and company settings give the same document."""
    rules = _rules(records, cat)
    bds = {k: b for (t, k), b in records.items() if t == "breakdown"}
    vocabs = rules.vocabularies
    metrics_in = {k: b for (t, k), b in records.items() if t == "metric"}
    out_metrics: dict[str, Any] = {}
    sources_used: dict[str, dict[str, Any]] = {}
    kinds: dict[str, str] = {}
    unmapped_checks: list[dict[str, Any]] = []
    tables_read: dict[str, dict[str, Any]] = {}

    for m in sorted(metrics_in):
        if not _KEY.match(m):
            raise CompileError(f"metric key {m!r} must be lower_snake_case")
        body = metrics_in[m]
        status = body.get("status", "active")
        entry: dict[str, Any] = {
            k: body.get(k)
            for k in (
                "name",
                "family",
                "variant",
                "unit",
                "grain",
                "kind",
                "headline",
                "listed",
                "description",
            )
        }
        entry["status"] = status
        kinds[m] = body.get("kind", "flow")
        if status in ("suggested", "declined"):
            continue
        if status == "needs_source":
            entry.update(
                {"needs": body.get("needs"), "source_label": "Needs a source", "run_location": None}
            )
            out_metrics[m] = entry
            continue
        if body.get("table"):
            t = _table(cat, body["table"], m)
            check_use(m, t)
            sql = _base_sql(m, body, t, cat, rules, bds, vocabs, t.dialect)
            entry.update(
                {
                    "type": "base",
                    "agg": body["measure"]["agg"],
                    "source": t.source_name,
                    "source_id": t.source_id,
                    "dialect": t.dialect,
                    "table": {"schema": t.schema, "name": t.name, "stable_key": t.stable_key},
                    "breakdowns": sorted(body.get("breakdowns", [])),
                    **sql,
                }
            )
            entry["sources"] = [t.source_name]
            entry["labels"] = [
                _label(t) if t.source_kind != "gsheets" else f"Google Sheets · {t.schema}"
            ]
            if t.source_kind == "gsheets":
                entry["source_label"] = f"Google Sheets · {t.schema}"
                entry["run_location"] = f"read from Google Sheets · {t.schema}"
            else:
                entry["source_label"] = _label(t)
                entry["run_location"] = f"runs in place on {_label(t)} · read-only"
            sources_used[t.source_name] = {
                "id": t.source_id,
                "kind": t.source_kind,
                "dialect": t.dialect,
            }
            tables_read[f"{t.source_name}:{t.schema}.{t.name}"] = {
                "source": t.source_name,
                "schema": t.schema,
                "table": t.name,
                "stable_key": t.stable_key,
                "watermark": t.watermark,
                "lookback_days": DEFAULT_LOOKBACK_DAYS,
            }
            if entry.get("unmapped"):
                unmapped_checks.append(
                    {
                        "metric": m,
                        "breakdown": entry["unmapped"]["breakdown"],
                        "bucket": entry["unmapped"]["other"],
                        "threshold": UNMAPPED_THRESHOLD,
                        "question": UNMAPPED_QUESTION,
                        "source": t.source_name,
                    }
                )
            entry["backfill"] = _backfill(body, t)
        out_metrics[m] = entry

    # derived: after every base, in dependency order
    order = _order(metrics_in, out_metrics)
    for m in order:
        body = metrics_in[m]
        if out_metrics.get(m, {}).get("type") == "base" or body.get("status") != "active":
            continue
        if body.get("formula") is None:
            continue
        refs = sorted(
            {r["metric"] for r in _refs(body["formula"])}
            | {r["metric"] for f in body.get("breakdown_formulas", {}).values() for r in _refs(f)}
        )
        comp = [out_metrics.get(r) for r in refs]
        missing = [
            r for r, c in zip(refs, comp, strict=True) if c is None or c.get("status") != "active"
        ]
        entry = out_metrics[m]
        if missing:
            entry.update(
                {
                    "status": "needs_source",
                    "needs": f"{', '.join(missing)} first",
                    "source_label": "Needs a source",
                    "run_location": None,
                }
            )
            continue
        parts = [c for c in comp if c is not None]
        comp_sources = sorted({x for c in parts for x in c.get("sources", [])})
        flat = sorted({x for c in parts for x in c.get("labels", [])})
        bkeys = sorted(set(body.get("breakdowns", [])))
        queries = {}
        grains = GRAINS
        for grain in grains:
            for bkey in [None, *bkeys]:
                f = (
                    body.get("breakdown_formulas", {}).get(bkey, body["formula"])
                    if bkey
                    else body["formula"]
                )
                usable = all(
                    bkey is None or bkey in (out_metrics[r["metric"]].get("breakdowns") or [])
                    for r in _refs(f)
                )
                if not usable:
                    continue
                queries[f"{grain}:{bkey or ''}"] = _derived_sql(m, f, grain, bkey, kinds)
        entry.update(
            {
                "type": "derived",
                "sources": comp_sources,
                "labels": flat,
                "components": refs,
                "breakdowns": sorted({k.split(":")[1] for k in queries if k.split(":")[1]}),
                "dialect": "duckdb",
                "queries": queries,
                "source_label": " + ".join(flat) + " · derived",
                "run_location": "joined in Othor AI from " + " and ".join(comp_sources),
            }
        )

    for m, entry in out_metrics.items():
        body = metrics_in.get(m, {})
        for k in ("at_stake", "distribution", "area"):
            if body.get(k):
                entry[k] = body[k]
        # the formula's signed parts (M16.1), only for a pure sum of metrics
        if entry.get("type") == "derived":
            signed = signed_terms(body.get("formula"))
            if signed:
                entry["terms"] = signed
            # a formula over the period's length (DSO: × days in period) means something only at
            # its own grain; Summary doesn't re-read it by the week (M16.2)
            if any(n.get("fn") == "days_in_period" for n in walk(body.get("formula") or {})):
                entry["per_period"] = True
            by = {
                k: tt
                for k, f in sorted((body.get("breakdown_formulas") or {}).items())
                if k in (entry.get("breakdowns") or []) and (tt := signed_terms(f))
            }
            if by:
                entry["terms_by"] = by
        if body.get("table"):
            entry["scale"] = str(body.get("scale") or "1")
        if entry.get("queries"):
            entry["render_hash"] = render_hash(entry)
    from othor.ontology.compiler.probes import compile_probes

    probes, pairs, owners = compile_probes(records, cat, rules, out_metrics, tables_read)
    known = sorted((k, b) for (t, k), b in records.items() if t == "known_pattern")
    goals = sorted((k, b) for (t, k), b in records.items() if t == "goal")
    spec: dict[str, Any] = {
        "spec_format": SPEC_FORMAT,
        "compiler": COMPILER_VERSION,
        "sqlglot": importlib.metadata.version("sqlglot"),
        "ontology_version": ontology_version,
        "company": company,
        "series_table": SERIES_TABLE,
        "sources": dict(sorted(sources_used.items())),
        "metrics": out_metrics,
        "order": [m for m in order if out_metrics.get(m, {}).get("status") == "active"],
        "breakdowns": {
            k: {"name": b["name"], "cap": b.get("cap", 50), "entity": bool(b.get("entity"))}
            for k, b in sorted(bds.items())
        },
        "suppressions": [{"key": k, **b} for k, b in known],
        "forecast": {
            "headline": sorted(
                {b["metric"] for _, b in goals}
                | {m for m, e in out_metrics.items() if e.get("headline")}
            ),
            "goals": [{"key": k, **b} for k, b in goals],
        },
        "detectors": DETECTOR_DEFAULTS,
        "read": dict(sorted(tables_read.items())),
        "unmapped_checks": unmapped_checks,
        "probes": probes,
        "pairs": pairs,
        "allocations": [
            {
                "key": k,
                "metric": b["metric"],
                "breakdown": b["breakdown"],
                "text": b.get("text", ""),
            }
            for (t, k), b in sorted(records.items())
            if t == "rule" and b.get("kind") == "allocation"
        ],
        "owners": owners,
        "rules": [
            {
                "key": k,
                "kind": b["kind"],
                "text": b.get("text") or _rule_sentence(b, cat, metrics_in),
                "applies_to": _applies_to(b, metrics_in),
            }
            for (t, k), b in sorted(records.items())
            if t == "rule"
        ],
    }
    # the state of the business's thresholds (v0.12 part 1b): only when the Ontology carries a
    # `weather` record, so a company without one compiles the same spec as before
    weather: dict[str, Any] = {}
    for (rtype, _rkey), rbody in sorted(records.items()):
        if rtype == "weather":
            weather.update(rbody)
    if weather:
        spec["weather"] = dict(sorted(weather.items()))
    # the floor the owner sets (M15.6): only when there is a `materiality` record, or the company
    # reports in another currency than ₹, so an INR company without one compiles as before
    floor = next((b for (t, _k), b in sorted(records.items()) if t == "materiality"), None)
    cur = str((company or {}).get("currency") or "INR")
    if floor is not None or cur != "INR":
        amount = floor["amount"] if floor else DEFAULT_FLOOR.get(cur, "5000")
        spec["detectors"] = {**DETECTOR_DEFAULTS, "materiality_inr": str(amount)}
    for m, entry in out_metrics.items():
        if entry.get("status") == "active" and (entry.get("queries") or entry.get("components")):
            entry["definition_hash"] = definition_hash(m, records, spec)
    return spec


KIND_LABEL = {"postgres": "PostgreSQL", "mysql": "MySQL", "gsheets": "Google Sheets"}


def _rule_sentence(
    rule: dict[str, Any], cat: Catalog, metrics_in: dict[str, dict[str, Any]]
) -> str:
    """A rule without its own text, said from the rule itself (the metric panel, 4f1)."""

    def where(ref: dict[str, Any] | None) -> str:
        if not ref:
            return ""
        kind = KIND_LABEL.get((cat.sources.get(ref["source"]) or ("", ""))[1], "")
        return f"{ref['table']} in {ref['source']}" + (f" ({kind})" if kind else "")

    def name(m: str) -> str:
        return str(metrics_in.get(m, {}).get("name") or m.replace("_", " "))

    kind = rule.get("kind", "")
    if kind == "join" and rule.get("vocabulary"):
        return f"{where(rule.get('table'))} joins its codes through the {rule['vocabulary']} list"
    if kind == "join":
        cols = " and ".join(a.replace("_", " ") for a, _ in rule.get("on") or [])
        via = " through your aliases" if rule.get("via") == "alias" else ""
        return f"Joined to {where(rule.get('to'))} on {cols}{via}"
    if kind == "allocation":
        over = rule.get("breakdown", "")
        return f"{name(rule.get('metric', ''))} is allocated across {over} by rule"
    label = kind.replace("_", " ").capitalize()
    return f"{label} on {where(rule.get('table'))}" if rule.get("table") else label


def _ref(r: Any) -> str | None:
    if not isinstance(r, dict) or not {"source", "schema", "table"} <= set(r):
        return None
    return f"{r['source']}/{r['schema']}/{r['table']}"


def _applies_to(rule: dict[str, Any], metrics_in: dict[str, dict[str, Any]]) -> list[str]:
    """The base metrics a rule touches (its table is theirs, or it names them): for the
    metric panel's "Rules from your Ontology" (4f1). Derived metrics show their parts' rules."""
    named = {rule.get("metric"), *(rule.get("over") or [])} - {None}
    tables = {_ref(rule.get("table")), _ref(rule.get("to"))} - {None}
    out = {
        m
        for m, b in metrics_in.items()
        if m in named or (b.get("table") and _ref(b["table"]) in tables)
    }
    return sorted(out)


NOT_MEANING = {
    "area",  # a grouping for Summary (M16.1), never a change of meaning
    "name",
    "description",
    "family",
    "variant",
    "headline",
    "listed",
    "status",
    "confirmed_by",
    "confirmed_at",
    "confidence",
    "evidence",
    "needs",
    "at_stake",
    "distribution",
    "scale",
    "text",
}


def definition_hash(
    metric: str,
    records: dict[tuple[str, str], dict[str, Any]],
    spec: dict[str, Any],
    seen: frozenset[str] = frozenset(),
) -> str:
    """The metric's meaning, from the Ontology records (decided 5 Nov 2026): its measure,
    filters, date, breakdowns, formula and unit, the rules that apply to it (filters, joins,
    book of record, currency, unit conversions, allocations), the vocabularies and aliases they
    read, and for a derived metric its parts' meanings. Only this starts a new series version.
    A rename, a description, an owner or a pure scale doesn't; and neither does a change in
    what the compiler renders (`render_hash`: a compiler fix, a wider decimal, a new dialect)."""
    import hashlib
    import json

    body = {
        k: v for k, v in (records.get(("metric", metric)) or {}).items() if k not in NOT_MEANING
    }
    rules = []
    vocabs: set[str] = set()
    alias = False
    for r in spec.get("rules", []):
        if metric in (r.get("applies_to") or []):
            rb = {k: v for k, v in (records.get(("rule", r["key"])) or {}).items() if k != "text"}
            rules.append([r["key"], rb])
            if rb.get("vocabulary"):
                vocabs.add(rb["vocabulary"])
            alias = alias or rb.get("via") == "alias"
    bds = []
    for b in body.get("breakdowns") or []:
        bb = {
            k: v
            for k, v in (records.get(("breakdown", b)) or {}).items()
            if k not in ("name", "cap")
        }
        if bb.get("vocabulary"):
            vocabs.add(bb["vocabulary"])
        bds.append([b, bb])
    doc: dict[str, Any] = {
        "metric": body,
        "rules": sorted(rules, key=lambda x: x[0]),
        "breakdowns": bds,
        "vocabularies": [[v, records.get(("vocabulary", v)) or {}] for v in sorted(vocabs)],
    }
    if alias:
        doc["aliases"] = sorted(
            [b.get("source"), b.get("term"), b.get("means")]
            for (typ, _), b in records.items()
            if typ == "alias" and b.get("source")
        )
    parts = (spec.get("metrics", {}).get(metric) or {}).get("components") or []
    doc["parts"] = [
        [c, definition_hash(c, records, spec, seen | {metric})]
        for c in sorted(parts)
        if c not in seen
    ]
    text = json.dumps(doc, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def render_hash(entry: dict[str, Any]) -> str:
    """What the compiler produced for the metric (its SQL, kind and unit): stored for replay
    and provenance; a change to it alone never versions a series."""
    import hashlib
    import json

    doc = {
        "kind": entry.get("kind"),
        "queries": entry.get("queries", {}),
        "unit": entry.get("unit"),
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:16]


def _order(metrics_in: dict[str, dict[str, Any]], compiled: dict[str, Any]) -> list[str]:
    """Bases first, then derived metrics after the metrics they use; cycles are an error."""
    done: list[str] = []
    state: dict[str, int] = {}

    def visit(m: str, path: tuple[str, ...]) -> None:
        if state.get(m) == 2:
            return
        if state.get(m) == 1:
            raise CompileError(f"derived metrics refer to each other: {' → '.join((*path, m))}")
        state[m] = 1
        body = metrics_in.get(m)
        if body is None:
            raise CompileError(f"{path[-1] if path else m}: refers to unknown metric {m}")
        for f in [body.get("formula"), *body.get("breakdown_formulas", {}).values()]:
            if f:
                for r in _refs(f):
                    visit(r["metric"], (*path, m))
        state[m] = 2
        done.append(m)

    for m in sorted(metrics_in):
        if metrics_in[m].get("status", "active") not in ("suggested", "declined"):
            visit(m, ())
    return done


_EVENT_DATE = re.compile(r"(date|_at|_on)$")


def _backfill(body: dict[str, Any], t: TableInfo) -> dict[str, Any] | None:
    """Snapshot metrics: can "as of" a past date be computed from event-date columns? (M5)"""
    if body.get("kind") != "snapshot":
        return None
    if t.source_kind == "gsheets":
        return {"possible": False, "reason": "Google Sheets tabs have no history"}
    nodes = [n for f in body.get("filters", []) for n in walk(f)]
    if body.get("measure", {}).get("expr"):
        nodes += walk(body["measure"]["expr"])
    uses_as_of = any(n.get("param") == "as_of" for n in nodes)
    if not uses_as_of:
        return {"possible": False, "reason": "the definition has no as-of date"}
    # every comparison involving :as_of must compare it with an event-date column
    date_cols: set[str] = set()
    fact_cols: set[str] = set()  # the fact table's own: where usable history starts
    for f in body.get("filters", []):
        for n in walk(f):
            if n.get("op") in ("<", "<=", ">", ">=", "=") or n.get("fn") == "days_between":
                args = n.get("args", [])
                if any(a.get("param") == "as_of" for a in args):
                    for a in args:
                        if "col" in a:
                            if not _EVENT_DATE.search(a["col"]):
                                return {
                                    "possible": False,
                                    "reason": f"{a['col']} is not an event date",
                                }
                            date_cols.add(a["col"])
                            if not a.get("table"):
                                fact_cols.add(a["col"])
    status_only = any("col" in n and n["col"] in ("status",) for n in nodes)
    if status_only and not date_cols:
        return {"possible": False, "reason": "it depends on a status without a date"}
    out: dict[str, Any] = {"possible": True, "as_of_columns": sorted(date_cols)}
    if fact_cols != date_cols:
        out["history_columns"] = sorted(fact_cols)
    return out


def reject_reason(e: CompileError) -> dict[str, Any]:
    if isinstance(e, PersonalColumnRejected):
        return {"metric": e.metric, "table": e.table, "where": e.where}
    return {"error": str(e)}


def render_base(
    records: dict[tuple[str, str], dict[str, Any]], cat: Catalog, metric: str, dialect: str
) -> dict[str, Any]:
    """One base metric's SQL in another dialect (tests run erp_db SQL on DuckDB)."""
    body = records[("metric", metric)]
    t = _table(cat, body["table"], metric)
    rules = _rules(records, cat)
    bds = {k: b for (ty, k), b in records.items() if ty == "breakdown"}
    return _base_sql(metric, body, t, cat, rules, bds, rules.vocabularies, dialect)
