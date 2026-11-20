# SPDX-License-Identifier: Apache-2.0
"""Typed patches over a metric body (M4b): what a chat proposes, by a quick reply or by Claude.

A patch is data, never SQL. `apply_patch` returns the new body (validated by the M3 types), any
records it needs (a unit conversion is a rule), and the change written line by line for the draft
card. The caller compiles and computes the value. Anything the scan doesn't know, a personal
column, an unknown operator or a patch that doesn't fit the definition is refused with a reason.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from othor.ontology.compiler.catalog import Catalog, TableInfo
from othor.ontology.types import validate_body, walk

KINDS = (
    "use_column",
    "use_duration",
    "add_filter",
    "set_threshold",
    "exclude",
    "convert_units",
    "coalesce_date",
    "set_breakdowns",
    "rename",
)
OPS = {"=": "=", "<>": "<>", "≠": "<>", ">": ">", "<": "<", ">=": ">=", "<=": "<="}
Records = dict[tuple[str, str], dict[str, Any]]


class PatchRefused(ValueError):
    pass


@dataclass(frozen=True)
class Patch:
    kind: str
    args: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "args": self.args}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Patch:
        return cls(str(data.get("kind", "")), dict(data.get("args") or {}))


@dataclass
class Patched:
    body: dict[str, Any]
    records: list[tuple[str, str, dict[str, Any]]]
    lines: list[str]


def _table(body: dict[str, Any], cat: Catalog) -> TableInfo:
    t = body.get("table") or {}
    info = cat.tables.get((t.get("source", ""), t.get("schema", ""), t.get("table", "")))
    if info is None:
        raise PatchRefused("this metric has no scanned table to change")
    return info


def _column(t: TableInfo, name: str, what: str = "column") -> str:
    c = t.column(name or "")
    if c is None:
        raise PatchRefused(f"{t.schema}.{t.name} has no {what} {name!r}")
    if c.personal:
        raise PatchRefused(f"{name} is a personal column; Othor AI never reads it")
    return c.name


def _breakdown_column(bd: dict[str, Any], t: TableInfo, records: Records) -> dict[str, Any] | None:
    """The breakdown's column as this fact table reaches it: its own column, or one on a table
    a join rule links it to (the breakdown's table, then its alternatives)."""
    if bd.get("column") is None:  # an expression breakdown (0.29) has no column to reach
        return None
    options = [{"table": bd.get("table"), "column": bd["column"]}, *bd.get("alternatives", [])]
    joined = {
        (r.get("to") or {}).get("table")
        for (kind, _), r in records.items()
        if kind == "rule"
        and r.get("kind") == "join"
        and (r.get("table") or {}).get("table") == t.name
        and (r.get("table") or {}).get("source") == t.source_name
    }
    for o in options:
        if not o.get("table") and t.column(o["column"]) is not None:
            return {"col": o["column"]}
        if o.get("table") in joined:
            return {"col": o["column"], "table": o["table"]}
    return None


_DAYS_COL = re.compile(r"days|overdue|age(d|ing)?\b", re.I)


def is_threshold(f: dict[str, Any]) -> bool:
    """A days threshold: days_between(…) > N, or a days column (days_overdue) > N."""
    args = f.get("args") or []
    if f.get("op") not in (">", ">=") or len(args) != 2 or not isinstance(args[0], dict):
        return False
    if "lit" not in args[1] or not re.fullmatch(r"\d+", str(args[1]["lit"])):
        return False
    first = args[0]
    return first.get("fn") == "days_between" or bool(
        "col" in first and _DAYS_COL.search(str(first["col"]))
    )


def apply_patch(body: dict[str, Any], patch: Patch, cat: Catalog, records: Records) -> Patched:
    if patch.kind not in KINDS:
        raise PatchRefused(f"unknown change {patch.kind!r}")
    b = copy.deepcopy(body)
    a = patch.args
    out_records: list[tuple[str, str, dict[str, Any]]] = []
    lines: list[str] = []

    if patch.kind == "use_column":
        ref = a.get("table") or {}
        t = cat.tables.get((ref.get("source", ""), ref.get("schema", ""), ref.get("table", "")))
        if t is None:
            raise PatchRefused(f"no scanned table {ref.get('schema')}.{ref.get('table')}")
        col = _column(t, a.get("column", ""))
        b["table"] = {"source": t.source_name, "schema": t.schema, "table": t.name}
        agg = (b.get("measure") or {}).get("agg", "sum")
        b["measure"] = {"agg": agg if agg != "count" else "sum", "expr": {"col": col}}
        b.pop("formula", None)
        if a.get("date"):
            b["date"] = _column(t, a["date"], "date column")
        b["status"] = "active"
        lines.append(
            f"Use {t.schema}.{t.name}.{col}" + (f" by {b['date']}" if a.get("date") else "")
        )

    elif patch.kind == "use_duration":
        ref = a.get("table") or {}
        t = cat.tables.get((ref.get("source", ""), ref.get("schema", ""), ref.get("table", "")))
        if t is None:
            raise PatchRefused(f"no scanned table {ref.get('schema')}.{ref.get('table')}")
        start = _column(t, a.get("start", ""), "date column")
        end = _column(t, a.get("end", ""), "date column")
        b["table"] = {"source": t.source_name, "schema": t.schema, "table": t.name}
        b["measure"] = {
            "agg": "avg",
            "expr": {"fn": "days_between", "args": [{"col": end}, {"col": start}]},
        }
        b.pop("formula", None)
        b.pop("needs", None)
        b.update(date=end, unit="days", status="active", kind="flow")
        lines.append(f"Days from {start} to {end}, average, by {end}")

    elif patch.kind == "add_filter":
        t = _table(b, cat)
        col = _column(t, a.get("column", ""))
        op = OPS.get(str(a.get("op", "")))
        if op is None:
            raise PatchRefused(f"unknown operator {a.get('op')!r}")
        value = a.get("value")
        b.setdefault("filters", []).append({"op": op, "args": [{"col": col}, {"lit": value}]})
        lines.append(f"Only rows where {col} {a.get('op')} {value}")

    elif patch.kind == "set_threshold":
        new = int(a.get("value", 0))
        found = None
        for f in b.get("filters", []):
            if is_threshold(f):
                found = f
        if found is None:
            raise PatchRefused("this definition has no threshold to change")
        old = found["args"][1]["lit"]
        found["args"][1] = {"lit": new}
        lines.append(f"Threshold {old} → {new} days")
        if re.search(rf"\b{old}\b", b.get("name", "")):
            b["name"] = re.sub(rf"\b{old}\b", str(new), b["name"])
        if re.search(rf"\b{old}\b", b.get("description", "")):
            b["description"] = re.sub(rf"\b{old}\b", str(new), b["description"])

    elif patch.kind == "exclude":
        values = [str(v) for v in a.get("values", []) if str(v).strip()]
        if not values:
            raise PatchRefused("nothing to exclude")
        if a.get("breakdown"):
            bd = records.get(("breakdown", a["breakdown"]))
            if bd is None:
                raise PatchRefused(f"no breakdown {a['breakdown']!r} in the Business Model")
            t = _table(b, cat)
            col_ref = _breakdown_column(bd, t, records)
            if col_ref is None:
                raise PatchRefused(f"{t.name} can't be split by {a['breakdown']}")
            label = bd.get("name", a["breakdown"]).lower()
        else:
            t = _table(b, cat)
            col_ref = {"col": _column(t, a.get("column", ""))}
            label = col_ref["col"]
        b.setdefault("filters", []).append(
            {"op": "not", "args": [{"op": "in", "args": [col_ref, *({"lit": v} for v in values)]}]}
        )
        lines.append(f"Exclude {label} = {', '.join(values)}")

    elif patch.kind == "convert_units":
        t = _table(b, cat)
        col = _column(t, a.get("column", ""))
        when = a.get("when") or {}
        wcol = _column(t, when.get("column", ""))
        factor = str(a.get("factor", ""))
        if not re.fullmatch(r"\d+(\.\d+)?", factor):
            raise PatchRefused("the factor must be a number")
        key = re.sub(r"[^a-z0-9]+", "_", f"{col}_{when.get('value', '')}_units".lower()).strip("_")
        rule = {
            "kind": "unit_conversion",
            "text": f"{col} where {wcol} = {when.get('value')} × {factor}",
            "table": {"source": t.source_name, "schema": t.schema, "table": t.name},
            "column": col,
            "when": {"op": "=", "args": [{"col": wcol}, {"lit": when.get("value")}]},
            "factor": factor,
            "to_unit": a.get("to_unit") or b.get("unit"),
        }
        out_records.append(("rule", key, validate_body("rule", rule)))
        to = f" (to {a['to_unit']})" if a.get("to_unit") else ""
        lines.append(f"Convert {col} where {wcol} = {when.get('value')} × {factor}{to}")

    elif patch.kind == "coalesce_date":
        t = _table(b, cat)
        primary = _column(t, a.get("primary", ""))
        fallback = _column(t, a.get("fallback", ""))
        expr = (b.get("measure") or {}).get("expr")
        hits = [n for n in walk(expr) if n.get("col") == primary] if expr else []
        if not hits:
            raise PatchRefused(f"{primary} isn't in this definition")

        def sub(node: Any) -> Any:
            if isinstance(node, dict):
                if node.get("col") == primary and set(node) == {"col"}:
                    return {"fn": "coalesce", "args": [{"col": primary}, {"col": fallback}]}
                return {k: sub(v) for k, v in node.items()}
            if isinstance(node, list):
                return [sub(x) for x in node]
            return node

        b["measure"] = sub(b["measure"])
        lines.append(f"Start = {primary}, else {fallback}")

    elif patch.kind == "set_breakdowns":
        keys = [str(k) for k in a.get("breakdowns", [])]
        unknown = [k for k in keys if ("breakdown", k) not in records]
        if unknown:
            raise PatchRefused(f"no breakdown {unknown[0]!r} in the Business Model")
        before = ", ".join(b.get("breakdowns", [])) or "none"
        b["breakdowns"] = keys
        lines.append(f"Breakdowns {before} → {', '.join(keys) or 'none'}")

    elif patch.kind == "rename":
        name = str(a.get("name", "")).strip()[:120]
        if not name:
            raise PatchRefused("a name is needed")
        lines.append(f"Renamed to {name}")
        b["name"] = name

    try:
        b = validate_body("metric", b)
    except ValueError as e:
        raise PatchRefused(str(e).splitlines()[-1].strip()) from e
    return Patched(b, out_records, lines)
