# SPDX-License-Identifier: Apache-2.0
"""A few real rows behind a choice in the Othor AI chat (0.26, M26.3; owner, 18 Nov 2026:
"previews show real values only, from a query code builds from names").

Code builds the query from the names a choice carries (source, table, column), never from a
model and never with a made-up value:
- **values**: the latest few values of one column (by a date column, or the largest first);
- **counts**: a column's most common values, each with how many rows have it.

Every query goes through the compiler's `Scope` (a column only if scanned, never a personal one,
never an ignored or Excluded table, the table's own filter rules applied) and `guard.recheck`, then
runs through `othor.connectors.read.run` (read-only, personal columns re-checked, only the SQL and
the row count logged). The rows go to the screen only: never stored, never sent to a model
(non-negotiable 3)."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlglot import exp

from othor.ontology.compiler.build import _from, _rules, _table, _where
from othor.ontology.compiler.catalog import Catalog
from othor.ontology.compiler.guard import recheck
from othor.ontology.compiler.render import FACT, CompileError, Scope, check_use, to_sql

WHO = "a preview"
ROWS = 6
TEXT = 48  # a long value is cut: the preview shows what it looks like, not all of it


@dataclass(frozen=True)
class Ask:
    """What to show: `kind` "values" or "counts" of `col` in `table`."""

    kind: str
    table: dict[str, str]  # {"source", "schema", "table"}
    col: str
    order: str | None = None  # values: newest first by this date column; None = largest first
    title: str = ""


@dataclass
class Preview:
    title: str
    head: list[str]
    rows: list[list[str]] = field(default_factory=list)
    note: str = ""  # a plain line under it (how many rows the table has)
    failed: str = ""  # the plain reason a read didn't work
    sql: str = ""  # for "Where this comes from"
    source: str = ""
    chart: Any = None  # a ChartSpec (move together), drawn by render_svg


def _words(name: str) -> str:
    return str(name).replace("_", " ")


def for_change(change: dict[str, Any] | None) -> Ask | None:
    """The preview a suggested answer's change has, from its own names (None: nothing to read;
    a pair's evidence is already computed and shown)."""
    if not change:
        return None
    body = change.get("body") or {}
    kind = body.get("kind")
    if change.get("type") == "rule" and kind == "reconcile" and body.get("on"):
        return Ask(
            "values",
            body["table"],
            str(body["on"][0][0]),
            order=body.get("date"),
            title=f"Latest {body.get('label') or 'numbers'} in {body['table']['table']}",
        )
    if change.get("type") == "rule" and kind == "threshold" and body.get("column"):
        return Ask(
            "values",
            body["table"],
            str(body["column"]),
            title=f"Largest {_words(body['column'])} in {body['table']['table']}",
        )
    ent = body.get("entity") if change.get("type") == "breakdown" else None
    if ent and ent.get("status_column"):
        return Ask(
            "counts",
            ent["table"],
            str(ent["status_column"]),
            title=f"{_words(ent['status_column']).capitalize()} in {ent['table']['table']}",
        )
    return None


def build(
    records: dict[tuple[str, str], dict[str, Any]], cat: Catalog, ask: Ask, *, limit: int = ROWS
) -> tuple[str, str, list[str]]:
    """(SQL, dialect, column heads). Raises CompileError / PersonalColumnRejected when the names
    don't hold (a column not scanned, a personal one, an ignored table)."""
    t = _table(cat, ask.table, WHO)
    check_use(WHO, t)
    dialect = t.dialect
    scope = Scope(WHO, cat, t, _rules(records, cat), dialect)
    col = scope.column({"col": ask.col}, where="the preview", convert=False)
    conds = _where(scope, {"filters": []})  # the table's own rules, as the nightly run reads it
    if ask.kind == "counts":
        n = exp.alias_(exp.Count(this=exp.Star()), "n", quoted=True)
        sel = _from(scope, exp.select(exp.alias_(col.copy(), "value", quoted=True), n))
        for c in conds:
            sel = sel.where(c)
        sel = sel.group_by(col.copy()).order_by(exp.Ordered(this=exp.column("n"), desc=True))
        heads = [_words(ask.col), "rows"]
    elif ask.kind == "values":
        picks: list[exp.Expr] = [exp.alias_(col.copy(), "value", quoted=True)]
        date = None
        if ask.order:
            tbl, _dot, name = ask.order.rpartition(".")  # "fact_dispatch.dispatch_date": joined
            node = {"col": name, **({"table": tbl} if tbl else {})}
            date = scope.column(node, where="the preview's date", convert=False)
            picks.append(exp.alias_(date.copy(), "on", quoted=True))
        sel = _from(scope, exp.select(*picks))
        sel = sel.where(exp.Not(this=exp.Is(this=col.copy(), expression=exp.Null())))
        for c in conds:
            sel = sel.where(c)
        key = date if date is not None else col
        sel = sel.order_by(exp.Ordered(this=key.copy(), desc=True))
        heads = [_words(ask.col)] + ([_words(ask.order.rpartition(".")[2])] if ask.order else [])
    else:
        raise CompileError(f"{WHO}: unknown kind {ask.kind}")
    sel = sel.limit(limit)
    sql = to_sql(sel, dialect)
    recheck(WHO, sql, dialect, scope)
    assert FACT in scope.used
    return sql, dialect, heads


def cell(v: Any) -> str:
    """A value as the page shows it, formatted by code."""
    from othor.core import fmt

    if v is None:
        return "—"
    if isinstance(v, dt.datetime):
        return fmt.day_month_year(v.date())
    if isinstance(v, dt.date):
        return fmt.day_month_year(v)
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int | Decimal) or (isinstance(v, float) and v == v):
        return fmt.num(v, decimals=0 if float(v).is_integer() else 2)
    text = str(v).strip()
    return text if len(text) <= TEXT else text[: TEXT - 1] + "…"


def read(
    ctx: Any,
    company_id: uuid.UUID,
    records: dict[tuple[str, str], dict[str, Any]],
    cat: Catalog,
    ask: Ask,
    limit: int = ROWS,
) -> Preview:
    """Read it live. A failure says so plainly (the answer can still be applied)."""
    from othor.connectors.read import Unreachable, run
    from othor.core import fmt

    out = Preview(title=ask.title, head=[], source=str(ask.table.get("source") or ""))
    try:
        sql, _d, heads = build(records, cat, ask, limit=limit)
    except CompileError:
        out.failed = (
            "I can't show rows for this one: the table or column has changed since the scan."
        )
        return out
    out.head, out.sql = heads, sql
    try:
        rows, _names, _ms = run(ctx, company_id, cat, str(ask.table["source"]), sql, {})
    except Unreachable as e:
        out.failed = f"I couldn't read {out.source} just now: {e}"
        return out
    out.rows = [[cell(v) for v in r] for r in rows]
    t = cat.table(ask.table["source"], ask.table["schema"], ask.table["table"])
    if t is not None and t.row_count:
        out.note = f"{fmt.num(t.row_count)} rows in {t.name} at the last scan."
    return out


# --- from stored results, no source read (0.26.2) --------------------------------------------


def pair_chart(rd: Any, a: str, b: str, weeks: int = 12) -> Any:
    """ "Move together": the two numbers' last weeks from the night's stored series, each indexed
    to 100 at its first week so both read on one scale (a ChartSpec; render_svg draws it)."""
    from decimal import Decimal as D

    from othor.briefs import tiles
    from othor.charts.spec import ChartSpec, Period, Series, Unit
    from othor.web import run_cache

    if rd.files is None or rd.run is None:
        return None
    by = run_cache.by_metric(rd.files.series)
    series = []
    names = []
    period = None
    for key, role in ((a, "neutral"), (b, "muted")):
        pts = tiles._series(list(by.get(key, ())), key, "week")[-weeks:]
        if len(pts) < 4 or not pts[0][1]:
            return None
        base = D(pts[0][1])
        name = str((rd.spec.get("metrics", {}).get(key) or {}).get("name") or key)
        names.append(name)
        period = period or (pts[0][0].isoformat(), pts[-1][0].isoformat())
        series.append(
            Series(
                key,
                name,
                role,  # type: ignore[arg-type]
                tuple((p.isoformat(), float(D(v) / base * 100)) for p, v in pts),
            )
        )
    assert period is not None
    return ChartSpec(
        id=f"pair:{a}:{b}:{period[1]}",
        kind="line",
        title=f"{names[0]} and {names[1]} · last {weeks} weeks, each from 100",
        unit=Unit("count"),
        period=Period("week", period[0], period[1]),
        series=tuple(series),
        alt_text=f"{names[0]} and {names[1]}, weekly, each indexed to 100 at {period[0]}.",
        provenance={"pair": f"{a},{b}", "run": str(rd.run.id)},
    )


def finding_rows(s: Any, fingerprint: str, limit: int = 6) -> Preview:
    """ "Which system is right" (and any question from a finding): the records in question, as
    the night stored them with the finding (Sweep's own rows), up to `limit`."""
    import sqlalchemy as sa

    from othor.engine.models import Finding, Run

    row = s.scalars(
        sa.select(Finding)
        .join(Run, Run.id == Finding.run_id)
        .where(Finding.fingerprint == fingerprint)
        .order_by(Run.number.desc())
        .limit(1)
    ).first()
    out = Preview(title="The records in question", head=[])
    t = ((row.body or {}).get("text") or {}).get("rows") if row is not None else None
    if not t or not t.get("rows"):
        out.failed = "No records were kept with this finding."
        return out
    rows = list(t["rows"])[:limit]
    keep = [i for i in range(len(t["headers"])) if any(str(x[i]).strip() for x in rows)]
    out.head = [str(t["headers"][i]) for i in keep]
    out.rows = [[cell(x[i]) for i in keep] for x in rows]
    total = int(t.get("total") or len(t["rows"]))
    out.note = f"{len(rows)} of {total} from last night's check." if total > len(rows) else ""
    out.source = "last night's check"
    return out
