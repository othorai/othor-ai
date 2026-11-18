# SPDX-License-Identifier: Apache-2.0
"""One read of rows for a report (Look back v2, LB7, 18 Nov 2026): the columns a plan needs from a
fact table and the tables the Business Model's confirmed join rules link to it, over a date
window, in one read-only query.

It is the compiler's evidence query widened (`build._base_sql`'s "evidence"), with the same
safeguards, so nothing here is new trust:
- a column is read only through `Scope`: a joined table only by a confirmed join rule
  (`Scope.table_for`), never a personal column (`Scope._raw` raises `PersonalColumnRejected`),
  never an ignored or Excluded table (`check_use`); the fact table's own filter rules and a joined
  table's rules apply as they do for a metric; unit-conversion rules apply to amounts;
- a number's own filters (`filters`, as in its definition) apply as they do in the nightly run;
- only the columns named, only between `:from` (inclusive) and `:to` (exclusive), and at most
  `limit` rows, so a caller can say plainly when a window holds too many;
- the finished SQL is re-checked by `guard.recheck`, as every compiled query is.

**Stacking** (owner, 18 Nov 2026): a second table of the same source with the same meaning (credit
notes under invoices) can be stacked under the fact table with UNION ALL; a `kind` column made by
code says which rows are which. A joined column the stacked table can't reach by its own confirmed
rule reads as NULL there. A deduction stored as a positive amount (a credit note's value) is read
as minus (`negate`), so the stacked rows add up to the net. Two sources are never joined here.

Core code: the Enterprise Edition's Look back calls it; nothing in it is Look back's own."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlglot import exp

from othor.ontology.compiler.build import _from, _rules, _table, _where
from othor.ontology.compiler.catalog import Catalog
from othor.ontology.compiler.guard import recheck
from othor.ontology.compiler.render import (
    FACT,
    CompileError,
    Scope,
    check_use,
    lit,
    placeholder,
    to_sql,
)

WHO = "a report's read"  # how errors name the caller (no metric here)
_OUT = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


@dataclass(frozen=True)
class Pick:
    """One column read: `col` on the fact table, or on `table`, joined by a confirmed rule."""

    out: str
    col: str
    table: str | None = None


@dataclass(frozen=True)
class Stack:
    """A table stacked under the fact table: its own column for each fact-table pick (by `out`),
    and the words in the `kind` column for each part."""

    table: dict[str, str]  # {"source", "schema", "table"}
    cols: dict[str, str]
    fact_label: str = "Sale"
    label: str = "Return"
    kind_out: str = "kind"
    filters: list[dict[str, Any]] = field(default_factory=list)  # the stacked number's own
    negate: tuple[str, ...] = ()  # amounts read as minus (a deduction stored as a positive)


@dataclass
class Read:
    sql: str
    tables: list[str] = field(default_factory=list)  # every table read, fact first
    columns: dict[str, list[str]] = field(default_factory=dict)  # table → columns read


def rows_sql(
    records: dict[tuple[str, str], dict[str, Any]],
    cat: Catalog,
    fact: dict[str, str],
    picks: list[Pick],
    date_out: str,
    dialect: str,
    *,
    stack: Stack | None = None,
    filters: list[dict[str, Any]] | None = None,
    limit: int,
) -> Read:
    """The query (parameters `:from`, `:to`) and what it reads, for provenance."""
    outs = [p.out for p in picks]
    if not picks or len(set(outs)) != len(outs) or not all(_OUT.match(o) for o in outs):
        raise CompileError(f"{WHO}: the columns need distinct plain names")
    if date_out not in outs:
        raise CompileError(f"{WHO}: the date column isn't among the columns read")
    rules = _rules(records, cat)
    parts: list[tuple[exp.Select, Scope]] = []
    read = Read(sql="")

    def part(
        ref: dict[str, str],
        cols: dict[str, Pick | None],
        label: str | None,
        own: list[dict[str, Any]],
    ) -> None:
        t = _table(cat, ref, WHO)
        check_use(WHO, t)
        scope = Scope(WHO, cat, t, rules, dialect)
        sel_cols: list[exp.Expr] = []
        date: exp.Expr | None = None
        for out in outs:
            p = cols[out]
            if p is None:
                sel_cols.append(exp.alias_(exp.Null(), out, quoted=True))
                continue
            node = {"col": p.col, **({"table": p.table} if p.table else {})}
            amount_like = out != date_out
            e = scope.column(node, where=f"column {out}", convert=amount_like)
            if stack is not None and label == stack.label and out in stack.negate:
                e = exp.Neg(this=exp.Paren(this=e))
            if out == date_out:
                date = scope.column(node, where="date", convert=False)
            sel_cols.append(exp.alias_(e, out, quoted=True))
        if label is not None and stack is not None:
            sel_cols.append(exp.alias_(lit(label), stack.kind_out, quoted=True))
        assert date is not None
        # rendered before the FROM: a filter on a joined table (region on customers) adds its
        # join to the scope, and `_from` writes every join the scope has
        conds = _where(scope, {"filters": own})  # the table's rules, then the number's own
        sel = _from(scope, exp.select(*sel_cols))
        for c in conds:
            sel = sel.where(c)
        sel = sel.where(exp.GTE(this=date.copy(), expression=exp.cast(placeholder("from"), "DATE")))
        sel = sel.where(exp.LT(this=date.copy(), expression=exp.cast(placeholder("to"), "DATE")))
        parts.append((sel, scope))
        for alias, used in sorted(scope.used.items(), key=lambda kv: (kv[0] != FACT, kv[0])):
            name = t.name if alias == FACT else scope.joined[alias].name
            read.columns.setdefault(name, [])
            read.columns[name] = sorted(set(read.columns[name]) | used)
            if name not in read.tables:
                read.tables.append(name)

    part(fact, {p.out: p for p in picks}, stack.fact_label if stack else None, filters or [])
    if stack is not None:
        if stack.table.get("source") != fact.get("source"):
            raise CompileError(f"{WHO}: a stacked table must be on the same source")
        st = _table(cat, stack.table, WHO)
        cols: dict[str, Pick | None] = {}
        for p in picks:
            own = stack.cols.get(p.out)
            if own:  # the stacked table's own column, however the fact table reaches it
                cols[p.out] = Pick(p.out, own)
            elif p.table is None:
                cols[p.out] = None
            elif (st.stable_key, p.table) in rules.joins:
                cols[p.out] = p
            else:
                cols[p.out] = None
        if cols.get(date_out) is None:
            raise CompileError(f"{WHO}: the stacked table needs its own date column")
        part(stack.table, cols, stack.label, stack.filters)

    for sel, scope in parts:
        recheck(WHO, to_sql(sel, dialect), dialect, scope)
    if len(parts) == 1:
        tree: exp.Expr = parts[0][0].limit(limit)
    else:
        tree = exp.union(parts[0][0], parts[1][0], distinct=False).limit(limit)
    read.sql = to_sql(tree, dialect)
    return read
