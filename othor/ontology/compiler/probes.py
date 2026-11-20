# SPDX-License-Identifier: Apache-2.0
"""What Sweep reads beyond the series (M6, docs/04 §3): compiled from Ontology records into
read-only, aggregate SQL that the nightly run's `probes` step sends through the query log.

- entities (`breakdown.entity`): each entity's name, status, identity and created date, from
  its own table (Missing records). Its activity is the entity metric's daily series by that
  breakdown, which the run already reads.
- thresholds (`rule` threshold): counts just below and just above a limit (Process artefact).
- reconcile (`rule` reconcile): keys on the left with their date and value, keys on the right
  (Cross-source, keys); or two metrics compared period by period (Cross-source, periods).
- distributions (`metric.distribution`): per-event values (Bimodal average).
- pairs (`rule` pair) and owners (`owner`) are passed through as data.

Every column goes through `Scope`, so a personal column is refused here exactly as in a metric.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from othor.ontology.compiler.catalog import Catalog
from othor.ontology.compiler.guard import recheck
from othor.ontology.compiler.render import (
    CompileError,
    Rules,
    Scope,
    aggregate,
    lit,
    placeholder,
    render,
    to_sql,
)


def compile_probes(
    records: dict[tuple[str, str], dict[str, Any]],
    cat: Catalog,
    rules: Rules,
    metrics_out: dict[str, Any],
    tables_read: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    from othor.ontology.compiler.build import _from, _table, _where

    probes: dict[str, Any] = {
        "entities": {},
        "thresholds": [],
        "reconcile": [],
        "distributions": [],
        "matches": [],
    }
    pairs: list[dict[str, Any]] = []
    owners: list[dict[str, Any]] = []

    for (typ, key), body in sorted(records.items()):
        if typ == "breakdown" and body.get("entity") and _scanned(cat, body["entity"]["table"]):
            e = body["entity"]
            t = _table(cat, e["table"], f"entity {key}")
            scope = Scope(f"entity {key}", cat, t, rules, t.dialect)
            cols: list[exp.Expr] = [
                exp.alias_(
                    scope.column({"col": body["column"]}, where="entity", convert=False),
                    "name",
                    quoted=True,
                )
            ]
            for alias, c in (
                ("status", e.get("status_column")),
                ("identity", e.get("identity_column")),
                ("created", e.get("created_column")),
            ):
                cols.append(
                    exp.alias_(
                        scope.column({"col": c}, where=f"entity {alias}", convert=False)
                        if c
                        else exp.Null(),
                        alias,
                        quoted=True,
                    )
                )
            # the other breakdowns kept on the entity's own table (a customer's region and
            # segment): the drill-down of a breakdown value by entity (M8), aggregates only
            attrs = sorted(
                k
                for (ty, k), bd in records.items()
                if ty == "breakdown"
                and k != key
                and bd.get("table") == e["table"]["table"]
                and not bd.get("vocabulary")
                and bd.get("column") is not None  # an expression breakdown isn't an attribute
                and t.column(bd["column"]) is not None
            )
            for a in attrs:
                cols.append(
                    exp.alias_(
                        scope.column(
                            {"col": records[("breakdown", a)]["column"]},
                            where=f"entity attribute {a}",
                            convert=False,
                        ),
                        f"attr_{a}",
                        quoted=True,
                    )
                )
            sel = _from(scope, exp.select(*cols))
            for f in rules.filters.get(t.stable_key, []):
                sel = sel.where(render(f, scope, where="table rule"))
            sql = to_sql(sel, t.dialect)
            recheck(f"entity {key}", sql, t.dialect, scope)
            probes["entities"][key] = {
                "metric": e["metric"],
                "source": t.source_name,
                "sql": sql,
                "active_values": sorted(e.get("active_values") or []),
                "attributes": attrs,
            }
        elif typ == "rule" and body.get("kind") == "threshold":
            if _scanned(cat, body["table"]):
                probes["thresholds"].append(_threshold(key, body, cat, rules, _from, _table))
        elif typ == "rule" and body.get("kind") == "reconcile":
            got = _reconcile(key, body, cat, rules, metrics_out, _from, _table)
            if got is not None:
                probes["reconcile"].append(got)
        elif typ == "rule" and body.get("kind") == "pair":
            a, b = body["over"]
            if any(metrics_out.get(m, {}).get("status") != "active" for m in (a, b)):
                continue  # like a rule on an unscanned table: it doesn't apply yet
            pairs.append(
                {
                    "key": key,
                    "a": a,
                    "b": b,
                    "relation": body["relation"],
                    "text": body.get("text", ""),
                }
            )
        elif typ == "owner":
            owners.append(
                {
                    "key": key,
                    "name": body["name"],
                    "role": body.get("role", ""),
                    "areas": sorted(body.get("areas", [])),
                }
            )
        elif (
            typ == "metric"
            and body.get("distribution")
            and body.get("status", "active") == "active"
        ):
            d = body["distribution"]
            base = records.get(("metric", d["metric"]))
            if base is None or not base.get("table"):
                raise CompileError(
                    f"{key}: its distribution reads {d['metric']}, which isn't a base metric"
                )
            t = _table(cat, base["table"], key)
            scope = Scope(key, cat, t, rules, t.dialect)
            value = (
                render(base["measure"]["expr"], scope, where="measure")
                if base["measure"].get("expr")
                else None
            )
            k = scope.column({"col": d["key"]}, where="distribution key", convert=False)
            date = scope.column({"col": base["date"]}, where="date", convert=False)
            sel = exp.select(
                exp.alias_(k, "key", quoted=True),
                exp.alias_(aggregate(base["measure"]["agg"], value), "value", quoted=True),
            )
            sel = _from(scope, sel)
            for c in _where(scope, base):
                sel = sel.where(c)
            sel = sel.where(
                exp.GTE(this=date.copy(), expression=exp.cast(placeholder("from"), "DATE"))
            )
            sel = sel.where(
                exp.LT(this=date.copy(), expression=exp.cast(placeholder("to"), "DATE"))
            )
            sql = to_sql(sel.group_by(k.copy()), t.dialect)
            recheck(key, sql, t.dialect, scope)
            probes["distributions"].append(
                {
                    "key": key,
                    "metric": key,
                    "of": d["metric"],
                    "source": t.source_name,
                    "sql": sql,
                    "days": d.get("days", 365),
                }
            )
    probes["matches"] = _matches(records, cat, rules, tables_read or {}, _from)
    return probes, pairs, owners


def _matches(
    records: dict[tuple[str, str], dict[str, Any]],
    cat: Catalog,
    rules: Rules,
    tables_read: dict[str, Any],
    _from: Any,
) -> list[dict[str, Any]]:
    """Cross-source matching (6h, answered 5 Nov 2026): a table read tonight from another
    source that holds an entity's identity (GSTIN) or its name gets a probe of its distinct
    values; the run measures how many match the entity, and a strong match is proposed as a
    join. Personal columns are refused by `Scope`, exactly as in a metric."""
    out = []
    for (typ, key), body in sorted(records.items()):
        if (
            typ != "breakdown"
            or not body.get("entity")
            or not _scanned(cat, body["entity"]["table"])
        ):
            continue
        e = body["entity"]
        et = cat.table(e["table"]["source"], e["table"]["schema"], e["table"]["table"])
        if et is None:
            continue
        want = []
        if e.get("identity_column"):
            want.append(("identity", e["identity_column"].lower()))
        want += [("name", key.lower()), ("name", body["column"].lower())]
        joined = {(f, to) for (f, to) in rules.joins}
        for ref in sorted(
            tables_read.values(), key=lambda r: (r["source"], r["schema"], r["table"])
        ):
            if ref["source"] == et.source_name:
                continue
            t = cat.table(ref["source"], ref["schema"], ref["table"])
            if t is None or t.use == "excluded":
                continue  # never a key from an excluded table (5 Nov 2026)
            hit = next(
                ((by, c) for by, c in want if c in t.columns and not t.columns[c].personal), None
            )
            if hit is None:
                continue
            by, col = hit
            scope = Scope(f"match {key}", cat, t, rules, t.dialect)
            try:
                k = scope.column({"col": col}, where="match key", convert=False)
            except CompileError:
                continue  # personal or unreadable: never matched
            sel = _from(scope, exp.select(exp.alias_(k, "key", quoted=True)).distinct())
            sel = sel.where(exp.Not(this=exp.Is(this=k.copy(), expression=exp.Null())))
            for f in rules.filters.get(t.stable_key, []):
                sel = sel.where(render(f, scope, where="table rule"))
            sql = to_sql(sel, t.dialect)
            recheck(f"match {key}", sql, t.dialect, scope)
            to_col = e["identity_column"] if by == "identity" else body["column"]
            out.append(
                {
                    "key": f"{key}:{t.stable_key}:{col}",
                    "entity": key,
                    "by": by,
                    "source": t.source_name,
                    "table": {"source": t.source_name, "schema": t.schema, "table": t.name},
                    "table_name": t.name,
                    "column": col,
                    "to": {"source": et.source_name, "schema": et.schema, "table": et.name},
                    "to_name": et.name,
                    "to_source": et.source_name,
                    "to_column": to_col,
                    "joined": (t.stable_key, et.name) in joined
                    or (et.stable_key, t.name) in joined,
                    "sql": sql,
                }
            )
    return out


def _scanned(cat: Catalog, ref: dict[str, Any]) -> bool:
    return cat.table(ref["source"], ref["schema"], ref["table"]) is not None


def _threshold(
    key: str, body: dict[str, Any], cat: Catalog, rules: Rules, _from: Any, _table: Any
) -> dict[str, Any]:
    t = _table(cat, body["table"], f"threshold {key}")
    scope = Scope(f"threshold {key}", cat, t, rules, t.dialect)
    amount = scope.column({"col": body["column"]}, where="threshold")
    limit, band = lit(body["limit"]), lit(body["band"])

    def between(lo: exp.Expr, hi: exp.Expr) -> exp.Expr:
        cond = exp.and_(
            exp.GTE(this=amount.copy(), expression=lo), exp.LT(this=amount.copy(), expression=hi)
        )
        return exp.Sum(this=exp.Case(ifs=[exp.If(this=cond, true=lit(1))], default=lit(0)))

    below = between(exp.Sub(this=limit.copy(), expression=band.copy()), limit.copy())
    above = between(limit.copy(), exp.Add(this=limit.copy(), expression=band.copy()))
    sel = _from(
        scope,
        exp.select(
            exp.alias_(below, "below", quoted=True),
            exp.alias_(above, "above", quoted=True),
            exp.alias_(exp.Count(this=exp.Star()), "events", quoted=True),
        ),
    )
    for f in rules.filters.get(t.stable_key, []):
        sel = sel.where(render(f, scope, where="table rule"))
    if body.get("when"):
        sel = sel.where(render(body["when"], scope, where="threshold filter"))
    if body.get("date"):
        d = scope.column({"col": body["date"]}, where="threshold date", convert=False)
        sel = sel.where(exp.GTE(this=d.copy(), expression=exp.cast(placeholder("from"), "DATE")))
        sel = sel.where(exp.LT(this=d.copy(), expression=exp.cast(placeholder("to"), "DATE")))
    sql = to_sql(sel, t.dialect)
    recheck(f"threshold {key}", sql, t.dialect, scope)
    return {
        "key": key,
        "label": body.get("label") or body.get("text", ""),
        "limit": body["limit"],
        "band": body["band"],
        "source": t.source_name,
        "sql": sql,
        "dated": bool(body.get("date")),
    }


def _reconcile(
    key: str,
    body: dict[str, Any],
    cat: Catalog,
    rules: Rules,
    metrics_out: dict[str, Any],
    _from: Any,
    _table: Any,
) -> dict[str, Any] | None:
    """None when the rule doesn't apply yet (a metric not active, a table not scanned)."""
    if len(body.get("over") or []) == 2:
        a, b = body["over"]
        if any(metrics_out.get(m, {}).get("status") != "active" for m in (a, b)):
            return None
        return {
            "key": key,
            "kind": "periods",
            "a": a,
            "b": b,
            "grain": body.get("grain") or "month",
            "a_label": metrics_out[a].get("source_label") or a,
            "b_label": metrics_out[b].get("source_label") or b,
            "text": body.get("text", ""),
        }
    if not (_scanned(cat, body["table"]) and _scanned(cat, body["to"])):
        return None
    lt = _table(cat, body["table"], f"reconcile {key}")
    rt = _table(cat, body["to"], f"reconcile {key}")
    (lkey, rkey), *_ = body["on"]
    from othor.ontology.compiler.build import _date_ref

    ls = Scope(f"reconcile {key}", cat, lt, rules, lt.dialect)
    k = ls.column({"col": lkey}, where="reconcile key", convert=False)
    d = exp.cast(ls.column(_date_ref(body["date"]), where="reconcile date", convert=False), "DATE")
    v = (
        ls.column({"col": body["column"]}, where="reconcile value")
        if body.get("column")
        else lit(0)
    )
    lcols: list[exp.Expr] = [
        exp.alias_(k, "key", quoted=True),
        exp.alias_(exp.Max(this=d.copy()), "date", quoted=True),
        exp.alias_(exp.Max(this=v), "value", quoted=True),
    ]
    left = _from(ls, exp.select(*lcols))
    for f in rules.filters.get(lt.stable_key, []):
        left = left.where(render(f, ls, where="table rule"))
    if body.get("when"):
        left = left.where(render(body["when"], ls, where="reconcile filter"))
    # one row per key (an order dispatched in several lines): its latest date, older than the lag
    left = left.group_by(k.copy()).having(
        exp.LT(this=exp.Max(this=d.copy()), expression=exp.cast(placeholder("cutoff"), "DATE"))
    )
    lsql = to_sql(left, lt.dialect)
    recheck(f"reconcile {key}", lsql, lt.dialect, ls)
    rs = Scope(f"reconcile {key}", cat, rt, rules, rt.dialect)
    rk = rs.column({"col": rkey}, where="reconcile key", convert=False)
    right = _from(rs, exp.select(exp.alias_(rk, "key", quoted=True)).distinct())
    right = right.where(exp.Not(this=exp.Is(this=rk.copy(), expression=exp.Null())))
    for f in rules.filters.get(rt.stable_key, []):
        right = right.where(render(f, rs, where="table rule"))
    rsql = to_sql(right, rt.dialect)
    recheck(f"reconcile {key}", rsql, rt.dialect, rs)
    return {
        "key": key,
        "kind": "keys",
        "lag_days": int(body["days"]),
        "left": {
            "source": lt.source_name,
            "table": lt.name,
            "sql": lsql,
            "label": f"{lt.name} in {lt.source_name}",
        },
        "right": {
            "source": rt.source_name,
            "table": rt.name,
            "sql": rsql,
            "label": f"{rt.name} in {rt.source_name}",
        },
        "noun": body.get("label") or "records",
        "text": body.get("text", ""),
    }
