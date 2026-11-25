# SPDX-License-Identifier: Apache-2.0
"""Build SQL from the expression AST with sqlglot, per dialect (docs/04 §1).

Nothing from a record is concatenated into SQL: identifiers come from the scanned catalog (and
are quoted by sqlglot), literals become sqlglot literals. Every column reference is resolved
through `Scope.column`, which applies unit-conversion rules and refuses personal columns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlglot import exp

from othor.ontology.compiler.catalog import Catalog, TableInfo

FACT = "t"


class CompileError(ValueError):
    """The definition can't be compiled (unknown table, column or join)."""


class PersonalColumnRejected(CompileError):
    """A definition touches a personal column. Names the metric and table, never values."""

    def __init__(self, metric: str, table: str, where: str) -> None:
        super().__init__(f"{metric}: {where} uses a personal column of {table}")
        self.metric, self.table, self.where = metric, table, where


def ident(name: str) -> exp.Identifier:
    return exp.to_identifier(name, quoted=True)


def table_expr(t: TableInfo, alias: str) -> exp.Table:
    if t.duck_table:  # Sheets tab extracted into the analytics store
        return exp.Table(this=ident(t.duck_table), alias=exp.TableAlias(this=ident(alias)))
    return exp.Table(
        this=ident(t.name), db=ident(t.schema), alias=exp.TableAlias(this=ident(alias))
    )


def lit(v: Any) -> exp.Expr:
    if v is None:
        return exp.Null()
    if isinstance(v, bool):
        return exp.Boolean(this=v)
    if isinstance(v, int):
        return exp.Literal.number(v)
    if isinstance(v, str) and re.fullmatch(r"-?\d+\.\d+", v):
        return exp.Literal.number(v)  # decimal strings: exact numbers
    return exp.Literal.string(str(v))


@dataclass
class Rules:
    """The rules that apply while rendering one metric."""

    conversions: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    filters: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # stable_key → exprs
    joins: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)  # (from, to)
    vocab_joins: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # stable_key
    vocabularies: dict[str, dict[str, Any]] = field(default_factory=dict)
    allocations: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)  # (metric, bd)
    aliases: dict[str, dict[str, str]] = field(default_factory=dict)  # source → term → means


@dataclass
class Scope:
    """One metric's FROM: the fact table and the tables joined to it."""

    metric: str
    catalog: Catalog
    fact: TableInfo
    rules: Rules
    dialect: str
    joined: dict[str, TableInfo] = field(default_factory=dict)  # alias → table
    join_on: dict[str, exp.Expr] = field(default_factory=dict)
    used: dict[str, set[str]] = field(default_factory=dict)  # alias → columns read
    # a parameter rendered as an expression instead of a bound value (the back-fill calendar)
    params: dict[str, exp.Expr] = field(default_factory=dict)

    def table_for(self, name: str | None, where: str) -> tuple[str, TableInfo]:
        if name is None or name == self.fact.name:
            return FACT, self.fact
        if name in self.joined:
            return name, self.joined[name]
        rule = self.rules.joins.get((self.fact.stable_key, name))
        if rule is None:
            raise CompileError(
                f"{self.metric}: {where} reads {name}, but no join rule links "
                f"{self.fact.name} to it"
            )
        target = self.catalog.by_name(rule["to"]["source"], rule["to"]["table"])
        if target is None:
            raise CompileError(f"{self.metric}: join target {rule['to']['table']} isn't scanned")
        check_use(self.metric, target)
        on: exp.Expr | None = None
        for a, b in rule["on"]:
            left = self.column({"col": a}, where=f"join {self.fact.name}→{name}", convert=False)
            if rule.get("via") == "alias":
                # a match by name: their term → the entity's name through alias records only,
                # never on the raw names (decided 5 Nov 2026)
                terms = self.rules.aliases.get(self.fact.source_name, {})
                left = exp.Case(
                    this=left,
                    ifs=[
                        exp.If(this=lit(term), true=lit(means))
                        for term, means in sorted(terms.items())
                    ],
                    default=exp.Null(),
                )
            right = self._raw(name, target, b, where=f"join {self.fact.name}→{name}")
            cond = exp.EQ(this=left, expression=right)
            on = cond if on is None else exp.And(this=on, expression=cond)
        assert on is not None
        self.joined[name] = target
        self.join_on[name] = on
        return name, target

    def _raw(self, alias: str, t: TableInfo, col: str, *, where: str) -> exp.Column:
        info = t.column(col)
        if info is None:
            raise CompileError(f"{self.metric}: {where}: {t.name}.{col} isn't in the scan")
        if info.personal:
            raise PersonalColumnRejected(self.metric, f"{t.schema}.{t.name}", where)
        self.used.setdefault(alias, set()).add(info.name)
        return exp.Column(this=ident(info.name), table=ident(alias))

    def date_column(self, node: dict[str, Any], *, where: str) -> exp.Expr:
        """A number's date column as its window compares it (#112, 24 Nov 2026): a Sheets column
        (read into DuckDB, often as text) or a database column the scan found to be text is
        cast to DATE, as the period already is; a database's real date or timestamp column is
        compared as it is, so its index still serves the incremental read."""
        alias, t = self.table_for(node.get("table"), where)
        c = self._raw(alias, t, node["col"], where=where)
        info = t.column(node["col"])
        is_date = info is not None and str(info.kind).lower() in ("date", "timestamp")
        if t.source_kind == "gsheets" or not is_date:
            return exp.cast(c, "DATE")
        return c

    def column(self, node: dict[str, Any], *, where: str, convert: bool = True) -> exp.Expr:
        alias, t = self.table_for(node.get("table"), where)
        c = self._raw(alias, t, node["col"], where=where)
        if not convert:
            return c
        rules = self.rules.conversions.get((t.stable_key, c.name.lower()), [])
        out: exp.Expr = c
        for r in rules:  # unit conversion: CASE WHEN <when> THEN col * factor ELSE col END
            cond = render(r["when"], self, where=f"{where} (unit rule)", convert=False)
            # widened first: a long factor (÷ 1.18 as 0.847457627118644) times a large amount
            # overflows an 18-digit decimal on some engines; 38 digits keep the product exact
            wide = exp.cast(out.copy(), exp.DataType.build("DECIMAL(38, 6)"))
            out = exp.Case(
                ifs=[exp.If(this=cond, true=exp.Mul(this=wide, expression=lit(r["factor"])))],
                default=out.copy(),
            )
        return out


class TableNotInUse(CompileError):
    """A number reads a table the owner set aside (ignored). The compiler leaves out that number,
    marked as needing a source, instead of failing the company's night (owner, 23 Nov 2026:
    ts-test-001's nights failed after its sheet's old tab was set to ignored)."""

    def __init__(self, metric: str, table: str) -> None:
        super().__init__(f"{metric}: {table} is ignored; use it first")
        self.metric, self.table = metric, table


def check_use(metric: str, t: TableInfo) -> None:
    if t.use == "excluded" and not t.without_personal:
        raise PersonalColumnRejected(metric, f"{t.schema}.{t.name}", "an Excluded table")
    if t.use not in ("metric_source", "dimension", "excluded"):
        raise TableNotInUse(metric, f"{t.schema}.{t.name}")


def placeholder(name: str) -> exp.Expr:
    return exp.Placeholder(this=name)


def render(node: dict[str, Any], scope: Scope, *, where: str, convert: bool = True) -> exp.Expr:
    d = scope.dialect
    if "col" in node:
        return scope.column(node, where=where, convert=convert)
    if "lit" in node:
        return lit(node["lit"])
    if "param" in node:
        if node["param"] in scope.params:
            return scope.params[node["param"]].copy()
        return exp.cast(placeholder(node["param"]), "DATE")
    if "case" in node:
        ifs = [
            exp.If(
                this=render(c, scope, where=where, convert=convert),
                true=render(v, scope, where=where, convert=convert),
            )
            for c, v in node["case"]
        ]
        default = (
            render(node["else"], scope, where=where, convert=convert) if "else" in node else None
        )
        return exp.Case(ifs=ifs, default=default)
    if "fn" in node:
        a = [render(x, scope, where=where, convert=convert) for x in node.get("args", [])]
        fn = node["fn"]
        if fn == "coalesce":
            return exp.Coalesce(this=a[0], expressions=a[1:])
        if fn == "abs":
            return exp.Abs(this=a[0])
        if fn == "date":
            return exp.cast(a[0], "DATE")
        if fn == "add_days":
            return exp.DateAdd(this=a[0], expression=a[1], unit=exp.var("DAY"))
        if fn == "days_between":  # fractional days, a − b
            return days_between(a[0], a[1], d)
        raise CompileError(f"{scope.metric}: {fn} isn't available here")
    if "op" in node:
        op, args = (
            node["op"],
            [render(x, scope, where=where, convert=convert) for x in node["args"]],
        )
        binary = {
            "+": exp.Add,
            "-": exp.Sub,
            "*": exp.Mul,
            "=": exp.EQ,
            "<>": exp.NEQ,
            "<": exp.LT,
            "<=": exp.LTE,
            ">": exp.GT,
            ">=": exp.GTE,
        }
        if op in binary:
            return binary[op](this=args[0], expression=args[1])
        if op == "/":
            return exp.Div(this=args[0], expression=exp.Nullif(this=args[1], expression=lit(0)))
        if op in ("and", "or"):
            cls = exp.And if op == "and" else exp.Or
            out = args[0]
            for x in args[1:]:
                out = cls(this=out, expression=x)
            return exp.Paren(this=out)
        if op == "not":
            return exp.Not(this=args[0])
        if op == "is_null":
            return exp.Is(this=args[0], expression=exp.Null())
        if op == "not_null":
            return exp.Not(this=exp.Is(this=args[0], expression=exp.Null()))
        if op == "in":
            return exp.In(this=args[0], expressions=args[1:])
    raise CompileError(f"{scope.metric}: can't render {sorted(node)}")


def days_between(a: exp.Expr, b: exp.Expr, dialect: str) -> exp.Expr:
    """a − b in days, with fractions for timestamps (dispatch time 4.1 d)."""
    if dialect == "mysql":
        secs: exp.Expr = exp.Anonymous(this="TIMESTAMPDIFF", expressions=[exp.var("SECOND"), b, a])
    elif dialect == "postgres":
        secs = exp.Extract(
            this=exp.var("EPOCH"),
            expression=exp.Paren(
                this=exp.Sub(this=exp.cast(a, "TIMESTAMP"), expression=exp.cast(b, "TIMESTAMP"))
            ),
        )
    else:
        secs = exp.Anonymous(
            this="DATE_DIFF",
            expressions=[
                exp.Literal.string("second"),
                exp.cast(b, "TIMESTAMP"),
                exp.cast(a, "TIMESTAMP"),
            ],
        )
    return exp.Div(this=secs, expression=exp.Literal.number("86400.0"))


def period(col: exp.Expr, grain: str, dialect: str) -> exp.Expr:
    """Day; Monday weeks; calendar months (fiscal roll-up is done from these)."""
    d = exp.cast(col, "DATE")
    if grain == "day":
        return d
    if grain == "month":
        return exp.cast(exp.DateTrunc(this=d.copy(), unit=exp.Literal.string("MONTH")), "DATE")  # type: ignore[no-untyped-call]
    if dialect == "mysql":  # MySQL has no week trunc: subtract WEEKDAY() (0 = Monday)
        return exp.DateSub(
            this=d,
            expression=exp.Anonymous(this="WEEKDAY", expressions=[d.copy()]),
            unit=exp.var("DAY"),
        )
    return exp.cast(exp.DateTrunc(this=d, unit=exp.Literal.string("WEEK")), "DATE")  # type: ignore[no-untyped-call]  # ISO Monday


def prefix_case(col: exp.Expr, vocab: dict[str, Any]) -> exp.Expr:
    """CASE WHEN code LIKE 'SU-%' THEN 'Surat' … ELSE 'Unmapped' END, from a prefix_map record."""
    ifs = [
        exp.If(
            this=exp.Like(this=col.copy(), expression=exp.Literal.string(prefix + "%")),
            true=exp.Literal.string(label),
        )
        for prefix, label in sorted(vocab["entries"].items())
    ]
    return exp.Case(ifs=ifs, default=exp.Literal.string(vocab.get("other", "Unmapped")))


AGG = {"sum": exp.Sum, "avg": exp.Avg, "min": exp.Min, "max": exp.Max, "stddev": exp.Stddev}


def aggregate(agg: str, value: exp.Expr | None) -> exp.Expr:
    if agg == "count":
        return exp.Count(this=exp.Star()) if value is None else exp.Count(this=value)
    if agg == "count_distinct":
        assert value is not None
        return exp.Count(this=exp.Distinct(expressions=[value]))
    if agg == "last":
        assert value is not None
        return exp.Sum(this=value)
    assert value is not None
    return AGG[agg](this=value)


def to_sql(e: exp.Expr, dialect: str) -> str:
    sql = e.sql(dialect=dialect, pretty=True)
    # one bound-parameter style for SQL sources (SQLAlchemy text): :name
    return re.sub(r"%\((\w+)\)s", r":\1", sql)
