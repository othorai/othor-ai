# SPDX-License-Identifier: Apache-2.0
"""Metric drafts (docs/01 §4, board 2m/2n): by Claude when the model is available, by rules when
it isn't (rules_draft.py), and by the owner through the form. Stored as `setup_draft` rows; the
Ontology is untouched until Confirm.

What a model drafts is a *definition*: the table, column, aggregation and date it names are
checked against the scanned catalog, and the body is validated by the M3 types and compiled by the
M3 compiler before its value is computed by SQL. A number in its wording that isn't in its inputs
is replaced by a sentence built by code. A personal column is never used: the row becomes the
"excluded table" line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.ai.guards import numbers_grounded
from othor.core.events import record_event
from othor.ontology import areas
from othor.ontology.compiler.catalog import Catalog, TableInfo
from othor.ontology.types import validate_body
from othor.setup.models import SetupDraft
from othor.setup.parse import Ref
from othor.setup.rules_draft import MAX_DRAFTS, RuleDraft

PROMPT = ("draft_metrics", "v4")  # v3 (12 Nov 2026): time between two dates; older than N days
# v4 (M16.1): each metric's area, filled in quietly (the Metrics step doesn't show it)
NO_SOURCE = "No source has it · We'll ask about it on Home."
REDRAFT_CAP = 15  # drafted metrics kept by a model redraft, besides the owner's
CODE_RULE = "Drafted by Othor AI · checked against the scan"


@dataclass
class Draft:
    key: str
    body: dict[str, Any]
    origin: dict[str, Any]
    refs: list[dict[str, str]] = field(default_factory=list)
    confidence: str = "high"  # high check conflict low
    state: str = "needs_confirmation"  # needs_confirmation needs_source excluded_table
    note: str | None = None

    @classmethod
    def from_rule(cls, d: RuleDraft) -> Draft:
        return cls(d.key, d.body, d.origin, d.refs, d.confidence, d.state)


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (s or "metric")[:60]


# --- inputs (schema only: names and kinds, never rows or personal columns) --------------------


def catalog_input(cat: Catalog) -> list[dict[str, Any]]:
    out = []
    for (src, schema, name), t in sorted(cat.tables.items()):
        if t.use not in ("metric_source", "dimension", "reference"):
            continue
        out.append(
            {
                "source": src,
                "schema": schema,
                "table": name,
                "use": t.use,
                "row_count": t.row_count,
                "columns": [
                    {"column": c.name, "kind": c.kind} for c in t.columns.values() if not c.personal
                ],
                **({"excluded": "has personal columns; they are left out"} if t.personal else {}),
            }
        )
    return out


def refs_input(refs: list[tuple[str, Ref]]) -> list[dict[str, str]]:
    out = []
    for name, r in refs:
        text = r.text if r.kind != "page" else r.text[:1500]
        out.append({"file": name, "locator": r.locator, "kind": r.kind, "text": text[:1500]})
    return out[:400]


def model_inputs(
    cat: Catalog,
    refs: list[tuple[str, Ref]],
    message: str,
    *,
    keys: list[str],
    rules: list[str],
    confirmed: list[str],
    breakdowns: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "tables": catalog_input(cat),
        "breakdowns": [
            {"key": k, "name": b.get("name", k), "column": b.get("column", "")}
            for k, b in sorted((breakdowns or {}).items())
        ],
        "files": refs_input(refs),
        "business": message[:6000],
        "rules": rules,
        "existing_keys": keys,  # reuse a key for the same metric
        "confirmed": confirmed,  # the owner settled these: don't redraft them
        "max_metrics": MAX_DRAFTS,
    }


# --- model output → typed bodies ----------------------------------------------------------------


def _formula(m: dict[str, Any]) -> dict[str, Any] | None:
    op = {"divide": "/", "subtract": "-", "add": "+", "multiply": "*"}.get(
        m.get("formula_op") or ""
    )
    if not op or not m.get("formula_a") or not m.get("formula_b"):
        return None
    f: dict[str, Any] = {
        "op": op,
        "args": [{"metric": slug(m["formula_a"])}, {"metric": slug(m["formula_b"])}],
    }
    if m.get("times_days_in_period"):
        f = {"op": "*", "args": [f, {"fn": "days_in_period", "args": []}]}
    return f


def _code_description(m: dict[str, Any], t: TableInfo | None) -> str:
    if t is None:
        return "Derived from other metrics"
    agg = m.get("agg") or "sum"
    what = "count of rows" if agg == "count" else f"{agg.replace('_', ' ')} of {m.get('column')}"
    by = f" by {m['date_column']}" if m.get("date_column") else ""
    return f"{what.capitalize()} in {t.schema}.{t.name}{by}"


def _breakdowns(names: list[str], known: dict[str, str], t: TableInfo | None) -> list[str]:
    """Only breakdowns the Ontology has (by key, or by the column they read); others dropped."""
    out: list[str] = []
    by_col = {c.lower(): k for k, c in sorted(known.items())}
    for n in names:
        k = slug(n)
        if k not in known:
            k = by_col.get(n.lower(), "")
        if k and k in known and k not in out:
            out.append(k)
    return out[:4]


def _filter(m: dict[str, Any], t: TableInfo) -> dict[str, Any] | None:
    col, op = (m.get("filter_column") or "").strip(), m.get("filter_op") or ""
    c = t.column(col) if col else None
    if c is None or c.personal or op not in ("=", "<>", ">", "<"):
        return None
    return {"op": op, "args": [{"col": c.name}, {"lit": str(m.get("filter_value") or "")[:100]}]}


DATE_KINDS = ("date", "timestamp", "datetime")


def _date_col(t: TableInfo, name: Any) -> str | None:
    """A date column of the table, never a personal one; None otherwise."""
    c = t.column(str(name or "").strip()) if name else None
    if c is None or c.personal or c.kind not in DATE_KINDS:
        return None
    return c.name


def _days_between(m: dict[str, Any], t: TableInfo) -> dict[str, Any] | None:
    """v3: the time between two date columns of one row (dispatched_at − ready_at), in days."""
    a, b = _date_col(t, m.get("days_to")), _date_col(t, m.get("days_from"))
    if a is None or b is None or a == b:
        return None
    agg = m.get("agg") if m.get("agg") in ("avg", "min", "max") else "avg"
    return {"agg": agg, "expr": {"fn": "days_between", "args": [{"col": a}, {"col": b}]}}


def _age_filter(m: dict[str, Any], t: TableInfo) -> dict[str, Any] | None:
    """v3: only rows older than N days on the as-of date (due_date, 60), for a snapshot."""
    c = _date_col(t, m.get("age_column"))
    try:
        n = int(m.get("age_over_days") or 0)
    except (TypeError, ValueError):
        return None
    if c is None or not 0 < n <= 3650 or (m.get("kind") or "flow") != "snapshot":
        return None
    return {
        "op": ">",
        "args": [
            {"fn": "days_between", "args": [{"param": "as_of"}, {"col": c}]},
            {"lit": n},
        ],
    }


def from_model(
    out: dict[str, Any],
    cat: Catalog,
    inputs: dict[str, Any],
    *,
    breakdowns: dict[str, str] | None = None,
    rules: list[tuple[str, dict[str, Any]]] | None = None,
) -> list[Draft]:
    """breakdowns: Ontology breakdown key → column. rules: (key, rule body), applied as for
    rule drafts (table filters such as cancelled invoices)."""
    from othor.setup.rules_draft import RuleDraft, _apply_rules

    known = breakdowns or {}
    drafts: list[Draft] = []
    seen: set[str] = set()
    for m in out.get("metrics", []):
        if sum(1 for d in drafts if d.state == "needs_confirmation") >= MAX_DRAFTS:
            break
        key = slug(m.get("key") or m.get("name") or "")
        if key in seen:
            continue
        seen.add(key)
        origin = {"by": "model", "prompt": f"{PROMPT[0]}@{PROMPT[1]}", "rule": CODE_RULE}
        refs = [
            {"upload": r.get("file", ""), "locator": r.get("locator", ""), "text": ""}
            for r in m.get("refs", [])[:4]
        ]
        t = None
        if m.get("table"):
            t = cat.tables.get((m.get("source", ""), m.get("schema", ""), m["table"]))
            if t is None:
                drafts.append(
                    Draft(
                        key,
                        _needs(m, f"No table {m['table']} in your sources"),
                        origin,
                        refs,
                        "check",
                        "needs_source",
                        f"We could not find {m['table']} in your sources; is it in another source?",
                    )
                )
                continue
            used = {x.lower() for x in (m.get("column"), m.get("date_column")) if x}
            if used & t.personal or (t.personal and not t.without_personal and t.use == "excluded"):
                drafts.append(
                    Draft(
                        key,
                        _needs(m, "Personal columns"),
                        origin,
                        refs,
                        "check",
                        "excluded_table",
                        f"{t.schema}.{t.name} has personal data. It can be used "
                        "without personal columns.",
                    )
                )
                continue
        description = str(m.get("description") or "")
        if not description or not numbers_grounded(description, inputs):
            description = _code_description(m, t)
        body: dict[str, Any] = {
            "name": str(m.get("name") or key)[:120],
            "description": description[:400],
            "grain": m.get("grain") or "month",
            "unit": m.get("unit") or "inr",
            "kind": m.get("kind") or "flow",
            "breakdowns": _breakdowns(list(m.get("breakdowns", [])), known, t),
            "status": "active",
        }
        if m.get("area") in areas.MODEL_CHOICES and (a := areas.clean(m["area"])):
            body["area"] = a
        if t is not None:
            agg = m.get("agg") or "sum"
            body["table"] = {"source": t.source_name, "schema": t.schema, "table": t.name}
            between = _days_between(m, t)
            body["measure"] = (
                between
                if between is not None
                else {"agg": "count"}
                if agg == "count" and not m.get("column")
                else {"agg": agg, "expr": {"col": m.get("column") or ""}}
            )
            if between is not None:
                body["unit"] = "days"
            if m.get("date_column"):
                body["date"] = m["date_column"]
            filters = [f for f in (_filter(m, t), _age_filter(m, t)) if f is not None]
            if filters:
                body["filters"] = filters
            rd = RuleDraft(key, body, {"rule": CODE_RULE})
            _apply_rules(rd, t, rules or [])
            body, origin["rule"] = rd.body, rd.origin["rule"]
        else:
            f = _formula(m)
            if f is None:
                drafts.append(
                    Draft(
                        key,
                        _needs(m, m.get("question") or "No source"),
                        origin,
                        refs,
                        "check",
                        "needs_source",
                        NO_SOURCE,
                    )
                )
                continue
            body["formula"] = f
        confidence = "check" if m.get("question") or m.get("confidence") == "check" else "high"
        note = str(m["question"])[:300] if m.get("question") else None
        if note and not numbers_grounded(note, inputs):
            note = "Please check this definition."
        try:
            body = validate_body("metric", body)
        except ValueError as e:
            drafts.append(
                Draft(key, _needs(m, "Invalid"), origin, refs, "check", "needs_source", _reason(e))
            )
            continue
        drafts.append(Draft(key, body, origin, refs, confidence, "needs_confirmation", note))
    for x in out.get("missing", [])[:10]:
        key = slug(x.get("name", ""))
        if key in seen or not x.get("name"):
            continue
        seen.add(key)
        # the model's own reason ("fact_dispatch has ready_at and dispatched_at but no column
        # with the time between them") is kept when its numbers are in the inputs; it says more
        # than "No source has it", which wasn't true when the table was there (owner, 12 Nov)
        why = str(x.get("why") or "").strip()
        if not why or not numbers_grounded(why, inputs):
            why = ""
        drafts.append(
            Draft(
                key,
                _needs(x, why or "No source has it"),
                {"by": "model", "prompt": f"{PROMPT[0]}@{PROMPT[1]}", "reason": bool(why)},
                [],
                "check",
                "needs_source",
                why[:300] or NO_SOURCE,
            )
        )
    return drafts


def _needs(m: dict[str, Any], why: str) -> dict[str, Any]:
    return validate_body(
        "metric",
        {
            "name": str(m.get("name") or "Metric")[:120],
            "status": "needs_source",
            "needs": str(why)[:200],
        },
    )


def _reason(e: Exception) -> str:
    msg = str(e).splitlines()
    return ("Couldn't use this definition: " + (msg[1] if len(msg) > 1 else msg[0]).strip())[:300]


# --- storage ------------------------------------------------------------------------------------


def stored(s: Session, kind: str = "metric") -> list[SetupDraft]:
    return list(
        s.scalars(
            sa.select(SetupDraft)
            .where(SetupDraft.kind == kind)
            .order_by(SetupDraft.position, SetupDraft.key)
        )
    )


def locked(row: SetupDraft) -> bool:
    """Confirmed, or written by the owner: a redraft never touches it."""
    return row.frozen or row.state == "confirmed" or (row.origin or {}).get("by") == "owner"


def store(s: Session, drafts: list[Draft], *, actor: str, redraft: bool = False) -> dict[str, int]:
    """Write drafts. A first draft replaces every unlocked row. A redraft keeps the old body as
    `prev_body` so the owner accepts or rejects each change (2nf)."""
    rows = {r.key: r for r in stored(s)}
    counts = {"added": 0, "changed": 0, "kept": 0}
    keep = {d.key for d in drafts}
    set_aside: list[str] = []
    if not redraft:
        for key, old in rows.items():
            if key not in keep and not locked(old):
                s.delete(old)
    else:
        # a model redraft replaces the rule-made rows it doesn't return: set aside (Skipped),
        # never deleted, so the owner can bring them back
        for key, old in rows.items():
            if (
                key not in keep
                and not locked(old)
                and old.state != "skipped"
                and (old.origin or {}).get("by") == "rules"
            ):
                old.state = "skipped"
                old.origin = {**(old.origin or {}), "set_aside": "redraft"}
                set_aside.append(key)
    pos = max([r.position for r in rows.values()] + [0]) + 1
    for i, d in enumerate(drafts):
        row: SetupDraft | None = rows.get(d.key)
        if row is not None and locked(row):
            counts["kept"] += 1
            continue
        if row is None:
            s.add(
                SetupDraft(
                    kind="metric",
                    key=d.key,
                    body=d.body,
                    origin=d.origin,
                    refs=d.refs,
                    confidence=d.confidence,
                    state=d.state,
                    note=d.note,
                    position=pos + i,
                )
            )
            counts["added"] += 1
            continue
        if redraft and row.body != d.body:
            row.prev_body = row.prev_body or row.body
            row.changed = True
            row.draft_no += 1
            counts["changed"] += 1
        elif redraft:
            counts["kept"] += 1
        row.body, row.origin, row.refs = d.body, d.origin, d.refs
        row.confidence, row.state, row.note = d.confidence, d.state, d.note
        row.value = None
    s.flush()
    if redraft:  # at most REDRAFT_CAP drafted rows, besides the owner's and confirmed ones
        open_rows = [
            r
            for r in stored(s)
            if r.state != "skipped" and not locked(r) and r.key not in set_aside
        ]
        order = {d.key: i for i, d in enumerate(drafts)}
        open_rows.sort(key=lambda r: (r.key not in order, order.get(r.key, 0), r.position))
        for r in open_rows[REDRAFT_CAP:]:
            r.state = "skipped"
            r.origin = {**(r.origin or {}), "set_aside": "redraft"}
            set_aside.append(r.key)
        counts["set_aside"] = len(set_aside)
    record_event(
        s,
        actor=actor,
        action="setup.redrafted" if redraft else "setup.drafted",
        object_type="setup_draft",
        object_id="metrics",
        after=counts,
    )
    return counts


def accept_change(s: Session, row: SetupDraft, *, actor: str, accept: bool) -> None:
    before = {"body": row.body}
    # the answer stays with the row (the Decisions panel lists it as decided; Change reopens it)
    settled = {
        "choice": "accept" if accept else "reject",
        "text": "Using the redrafted definition." if accept else "Kept your definition.",
        "prev": row.prev_body,
        "new": row.body,
    }
    if not accept and row.prev_body is not None:
        row.body = row.prev_body
        row.value = None
    row.prev_body, row.changed = None, False
    row.value = {**(row.value or {}), "settled": settled}
    record_event(
        s,
        actor=actor,
        action="setup.draft_accepted" if accept else "setup.draft_rejected",
        object_type="setup_draft",
        object_id=row.id,
        before=before,
        after={"body": row.body},
    )


def confirm_row(s: Session, row: SetupDraft, *, actor: str) -> None:
    if row.confidence == "conflict":
        raise ValueError("Settle the conflict first.")
    before = {"state": row.state, "confidence": row.confidence}
    row.state, row.frozen = "confirmed", True
    record_event(
        s,
        actor=actor,
        action="setup.draft_confirmed",
        object_type="setup_draft",
        object_id=row.id,
        before=before,
        after={"state": row.state},
    )


def unconfirm_row(s: Session, row: SetupDraft, *, actor: str) -> None:
    """Undo Confirm on one metric (M11c): back to needs confirmation, unfrozen."""
    if row.state != "confirmed":
        return
    row.state, row.frozen = "needs_confirmation", False
    record_event(
        s,
        actor=actor,
        action="setup.draft_unconfirmed",
        object_type="setup_draft",
        object_id=row.id,
        before={"state": "confirmed"},
        after={"state": row.state},
    )


def confirmable(row: SetupDraft) -> bool:
    """A metric the Metrics-to-confirm panel can confirm now: not a decision (an open conflict,
    a redrafted definition, a missing source, an excluded table) and not confirmed yet."""
    return (
        row.state == "needs_confirmation"
        and row.confidence != "conflict"
        and not row.changed
        and row.body.get("status", "active") == "active"
    )


def confirm_rows(s: Session, rows: list[SetupDraft], *, actor: str) -> list[str]:
    """ "Confirm the N remaining" (M11c): one owner action, one event per metric. Returns the
    keys confirmed (for Undo)."""
    done = []
    for r in rows:
        if confirmable(r):
            confirm_row(s, r, actor=actor)
            done.append(r.key)
    return done


def owner_metric(form: dict[str, Any], cat: Catalog) -> dict[str, Any]:
    """The "Add a metric" form (with or without the model) → a typed body. Raises ValueError."""
    key = (form.get("source", ""), form.get("schema", ""), form.get("table", ""))
    t = cat.tables.get(key)
    if t is None:
        raise ValueError("Pick a table.")
    agg = form.get("agg") or "sum"
    col = (form.get("column") or "").strip()
    date = (form.get("date") or "").strip()
    for c in (col, date, *form.get("breakdowns", [])):
        if c and c.lower() in t.personal:
            raise ValueError(f"{c} is a personal column. Othor AI never reads it.")
        if c and t.column(c) is None:
            raise ValueError(f"{t.name} has no column {c}.")
    if agg != "count" and not col:
        raise ValueError("Pick the column to add up.")
    body: dict[str, Any] = {
        "name": (form.get("name") or "").strip() or f"{agg} of {col or 'rows'}",
        "description": _code_description({"agg": agg, "column": col, "date_column": date}, t),
        "table": {"source": t.source_name, "schema": t.schema, "table": t.name},
        "measure": {"agg": agg}
        if agg == "count" and not col
        else {"agg": agg, "expr": {"col": col}},
        "grain": form.get("grain") or "month",
        "unit": form.get("unit") or ("count" if agg in ("count", "count_distinct") else "inr"),
        "kind": "flow",
        "breakdowns": list(form.get("breakdowns", [])),
        "status": "active",
    }
    if date:
        body["date"] = date
    f = form.get("filter_column")
    if f:
        if f.lower() in t.personal or t.column(f) is None:
            raise ValueError(f"{t.name} has no column {f} Othor AI can read.")
        op = {"=": "=", "≠": "<>", "<>": "<>", ">": ">", "<": "<"}.get(
            form.get("filter_op", "="), "="
        )
        body["filters"] = [
            {"op": op, "args": [{"col": f}, {"lit": str(form.get("filter_value", ""))}]}
        ]
    return validate_body("metric", body)


def bring_back(s: Session, *, actor: str) -> int:
    """Undo a redraft's set-aside: the rows it skipped return as drafts to check."""
    back = [
        r
        for r in stored(s)
        if r.state == "skipped" and (r.origin or {}).get("set_aside") == "redraft"
    ]
    for r in back:
        r.state = "needs_confirmation"
        r.origin = {k: v for k, v in (r.origin or {}).items() if k != "set_aside"}
    if back:
        record_event(
            s,
            actor=actor,
            action="setup.drafts_brought_back",
            object_type="setup_draft",
            object_id="metrics",
            after={"keys": [r.key for r in back]},
        )
    return len(back)
