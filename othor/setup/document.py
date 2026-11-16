# SPDX-License-Identifier: Apache-2.0
"""Redraft the metrics with a document the owner uploads on Metrics (M4b: 2ne, 2nf).

A data dictionary says how the team reads its data. Code reads it with a small set of sentence
patterns; with the model, `document_changes` proposes more. Every change cites the document's own
words: a change whose quote isn't in the document's text is dropped. A change is a typed patch to
a draft, or a typed record (a rule, a vocabulary entry); never SQL and never a number to show.

Results: conflict_resolved · mapped · rule_added · dimension_updated · filter_added · noted ·
no_change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from othor.ontology.compiler.catalog import Catalog, TableInfo
from othor.setup import drafts
from othor.setup.connect import _NOT_A_SUM
from othor.setup.models import SetupDraft
from othor.setup.patches import Patch, PatchRefused, apply_patch

RESULTS = {
    "conflict_resolved": "Conflict resolved",
    "mapped": "Mapped",
    "rule_added": "Rule added",
    "dimension_updated": "Dimension updated",
    "filter_added": "Filter added",
    "noted": "Noted",
    "no_change": "No change",
}
NOT_COVERED = "Not covered by the document"


@dataclass
class Change:
    subject: str  # what the owner sees in the Metric column
    result: str
    quote: str | None = None
    locator: str | None = None
    key: str | None = None  # the metric draft it changes
    body: dict[str, Any] | None = None  # the patched metric body
    records: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    choose: str | None = None  # settle the row's conflict with this choice
    note: str | None = None  # the row's "why" line on 2nf


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def quoted(quote: str, pages: list[tuple[str, str]]) -> str | None:
    """The locator of the page whose text holds the quote, or None (the quote check)."""
    q = _norm(quote)
    if len(q) < 8:
        return None
    for loc, text in pages:
        if q in _norm(text):
            return loc
    return None


def _tables(cat: Catalog, row: SetupDraft) -> TableInfo | None:
    t = row.body.get("table") or {}
    return cat.tables.get((t.get("source", ""), t.get("schema", ""), t.get("table", "")))


def _open(rows: list[SetupDraft]) -> list[SetupDraft]:
    return [r for r in rows if not drafts.locked(r) and r.state != "skipped"]


def rule_changes(
    pages: list[tuple[str, str]],
    rows: list[SetupDraft],
    cat: Catalog,
    records: dict[tuple[str, str], Any],
) -> list[Change]:
    out: list[Change] = []
    open_rows = _open(rows)
    for loc, text in pages:
        flat = re.sub(r"\s+", " ", text)

        # "net_value already excludes GST" → settle the GST conflict in favour of the source
        for m in re.finditer(r"\b([a-z_]+(?:\.[a-z_]+)?) already excludes GST\b", flat):
            for r in open_rows:
                if (r.value or {}).get("conflict") and r.confidence == "conflict":
                    out.append(
                        Change(
                            r.body.get("name", r.key),
                            "conflict_resolved",
                            m.group(0),
                            loc,
                            key=r.key,
                            choose="source",
                            note=f"Your data dictionary: {m.group(0)}.",
                        )
                    )

        # "status X means cancelled; cancelled invoices are left out" → a filter rule
        for m in re.finditer(
            r"\bstatus ([A-Za-z0-9]+) means cancelled(?:; cancelled (\w+) are left out)?", flat
        ):
            value, noun = m.group(1), (m.group(2) or "").lower()
            for t in sorted(cat.tables.values(), key=lambda x: (x.source_name, x.name)):
                st = t.column("status")
                if st is None or st.personal or (noun and not t.name.startswith(noun[:-1])):
                    continue
                if t.use != "metric_source":
                    continue
                key = f"{t.name}_status_{value}".lower()
                rule = {
                    "kind": "filter",
                    "text": f"{t.name.capitalize()} with status {value} are left out",
                    "table": {"source": t.source_name, "schema": t.schema, "table": t.name},
                    "expr": {"op": "<>", "args": [{"col": "status"}, {"lit": value}]},
                }
                users = [r for r in open_rows if _tables(cat, r) is t]
                subject = users[0].body.get("name", users[0].key) if users else t.name
                out.append(
                    Change(
                        subject,
                        "filter_added",
                        m.group(0),
                        loc,
                        key=users[0].key if users else None,
                        records=[("rule", key, rule)],
                        note=f"Excludes status {value} (cancelled).",
                    )
                )

        # "EXP in dim_customer.region means Export." → a vocabulary entry for the dimension
        for m in re.finditer(
            r"\b([A-Z0-9]{2,8}) in ([a-z_]+)\.([a-z_]+) means ([A-Z][\w ]{1,30}?)\.", flat
        ):
            term, table, column, means = m.groups()
            bd = next(
                (
                    b
                    for (kind, _k), b in records.items()
                    if kind == "breakdown" and b.get("column") == column
                ),
                None,
            )
            subject = bd.get("name", column) if bd else column.replace("_", " ").capitalize()
            out.append(
                Change(
                    subject,
                    "dimension_updated",
                    m.group(0).rstrip("."),
                    loc,
                    records=[
                        (
                            "vocabulary",
                            f"{column}_{term}".lower(),
                            {
                                "term": term,
                                "text": f"{term} in {table}.{column} means {means}",
                                "kind": "definition",
                                "column": column,
                            },
                        )
                    ],
                )
            )

        # "Metres shipped are in sales_dw.fact_dispatch.dispatch_mtrs." → map a waiting metric
        for m in re.finditer(
            r"\b(?:[Mm]etres|[Mm]eters)[\w ]* (?:are )?in ([a-z_]+)\.([a-z_]+)\.([a-z_]+)", flat
        ):
            schema, table, column = m.groups()
            found = next(
                (x for x in cat.tables.values() if x.schema == schema and x.name == table), None
            )
            if found is None:
                continue
            for r in open_rows:
                name = str(r.body.get("name") or r.key)
                # only a metres quantity itself: never a formula, a rate or a "per metre" ratio
                if "metre" not in name.lower() or r.body.get("formula") or _NOT_A_SUM.search(name):
                    continue
                active = r.body.get("status") == "active" and r.body.get("table")
                if active and (r.body.get("measure") or {}).get("expr") == {"col": column}:
                    continue  # already reads that column
                if active and not (r.note and "metre" in r.note.lower()):
                    continue
                from othor.setup.chat import _first_date

                try:
                    got = apply_patch(
                        {**r.body, "kind": r.body.get("kind", "flow")},
                        Patch(
                            "use_column",
                            {
                                "table": {
                                    "source": found.source_name,
                                    "schema": found.schema,
                                    "table": found.name,
                                },
                                "column": column,
                                "date": _first_date(found),
                            },
                        ),
                        cat,
                        records,
                    )
                except PatchRefused:
                    continue
                out.append(
                    Change(
                        r.body.get("name", r.key),
                        "mapped",
                        m.group(0),
                        loc,
                        key=r.key,
                        body=got.body,
                        note="Your data dictionary named the column.",
                    )
                )

        # "records dispatch in yards (uom YD); 1 yard = 0.9144 metres" → a unit conversion
        for m in re.finditer(
            r"in yards \(uom ([A-Z]{1,4})\); 1 yard = (\d+(?:\.\d+)?) metres", flat
        ):
            code, factor = m.groups()
            for r in open_rows:
                info = _tables(cat, r)
                expr = (r.body.get("measure") or {}).get("expr") or {}
                if info is None or info.column("uom") is None or "col" not in expr:
                    continue
                try:
                    got = apply_patch(
                        r.body,
                        Patch(
                            "convert_units",
                            {
                                "column": expr["col"],
                                "when": {"column": "uom", "value": code},
                                "factor": factor,
                                "to_unit": "metres",
                            },
                        ),
                        cat,
                        records,
                    )
                except PatchRefused:
                    continue
                out.append(
                    Change(
                        r.body.get("name", r.key),
                        "rule_added",
                        m.group(0),
                        loc,
                        key=r.key,
                        records=got.records,
                        note=f"Yards (uom {code}) converted × {factor}.",
                    )
                )

        # "Rebates are booked in … account 4410 and reduce net revenue." → noted for the owner
        for m in re.finditer(r"[Rr]ebates are booked in [^.]*? account (\d+)[^.]*\.", flat):
            rev = next((x for x in rows if "revenue" in x.key), None)
            out.append(
                Change(
                    rev.body.get("name", rev.key) if rev else "Net revenue",
                    "noted",
                    m.group(0).rstrip("."),
                    loc,
                    key=rev.key if rev else None,
                    records=[
                        (
                            "business_fact",
                            f"rebates_account_{m.group(1)}",
                            {"kind": "other", "text": m.group(0).rstrip(".")},
                        )
                    ],
                )
            )

    # waiting metrics the document didn't cover (2ne lists them)
    touched = {c.key for c in out if c.key}
    for r in open_rows:
        if r.state == "needs_source" and r.key not in touched:
            out.append(Change(r.body.get("name", r.key), "no_change", None, None, key=r.key))
    return out


def model_changes(
    out: dict[str, Any],
    pages: list[tuple[str, str]],
    rows: list[SetupDraft],
    cat: Catalog,
    records: dict[tuple[str, str], Any],
    inputs: Any = None,
) -> list[Change]:
    """`document_changes` output → changes. Dropped: a quote not in the document, an unknown
    metric, a patch that doesn't apply (unknown or personal column, a bad operator)."""
    by_key = {r.key: r for r in _open(rows)}
    got: list[Change] = []
    for c in out.get("changes") or []:
        quote = str(c.get("quote") or "")
        loc = quoted(quote, pages)
        row = by_key.get(str(c.get("metric") or ""))
        p = c.get("patch") or {}
        if loc is None or row is None or not p.get("kind"):
            continue
        from othor.setup.chat import proposal_from_model

        patch = proposal_from_model({"patch": p}, inputs if inputs is not None else pages)
        if patch is None or not _fits(row, patch, quote):
            continue
        try:
            done = apply_patch(row.body, patch, cat, records)
        except PatchRefused:
            continue
        result = {
            "use_column": "mapped",
            "convert_units": "rule_added",
            "add_filter": "filter_added",
            "exclude": "filter_added",
        }.get(patch.kind, "mapped")
        got.append(
            Change(
                row.body.get("name", row.key),
                result,
                quote[:500],
                loc,
                key=row.key,
                body=done.body,
                records=done.records,
                note=f"Your document: {quote[:160]}",
            )
        )
    return got


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 3}


def _fits(row: SetupDraft, patch: Patch, quote: str) -> bool:
    """A model's change must be about this metric: a formula (a ratio, a margin) is never
    replaced by one column, and a column named in the quote maps only to a metric the quote is
    about (its name shares a word with the quote) or one still without a source."""
    if patch.kind == "use_column":
        if row.body.get("formula"):
            return False
        waiting = row.body.get("status") != "active"
        return waiting or bool(_stems(row.body.get("name") or row.key) & _stems(quote))
    return True


def pages_of(refs: list[Any]) -> list[tuple[str, str]]:
    """An upload's text refs as (locator, text), in reading order."""
    got = [r for r in refs if r.kind == "page" and r.text]
    got.sort(key=lambda r: int((r.data or {}).get("page") or 0))
    return [(r.locator, r.text) for r in got]


def apply(
    s: Any, changes: list[Change], *, upload: Any, draft_round: int, actor: str
) -> dict[str, int]:
    """Write the changes to the drafts (not live until Confirm) and keep each as a setup_change
    row for 2ne's table and 2nf's citations. One event for the redraft."""
    import sqlalchemy as sa

    from othor.core.events import record_event
    from othor.ontology.types import validate_body
    from othor.setup import confirm
    from othor.setup.models import SetupChange

    rows = {r.key: r for r in drafts.stored(s)}
    counts: dict[str, int] = {}
    for c in changes:
        row = rows.get(c.key) if c.key else None
        applied = False
        try:
            if c.choose and row is not None and (row.value or {}).get("conflict"):
                confirm.choose(s, row, c.choose, actor=actor)
                applied = True
            if c.body is not None and row is not None:
                row.body = validate_body("metric", c.body)
                row.confidence, row.note = "high", None
                if row.state in ("needs_source", "excluded_table"):
                    row.state = "needs_confirmation"
                applied = True
            for type_, key, body in c.records:
                body_ = validate_body(type_, body)
                existing = s.scalars(
                    sa.select(SetupDraft).where(SetupDraft.kind == type_, SetupDraft.key == key)
                ).first()
                if existing is None:
                    s.add(
                        SetupDraft(
                            kind=type_,
                            key=key,
                            body=body_,
                            confidence="high",
                            state="needs_confirmation",
                            origin={"by": "document", "upload": str(upload.id)},
                            refs=[{"upload": upload.filename, "locator": c.locator or ""}],
                        )
                    )
                else:
                    existing.body = body_
                applied = True
        except ValueError:
            applied = False
        if applied and row is not None:
            ref = {"upload": upload.filename, "locator": c.locator or "", "text": c.quote or ""}
            row.refs = [ref, *[x for x in (row.refs or []) if x != ref]]
            prev = (row.origin or {}).get("document") or {}
            note = (prev.get("note") if prev.get("round") == draft_round else None) or c.note
            row.origin = {
                **(row.origin or {}),
                "document": {"upload": str(upload.id), "round": draft_round, "note": note},
            }
            if row.confidence != "conflict":
                row.confidence = "high"
            row.draft_no = draft_round
            row.value = None
        result = c.result if applied or c.result == "no_change" else "no_change"
        counts[result] = counts.get(result, 0) + 1
        s.add(
            SetupChange(
                upload_id=upload.id,
                draft_round=draft_round,
                subject=c.subject[:120],
                draft_key=c.key,
                quote=(c.quote or None) and c.quote[:500],
                locator=c.locator,
                result=result,
                applied=applied,
                created_by=actor,
            )
        )
    s.flush()
    record_event(
        s,
        actor=actor,
        action="setup.redrafted_with_document",
        object_type="upload",
        object_id=upload.id,
        after={"round": draft_round, **counts},
    )
    return counts
