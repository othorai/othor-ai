# SPDX-License-Identifier: Apache-2.0
"""The `probes:<source>` step (M6): the aggregates Sweep reads beyond the series, compiled from
Ontology records (othor/ontology/compiler/probes.py), sent read-only through the query log and
stored in the run's inputs (`inputs/probes_<source>.json`), so replay reads them, never the
source. Personal columns are refused at compile time and again by the query log's guard.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from othor.engine import periods
from othor.engine.context import EngineContext
from othor.engine.sources import Executor, RunInfo, SourceOutcome, _jsonable
from othor.engine.store import write_json


def sources_with_probes(spec: dict[str, Any]) -> list[str]:
    p = spec.get("probes") or {}
    names: set[str] = set()
    for e in (p.get("entities") or {}).values():
        names.add(e["source"])
    for t in p.get("thresholds") or []:
        names.add(t["source"])
    for d in p.get("distributions") or []:
        names.add(d["source"])
    for r in p.get("reconcile") or []:
        if r["kind"] == "keys":
            names |= {r["left"]["source"], r["right"]["source"]}
    for m in p.get("matches") or []:
        names.add(m["source"])
    return sorted(n for n in names if n in spec.get("sources", {}))


def _num(v: Any) -> str | None:
    return None if v is None else str(Decimal(str(v)))


def run(ctx: EngineContext, info: RunInfo, source: str) -> SourceOutcome:
    spec = info.spec.get("probes") or {}
    as_of = periods.as_of(info.run_date)
    out: dict[str, Any] = {
        "entities": {},
        "thresholds": [],
        "distributions": [],
        "keys": [],
        "matches": [],
    }
    with Executor(ctx, info, source) as ex:
        for key, e in sorted((spec.get("entities") or {}).items()):
            if e["source"] != source:
                continue
            rows = ex.run(e["sql"])
            out["entities"][key] = sorted(
                [[_s(r[0]), _s(r[1]), _s(r[2]), _date(r[3]), *[_s(x) for x in r[4:]]] for r in rows]
            )
        for t in spec.get("thresholds") or []:
            if t["source"] != source:
                continue
            params = (
                {"from": as_of - dt.timedelta(days=365), "to": info.run_date}
                if t.get("dated")
                else {}
            )
            r = ex.run(t["sql"], params)[0]
            out["thresholds"].append(
                {
                    "key": t["key"],
                    "below": int(r[0] or 0),
                    "above": int(r[1] or 0),
                    "events": int(r[2] or 0),
                }
            )
        for d in spec.get("distributions") or []:
            if d["source"] != source:
                continue
            frm = info.run_date - dt.timedelta(days=int(d.get("days", 365)))
            rows = ex.run(d["sql"], {"from": frm, "to": info.run_date})
            out["distributions"].append(
                {
                    "key": d["key"],
                    "values": sorted(str(Decimal(str(r[1]))) for r in rows if r[1] is not None),
                }
            )
        for r in spec.get("reconcile") or []:
            if r["kind"] != "keys":
                continue
            for side in ("left", "right"):
                if r[side]["source"] != source:
                    continue
                params = (
                    {"cutoff": as_of - dt.timedelta(days=int(r["lag_days"]))}
                    if side == "left"
                    else {}
                )
                rows = ex.run(r[side]["sql"], params)
                if side == "left":
                    got: list[Any] = sorted(
                        ([_s(x[0]), _date(x[1]), _num(x[2])] for x in rows),
                        key=lambda row: [str(v) for v in row],
                    )
                else:
                    got = sorted(str(x[0]) for x in rows if x[0] is not None)
                out["keys"].append({"key": r["key"], "side": side, "rows": got})
        for m in spec.get("matches") or []:
            if m["source"] != source:
                continue
            rows = ex.run(m["sql"])
            vals = sorted(
                {str(r[0]).strip() for r in rows if r[0] is not None and str(r[0]).strip()}
            )
            out["matches"].append({"key": m["key"], "values": vals})
        queries, ms = ex.save_log()
    out["keys"].sort(key=lambda k: (k["key"], k["side"]))
    out["matches"].sort(key=lambda m: m["key"])
    write_json(info.files.inputs / f"probes_{_safe(source)}.json", out)
    return SourceOutcome(True, {"queries": queries, "ms": ms})


def assemble(
    spec: dict[str, Any],
    files: dict[str, dict[str, Any]],
    inputs: list[Any],
    outputs: list[Any],
) -> dict[str, Any]:
    """The detectors' probe input: probe files, each entity's daily activity from the raw
    inputs (every entity, before the breakdown cap), and period totals from the outputs."""
    out: dict[str, Any] = {"entities": {}, "thresholds": [], "distributions": [], "reconcile": []}
    for key, e in sorted((spec.get("entities") or {}).items()):
        status = (files.get(e["source"]) or {}).get("entities", {}).get(key)
        if status is None:
            continue
        events = [
            [p.breakdown_value, p.period.isoformat(), str(p.value)]
            for p in inputs
            if p.metric == e["metric"]
            and p.grain == "day"
            and p.breakdown == key
            and p.value
            and p.value > 0
        ]
        attrs = list(e.get("attributes") or [])
        out["entities"][key] = {
            "metric": e["metric"],
            "events": sorted(events),
            "status": [row[:4] for row in status],
            "active_values": e.get("active_values", []),
            # each entity's other breakdown values (a customer's region), for drill-downs
            "attributes": {
                a: {row[0]: row[4 + i] for row in status if len(row) > 4 + i and row[0]}
                for i, a in enumerate(attrs)
            },
        }
    for t in spec.get("thresholds") or []:
        got = next(
            (
                x
                for x in (files.get(t["source"]) or {}).get("thresholds", [])
                if x["key"] == t["key"]
            ),
            None,
        )
        if got:
            out["thresholds"].append(
                {**got, "label": t.get("label", ""), "limit": t["limit"], "band": t["band"]}
            )
    for d in spec.get("distributions") or []:
        got = next(
            (
                x
                for x in (files.get(d["source"]) or {}).get("distributions", [])
                if x["key"] == d["key"]
            ),
            None,
        )
        if got:
            out["distributions"].append(
                {"key": d["key"], "metric": d["metric"], "values": got["values"]}
            )
    for r in spec.get("reconcile") or []:
        if r["kind"] == "periods":
            out["reconcile"].append({**r, "rows": _period_rows(outputs, r)})
            continue
        sides = {}
        for side in ("left", "right"):
            f = files.get(r[side]["source"]) or {}
            hit = next(
                (k for k in f.get("keys", []) if k["key"] == r["key"] and k["side"] == side), None
            )
            sides[side] = hit["rows"] if hit else None
        if sides["left"] is None or sides["right"] is None:
            continue
        right = set(sides["right"])
        unmatched = [row for row in sides["left"] if row[0] not in right]
        out["reconcile"].append(
            {
                "key": r["key"],
                "kind": "keys",
                "lag_days": r["lag_days"],
                "left_label": r["left"]["label"],
                "right_label": r["right"]["label"],
                "left_source": r["left"]["source"],
                "right_source": r["right"]["source"],
                "right_table": r["right"].get("table", ""),
                "noun": r.get("noun", "records"),
                "unmatched": unmatched,
                "left_count": len(sides["left"]),
            }
        )
    return out


def _period_rows(points: list[Any], r: dict[str, Any]) -> list[list[str | None]]:
    grain = r.get("grain") or "month"
    vals: dict[str, dict[str, str]] = {"a": {}, "b": {}}
    for p in points:
        if p.grain == grain and not p.breakdown and p.value is not None and p.complete:
            if p.metric == r["a"]:
                vals["a"][p.period.isoformat()] = str(p.value)
            elif p.metric == r["b"]:
                vals["b"][p.period.isoformat()] = str(p.value)
    both = sorted(set(vals["a"]) & set(vals["b"]))
    return [[d, vals["a"][d], vals["b"][d]] for d in both]


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def _date(v: Any) -> str | None:
    if v is None:
        return None
    j = _jsonable(v.date() if isinstance(v, dt.datetime) else v)
    return str(j)[:10]


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
