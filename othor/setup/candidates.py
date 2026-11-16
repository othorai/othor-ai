# SPDX-License-Identifier: Apache-2.0
"""Where is it? (2nb) and which table? (2nc): candidates found by code from the scan.

Words from the metric's name, description or question are matched against column and table
names (with the concept synonyms the rule drafts use). Every candidate carries what the owner
needs to choose: the unit, the rows, and the key it shares with the metric's own table. Personal
columns are never candidates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from othor.connectors.classify import KEYISH
from othor.ontology.compiler.catalog import Catalog, TableInfo
from othor.setup.rules_draft import _unit_of, tokens

SYNONYMS = {
    "metres": {"mtrs", "mtr", "metres", "meters", "metre", "meter"},
    "shipped": {"dispatch", "dispatched", "shipped", "despatch"},
    "orders": {"order", "orders"},
    "deals": {"deal", "deals", "opportunity", "opportunities"},
    "revenue": {"revenue", "sales", "net", "invoice", "value"},
    "receivables": {"receivable", "receivables", "ar"},
    "customers": {"customer", "customers", "client", "clients"},
}
STOP = {"the", "and", "not", "yet", "per", "from", "with", "find", "could", "this", "that", "into"}


def _words(text: str) -> set[str]:
    base = {w for w in tokens(text) if w not in STOP}
    out = set(base)
    for group in SYNONYMS.values():
        if base & group:
            out |= group
    return out


CONCEPT_WORDS = set().union(*SYNONYMS.values())


def _weight(matched: set[str]) -> int:
    """A concept word (orders, metres, deals) counts more than an ordinary one (open, net)."""
    return sum(5 if w in CONCEPT_WORDS else 2 for w in matched)


def _name_words(name: str) -> set[str]:
    return set(re.split(r"[^a-z0-9]+", name.lower())) - {""}


@dataclass
class ColumnCandidate:
    table: TableInfo
    column: str
    unit: str
    rows: int
    joins: list[str]
    score: int
    personal: bool = False

    @property
    def label(self) -> str:
        return f"{self.table.source_name} · {self.table.schema}.{self.table.name}.{self.column}"


@dataclass
class TableCandidate:
    table: TableInfo
    rows: int
    useful: list[str] = field(default_factory=list)
    score: int = 0


def _shared_keys(a: TableInfo, b: TableInfo | None) -> list[str]:
    if b is None:
        return []
    keys = {
        c.name.lower()
        for c in a.columns.values()
        if KEYISH.search(c.name.lower()) and not c.personal
    }
    return sorted(k for k in keys if b.column(k) is not None)


def column_candidates(
    cat: Catalog, text: str, *, near: tuple[str, str, str] | None = None, limit: int = 4
) -> list[ColumnCandidate]:
    words = _words(text)
    near_t = cat.tables.get(near) if near else None
    out: list[ColumnCandidate] = []
    for t in cat.tables.values():
        if t.use not in ("metric_source", "dimension") or (
            t.source_kind == "gsheets" and not t.duck_table
        ):
            continue
        tw = _name_words(t.name)
        for c in t.columns.values():
            if c.personal or c.kind not in ("decimal", "float", "int"):
                continue
            if KEYISH.search(c.name.lower()):
                continue  # an id is never a measure
            cw = _name_words(c.name)
            hit = _weight(cw & words) + _weight(tw & words) // 2
            if not cw & words:
                continue
            out.append(
                ColumnCandidate(
                    t,
                    c.name,
                    _unit_of(c.name),
                    t.row_count,
                    _shared_keys(t, near_t),
                    hit + (2 if _shared_keys(t, near_t) else 0),
                )
            )
    out.sort(key=lambda x: (-x.score, -x.rows, x.label))
    return out[:limit]


def table_candidates(cat: Catalog, text: str, *, limit: int = 3) -> list[TableCandidate]:
    words = _words(text)
    out: list[TableCandidate] = []
    for t in cat.tables.values():
        if t.use != "metric_source":
            continue
        tw = _name_words(t.name)
        if not tw & words:
            continue
        useful = [
            c.name
            for c in t.columns.values()
            if not c.personal
            and re.search(r"status|stage|amount|value|date|promised|closed|created", c.name.lower())
        ]
        out.append(TableCandidate(t, t.row_count, useful, _weight(tw & words) * 2 + len(useful)))
    out.sort(key=lambda x: (-x.score, -x.rows, x.table.name))
    return out[:limit]


DURATION = re.compile(r"\b(time|days|cycle|duration|lead|turnaround|age)\b", re.I)
_START = re.compile(r"ready|created|start|order|opened|requested|invoice_date|placed")
_END = re.compile(r"dispatched|closed|end|paid|done|shipped|delivered|decided|completed")


@dataclass(frozen=True)
class DurationCandidate:
    table: TableInfo
    start: str
    end: str
    rows: int
    score: int


def duration_candidates(cat: Catalog, text: str, *, limit: int = 3) -> list[DurationCandidate]:
    """For a duration ("Dispatch time"): a start and an end date on one table, by name."""
    words = _words(text)
    out: list[DurationCandidate] = []
    for t in cat.tables.values():
        if t.use != "metric_source":
            continue
        dates = [
            c.name for c in t.columns.values() if c.kind in ("date", "timestamp") and not c.personal
        ]
        starts = [d for d in dates if _START.search(d.lower())]
        ends = [d for d in dates if _END.search(d.lower())]
        if not starts or not ends:
            continue
        hit = _weight((_name_words(t.name) | {w for d in dates for w in _name_words(d)}) & words)
        if not hit:
            continue
        start = sorted(starts, key=lambda d: (not d.lower().endswith("_at"), d))[0]
        end = sorted(ends, key=lambda d: (not d.lower().endswith("_at"), d))[0]
        if start != end:
            out.append(DurationCandidate(t, start, end, t.row_count, hit))
    out.sort(key=lambda x: (-x.score, -x.rows, x.table.name))
    return out[:limit]
