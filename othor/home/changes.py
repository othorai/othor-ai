# SPDX-License-Identifier: Apache-2.0
"""Change with Othor AI (6a3, M10b): the owner's line → one typed Ontology change, and its
computed effect before Apply.

- The model (`ontology_change` v1) drafts `{op, type, key, body}`. Code checks it: allowed type and
  fields, the record schema, the numbers guard (a figure must be in the input or the owner's own
  words, as written or scaled to lakh or crore), and the numbers-in-names rule.
- The effect is code only: the candidate version is compiled in memory (nothing written) and
  compared with the current spec: which metrics change meaning, which briefs and watches read
  them, which known patterns come or go.
- Today's value before and after, for up to three changed metrics, through the read-only role,
  bounded (aggregates only, 20 s) and logged in `run_query` as purpose "preview"; never part of a
  run, never stored as a value. Sheets metrics read their stored extract. A source that can't be
  reached gives "Values after tonight's run".
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.core import fmt
from othor.ontology import service
from othor.ontology.types import validate_body

PROMPT = ("ontology_change", "v3")  # v2: body as body_json (12 Nov 2026); v3: area (M16.1)
PREVIEW_TIMEOUT_S = 20
PREVIEW_METRICS = 3
AFTER_TONIGHT = "Values after tonight's run"
NOT_AVAILABLE = "Not available right now"
# a drafted change that the checks refused: said plainly, never the model's "This adds…" (#66)
COULDNT = (
    "I couldn’t turn that into a change I can check. Say which number and how it’s worked out "
    "(from which table and column), or what changed in the business and when."
)
ALLOWED = {
    "business_fact",
    "goal",
    "vocabulary",
    "alias",
    "customer",
    "known_pattern",
    "owner",
    "metric",
    "rule",
    "breakdown",
    "watch",  # 2.4 L2: not a record; started on Apply (othor.engine.watches), no version
}
METRIC_FIELDS = {"name", "description", "breakdowns", "area"}
# Business Model 2.4 (L2): a number's headline flag; a derived number's formula over other numbers
CHANGE_FIELDS = METRIC_FIELDS | {"headline", "formula"}
# a new number: one table and one measure (a part), or a formula over numbers (built from parts)
NEW_METRIC_FIELDS = CHANGE_FIELDS | {
    "grain",
    "unit",
    "kind",
    "table",
    "measure",
    "date",
    "filters",
    "listed",
    "family",
    "variant",
}
RULE_KINDS = ("pair", "reconcile", "filter", "unit_conversion")
WATCH_DIRECTIONS = ("above", "below")
MODEL_TABLES = 8  # tables named by the owner, sent by name with their columns (never rows)
SECTION = {
    "business_fact": "THE BUSINESS",
    "goal": "THE BUSINESS",
    "metric": "NUMBERS",
    "rule": "NUMBERS",
    "known_pattern": "KNOWN PATTERNS",
    "breakdown": "NUMBERS",
    "watch": "WATCHES",
}
SCALES = (Decimal(1), Decimal(1000), Decimal(100000), Decimal(1000000), Decimal(10000000))


@dataclass
class Draft:
    change: dict[str, Any] | None
    reply: str
    refused: str | None = None


@dataclass
class Effect:
    metrics: list[str] = field(default_factory=list)  # names of metrics whose meaning changes
    briefs: list[str] = field(default_factory=list)
    watches: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)  # "+ name" / "− name"
    values: list[tuple[str, str, str]] = field(default_factory=list)  # (name, before, after)
    note: str | None = None  # AFTER_TONIGHT when a source couldn't be reached
    error: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics,
            "briefs": self.briefs,
            "watches": self.watches,
            "patterns": self.patterns,
            "values": [list(v) for v in self.values],
            "note": self.note,
            "error": self.error,
        }


# --- inputs ------------------------------------------------------------------------------------


def model_inputs(s: Session, line: str, subject: str, today: dt.date) -> dict[str, Any]:
    from othor.home.answers import calendar_inputs

    recs = []
    for r in service.active(s, sorted(ALLOWED)):
        body = dict(r.body)
        if r.type == "metric":
            body = {k: v for k, v in body.items() if k in METRIC_FIELDS | {"grain", "status"}}
        if r.type == "rule" and body.get("kind") not in ("pair",):
            continue
        if "values" in body:  # a forbidden key in model input (raw rows travel as "values")
            body["value_list"] = ", ".join(str(v) for v in body.pop("values") or [])
        recs.append({"type": r.type, "key": r.key, "body": body})
    return {
        "line": line[:500],
        "subject": subject,
        "records": recs,
        "breakdowns": sorted({r.key for r in service.active(s, ["breakdown"])}),
        "tables": tables_named(s, line, subject),
        "calendar": calendar_inputs(today),
    }


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


def tables_named(s: Session, line: str, subject: str) -> list[dict[str, Any]]:
    """The tables a new number may read (2.4 L2): those the owner names (by table name, or
    source.table), and the subject number's own table, by name with their non-personal columns
    and date columns. Names only, never rows (rule 3); personal columns are left out."""
    from othor.ontology.compiler.catalog import load_catalog

    cat = load_catalog(s)
    said = _words(line)
    own: set[tuple[str, str, str]] = set()
    if subject.startswith("metric:"):
        key = subject.removeprefix("metric:")
        rec = next((r for r in service.active(s, ["metric"]) if r.key == key), None)
        tb = (rec.body.get("table") if rec else None) or {}
        if tb:
            own.add((tb.get("source", ""), tb.get("schema", ""), tb.get("table", "")))
    out = []
    for (src, schema, name), info in sorted(cat.tables.items()):
        if (src, schema, name) not in own and name.lower() not in said:
            continue
        cols = [c for c in info.columns.values() if not c.personal]
        out.append(
            {
                "source": src,
                "schema": schema,
                "table": name,
                "columns": [c.name for c in cols][:80],
                "dates": [c.name for c in cols if c.kind in ("date", "timestamp")][:20],
            }
        )
    return out[:MODEL_TABLES]


# --- the draft, checked -------------------------------------------------------------------------


def _numbers_ok(change: dict[str, Any], inputs: dict[str, Any], said: str) -> bool:
    """Every numeral in the change is in the input JSON, or is one of the owner's own numbers
    as written or scaled (65 → 6500000 for "₹65 L", 650000000 for "₹65 Cr")."""
    from othor.ai.guards import allowed_numerals, numbers_grounded, numerals

    text = json.dumps(change, ensure_ascii=False)
    if numbers_grounded(text, inputs):
        return True
    allowed = allowed_numerals(inputs)
    own: set[Decimal] = set()
    for n in numerals(said):
        try:
            d = Decimal(n)
        except InvalidOperation:
            continue
        own |= {d * k for k in SCALES}
    for n in numerals(text) - allowed:
        try:
            if Decimal(n) not in own:
                return False
        except InvalidOperation:
            return False
    return True


def check(
    s: Session,
    out: Any,
    inputs: dict[str, Any],
    said: str,
    today: dt.date,
    known: set[str] | frozenset[str] = frozenset(),
) -> Draft:
    """One drafted change, checked by code. `known`: the keys of new numbers drafted in the same
    message (2.4 L2), so a formula may use a number added beside it ("minus rebates")."""
    if not isinstance(out, dict):
        return Draft(None, NOT_AVAILABLE, "no draft")
    reply = str(out.get("reply") or "")[:300]
    from othor.ai.guards import numbers_grounded

    if reply and not numbers_grounded(reply, {**inputs, "said": said}):
        reply = "Here is the change and what it does today."
    ch = out.get("change")
    if not isinstance(ch, dict):
        return Draft(None, reply or "Say what changed, in one line.", "no change")
    op, type_, key = ch.get("op"), ch.get("type"), str(ch.get("key") or "")
    if op not in ("put", "retire") or type_ not in ALLOWED:
        return Draft(None, COULDNT, "not allowed")
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,80}", key):
        return Draft(None, COULDNT, "bad key")
    if type_ == "watch":
        return _watch(s, ch, key, op, inputs, said, reply)
    current = next((r for r in service.active(s, [type_]) if r.key == key), None)
    if op == "retire":
        if current is None or type_ == "metric":
            return Draft(None, COULDNT, "nothing to retire")
        return Draft({"op": "retire", "type": type_, "key": key, "body": None}, reply)
    from othor.home.answers import record_body

    body = record_body(ch)
    if "value_list" in body:
        body["values"] = [v.strip() for v in str(body.pop("value_list")).split(",") if v.strip()]
    said_body = dict(body)  # what the model wrote: its numbers must be the owner's
    if type_ == "metric":
        if current is None:  # 2.4 L2: a new number, a part or built from parts
            if set(body) - NEW_METRIC_FIELDS:
                return Draft(None, COULDNT, "metric fields")
            body = {"status": "active", **body}
        else:
            if set(body) - CHANGE_FIELDS:
                return Draft(None, COULDNT, "metric fields")
            if "formula" in body and not current.body.get("formula"):
                return Draft(None, COULDNT, "a formula on a part")
            body = {**current.body, **body}
        why = _metric_ok(s, body, known)
        if why:
            return Draft(None, COULDNT, why)
    if type_ == "rule":
        why = "rule kind" if body.get("kind") not in RULE_KINDS else _rule_ok(s, body, known)
        if why:
            return Draft(None, COULDNT, why)
    if type_ == "breakdown":
        col = str(body.get("column") or "")
        why = _columns_ok(s, None, [col]) if col else "breakdown fields"
        if why or body.get("expr") is not None or body.get("entity") is not None:
            return Draft(None, COULDNT, why or "breakdown fields")
    if type_ == "known_pattern":
        from othor.home.answers import _name_grounded

        if not _name_grounded(str(body.get("name", "")), said, dict(body.get("window") or {})):
            return Draft(None, COULDNT, "a number in the name")
    if (
        type_ == "goal"
        and isinstance(body.get("target"), int | float)
        and not isinstance(body.get("target"), bool)
    ):
        # the model may write the owner's target as a number (#66): kept as written, as text
        body["target"] = _plain(body["target"])
    if not _numbers_ok(said_body if type_ == "metric" else body, inputs, said):
        return Draft(None, COULDNT, "ungrounded number")
    try:
        body = validate_body(type_, body)
    except ValueError:
        return Draft(None, COULDNT, "invalid")
    if current is not None and current.body == body:
        return Draft(None, "That is already in your Business Model.", "no change")
    return Draft({"op": "put", "type": type_, "key": key, "body": body}, reply)


def _cols(node: Any) -> list[str]:
    """Every column an expression reads."""
    from othor.ontology.types import walk

    return [str(n["col"]) for n in walk(node) if "col" in n] if node else []


def _metric_refs(node: Any) -> list[str]:
    from othor.ontology.types import walk

    return [str(n["metric"]) for n in walk(node) if "metric" in n] if node else []


def _columns_ok(s: Session, table: dict[str, Any] | None, cols: list[str]) -> str:
    """ "" when every column is in the table (any scanned table when None) and none is personal;
    else why (rule 3: a personal column is never read, not even in a draft)."""
    from othor.ontology.compiler.catalog import load_catalog

    cat = load_catalog(s)
    if table is not None:
        info = cat.table(
            str(table.get("source", "")), str(table.get("schema", "")), str(table.get("table", ""))
        )
        if info is None:
            return "no such table"
        tables = [info]
    else:
        tables = list(cat.tables.values())
    for c in cols:
        if "." in c and table is not None:
            continue  # a joined table's column (invoices.invoice_date): the compiler checks it
        name = c.lower()
        hits = [t.columns[name] for t in tables if name in t.columns]
        if not hits:
            return "no such column"
        if any(h.personal for h in hits):
            return "a personal column"
    return ""


def _metric_ok(s: Session, body: dict[str, Any], known: set[str] | frozenset[str]) -> str:
    """A number's table, columns and parts exist (2.4 L2); "" when it holds."""
    live = {r.key for r in service.active(s, ["metric"])} | set(known)
    refs = _metric_refs(body.get("formula"))
    for f in (body.get("breakdown_formulas") or {}).values():
        refs += _metric_refs(f)
    if any(k not in live for k in refs):
        return "a part that isn't a number"
    if body.get("table"):
        cols = _cols(body.get("measure", {}).get("expr"))
        cols += [c for f in body.get("filters") or [] for c in _cols(f)]
        if body.get("date"):
            cols.append(str(body["date"]))
        return _columns_ok(s, body["table"], cols)
    return ""


def _rule_ok(s: Session, body: dict[str, Any], known: set[str] | frozenset[str]) -> str:
    """A pair or cross-source check over two numbers; a filter or unit rule on one table."""
    live = {r.key for r in service.active(s, ["metric"])} | set(known)
    if body.get("kind") in ("pair", "reconcile"):
        if body.get("table") or body.get("to") or len(body.get("over") or []) != 2:
            return "rule fields"
        return "" if all(k in live for k in body["over"]) else "a pair of unknown numbers"
    cols = _cols(body.get("expr")) + _cols(body.get("when"))
    if body.get("column"):
        cols.append(str(body["column"]))
    return _columns_ok(s, body.get("table") or {}, cols)


def _watch(
    s: Session,
    ch: dict[str, Any],
    key: str,
    op: Any,
    inputs: dict[str, Any],
    said: str,
    reply: str,
) -> Draft:
    """A watch (2.4 L2): a number, a limit in the owner's own words, above or below. Started on
    Apply (othor.engine.watches.create), not a Business Model version."""
    from othor.home.answers import record_body

    body = record_body(ch)
    metric = str(body.get("metric") or "")
    rec = next((r for r in service.active(s, ["metric"]) if r.key == metric), None)
    try:
        threshold = Decimal(str(body.get("threshold")))
    except (InvalidOperation, ValueError):
        return Draft(None, COULDNT, "watch threshold")
    if op != "put" or rec is None or body.get("direction") not in WATCH_DIRECTIONS:
        return Draft(None, COULDNT, "watch fields")
    clean = {
        "metric": metric,
        "threshold": format(threshold.normalize(), "f"),
        "direction": str(body["direction"]),
        "label": str(body.get("label") or rec.body.get("name") or metric)[:120],
        "owner": str(body.get("owner") or "")[:120],
    }
    if not _numbers_ok({"threshold": clean["threshold"]}, inputs, said):
        return Draft(None, COULDNT, "ungrounded number")
    return Draft({"op": "put", "type": "watch", "key": key, "body": clean}, reply)


def _plain(x: int | float) -> str:
    """200 → "200", 2.5 → "2.5": a number as the owner wrote it, never "200.0" or "2e2"."""
    from decimal import Decimal

    d = Decimal(repr(x))
    return str(d.quantize(Decimal(1))) if d == d.to_integral_value() else format(d.normalize(), "f")


def watch_line(s: Session, body: dict[str, Any]) -> str:
    """A drafted watch in a line: the owner's own limit, shown in the number's unit."""
    from othor.briefs import values

    rec = next((r for r in service.active(s, ["metric"]) if r.key == body.get("metric")), None)
    unit = (rec.body.get("unit") if rec else None) or "inr"
    name = str((rec.body.get("name") if rec else None) or body.get("metric"))
    limit = values.text(unit, Decimal(str(body["threshold"])))
    verb = "falls below" if body.get("direction") == "below" else "goes above"
    return f"{body.get('label') or name}: tell me when {name.lower()} {verb} {limit}"


def card_line(s: Session, change: dict[str, Any]) -> tuple[str, str, str]:
    """(section label, the line as it reads now, the line after) for the draft card."""
    from types import SimpleNamespace

    from othor.web.ontology_view import line

    if change["type"] == "watch":
        return SECTION["watch"], "", watch_line(s, change["body"])
    cur = next((r for r in service.active(s, [change["type"]]) if r.key == change["key"]), None)
    before = line(cur) if cur is not None else ""
    after = (
        line(SimpleNamespace(type=change["type"], key=change["key"], body=change["body"]))
        if change["body"] is not None
        else ""
    )
    if change["type"] == "metric" and cur is not None and change["body"] is not None:
        from othor.ontology import areas

        was, now = (cur.body or {}).get("area"), change["body"].get("area")
        if was != now:  # an area change (M16.1): the line says where the number moves
            before, after = f"{before} · {areas.label(was)}", f"{after} · {areas.label(now)}"
    return SECTION.get(change["type"], "NAMES AND WORDS"), before, after


# --- the effect (code only) ----------------------------------------------------------------------


def effect(
    s: Session,
    company: Any,
    change: dict[str, Any],
    *,
    preview: Any | None = None,
    today: dt.date | None = None,
    also: list[dict[str, Any]] | None = None,
) -> Effect:
    """What the change does, compiled in memory (nothing written). `also`: the changes of the
    same set it needs (2.4 L2: a formula with the new number drafted beside it)."""
    if change["type"] == "watch":  # not a record: nothing compiles, no number changes meaning
        return Effect()
    from othor.ontology.compiler.api import company_settings, compile_version
    from othor.ontology.compiler.build import compile_records
    from othor.ontology.compiler.catalog import load_catalog
    from othor.ontology.compiler.render import CompileError
    from othor.web.ontology_view import in_briefs

    live = {(r.type, r.key): r.body for r in service.active(s)}
    cand = dict(live)
    for c in [*(also or []), change]:
        if c["type"] == "watch":
            continue
        if c["body"] is None:
            cand.pop((c["type"], c["key"]), None)
        else:
            cand[(c["type"], c["key"])] = c["body"]
    cur = compile_version(s, company).compiled
    try:
        new = compile_records(cand, load_catalog(s), company_settings(company), 0)
    except CompileError as e:
        return Effect(error=f"That change can’t be computed: {e}.")
    names = {k: str(m.get("name") or k) for k, m in new.get("metrics", {}).items()}
    changed = sorted(
        k
        for k in set(cur.get("metrics", {})) | set(new.get("metrics", {}))
        if cur.get("metrics", {}).get(k, {}).get("definition_hash")
        != new.get("metrics", {}).get(k, {}).get("definition_hash")
    )
    used = in_briefs(s)
    briefs = sorted({b for k in changed for b in used.get(k, [])})
    from othor.engine.models import Watch

    watches = sorted(
        w.label
        for w in s.scalars(sa.select(Watch).where(Watch.stopped_at.is_(None)))
        if (w.indicator or {}).get("metric") in changed
    )
    before_p = {p["key"]: p.get("name", p["key"]) for p in cur.get("suppressions", [])}
    after_p = {p["key"]: p.get("name", p["key"]) for p in new.get("suppressions", [])}
    patterns = [f"+ {after_p[k]}" for k in sorted(set(after_p) - set(before_p))]
    patterns += [f"− {before_p[k]}" for k in sorted(set(before_p) - set(after_p))]
    eff = Effect(
        metrics=[names.get(k, k) for k in changed],
        briefs=briefs,
        watches=watches,
        patterns=patterns,
    )
    if preview is not None and today is not None and changed:
        try:
            eff.values = preview(cur, new, changed[:PREVIEW_METRICS], today)
        except Exception:  # a source that can't be reached: the structure only
            eff.values, eff.note = [], AFTER_TONIGHT
    return eff


def preview_runner(s: Session, key_: Any, company_id: Any, analytics: Any) -> Any:
    """Today's value before and after through the read-only role, 20 s per statement, logged
    in `run_query` as purpose "preview" (never part of a run, never stored as a value)."""
    from othor.briefs import values
    from othor.connectors.models import RunQuery, Source
    from othor.connectors.sql.query_log import QueryLog
    from othor.setup.compute import Runner
    from othor.setup.inputs import creds_loader, gather

    inp = gather(s)
    creds = creds_loader(s, key_, company_id)
    ids = {x.name: x.id for x in s.scalars(sa.select(Source))}

    def run(cur: dict[str, Any], new: dict[str, Any], keys: list[str], today: dt.date) -> list:
        out = []
        logs: list[tuple[QueryLog, Any]] = []
        for spec in (cur, new):
            log = QueryLog(personal=inp.personal)
            r = Runner(
                spec,
                inp.cat,
                creds.get,
                log,
                analytics if analytics.exists() else None,
                timeout_s=PREVIEW_TIMEOUT_S,
            )
            logs.append((log, r))
        try:
            for k in keys:
                m = new.get("metrics", {}).get(k) or cur.get("metrics", {}).get(k) or {}
                unit = m.get("unit")
                got = []
                for (_log, r), spec in zip(logs, (cur, new), strict=True):
                    if k not in spec.get("metrics", {}):
                        got.append("—")
                        continue
                    c = r.value(k, today)
                    got.append(values.text(unit, c.value) if c.value is not None else "—")
                out.append((str(m.get("name") or k), got[0], got[1]))
        finally:
            for log, r in logs:
                r.close()
                for source, i in r.ran:
                    q = log.entries[i]
                    s.add(
                        RunQuery(
                            purpose="preview",
                            source_id=ids.get(source),
                            sql=q.sql,
                            rows=q.rows,
                            duration_ms=q.duration_ms,
                            hash=q.hash,
                            params={
                                k: v.isoformat() if isinstance(v, dt.date) else v
                                for k, v in q.params.items()
                            }
                            or None,
                        )
                    )
        return out

    return run


def effect_lines(e: Effect) -> list[str]:
    """The card's effect, said from the computed effect."""
    out = []
    if e.metrics:
        n = len(e.metrics)
        out.append(
            f"{fmt.num(n)} number{'s' if n != 1 else ''} change meaning: {', '.join(e.metrics)}."
        )
    if e.briefs:
        out.append(f"Read by {', '.join(e.briefs)}.")
    if e.watches:
        out.append(f"Watched: {', '.join(e.watches)}.")
    for p in e.patterns:
        verb = "Sweep won't flag" if p.startswith("+") else "Sweep flags again"
        out.append(f"{verb}: {p[2:]}.")
    if not out:
        out.append("No number changes; the Business Model line is updated.")
    return out


def value_lines(e: Effect) -> list[str]:
    """Today's values before and after (the preview): shown with the draft, never stored."""
    return [f"{name} today: {before} → {after}" for name, before, after in e.values]
