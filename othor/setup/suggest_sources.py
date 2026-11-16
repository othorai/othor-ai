# SPDX-License-Identifier: Apache-2.0
"""Suggested sources (10a, 6c "Suggested", 6e): from where the owner said the data lives, their
business message and their uploads (SQL schema names, sheet and file names). Rules only."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from othor.setup.parse import Ref

KINDS = {"mysql": "MySQL", "postgres": "PostgreSQL", "gsheets": "Google Sheets"}


@dataclass
class Suggestion:
    term: str  # what the owner (or a file) called it
    kind: str  # mysql postgres gsheets
    name: str | None  # erp_db, MIS-Weekly
    files: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        k = KINDS[self.kind]
        return f"{k} · {self.name}" if self.name and self.kind != "postgres" else k

    @property
    def text(self) -> str:
        parts = []
        if self.tables:
            parts.append(", ".join(t.replace("_", " ") for t in self.tables[:5]).capitalize() + ".")
        if self.files:
            parts.append("Found in " + " and ".join(self.files[:2]) + ".")
        return (
            " ".join(parts)
            or "Not connected yet. Connect it and its tables are scanned before use."
        )

    @property
    def href(self) -> str:
        return f"/setup/sources/{self.kind}/new"


def _kind(text: str) -> str | None:
    t = text.lower()
    if re.search(r"postgres|warehouse|_dw\b|\bdw\b", t):
        return "postgres"
    if re.search(r"mysql|_db\b|\berp\b|accounts", t):
        return "mysql"
    if re.search(r"sheet|mis\b|tracker|excel", t):
        return "gsheets"
    return None


def suggest(
    data_lives: list[str], message: str, refs: list[tuple[str, Ref]], connected: set[str]
) -> list[Suggestion]:
    out: dict[str, Suggestion] = {}

    def add(
        term: str,
        kind: str | None,
        name: str | None,
        file: str | None = None,
        table: str | None = None,
    ) -> None:
        if kind is None:
            return
        key = (name or term).lower()
        if key in {c.lower() for c in connected}:
            return
        sug = out.setdefault(key, Suggestion(term, kind, name))
        if file and file not in sug.files:
            sug.files.append(file)
        if table and table not in sug.tables:
            sug.tables.append(table)

    for term in data_lives:
        t = term.strip()
        # the owner's own capitals stay ("MIS-Weekly"); only a lower-case first letter is raised
        name = term if re.fullmatch(r"[a-z0-9_]+", term) else t[:1].upper() + t[1:]
        add(term, _kind(term), name)
    for m in re.finditer(r"\b([a-z]+_(?:db|dw))\b", message):
        add(m.group(1), _kind(m.group(1)), m.group(1))
    if re.search(r"order tracker", message, re.I):
        add("order tracker", "gsheets", "Order tracker")
    for file, r in refs:
        if r.kind == "table" and r.data.get("schema"):
            schema = str(r.data["schema"])
            add(schema, _kind(schema), schema, file, str(r.data.get("table") or ""))
        for m in re.finditer(r"\b([a-z]+_(?:db|dw))\b", r.text):
            add(m.group(1), _kind(m.group(1)), m.group(1), file)
    return list(out.values())[:4]


# the words that only name a kind of source, not a particular one
_KIND_WORDS = {
    "postgres": {"postgresql", "postgres", "pg"},
    "mysql": {"mysql"},
    "gsheets": {"google", "sheets", "sheet", "gsheets"},
}


def without_connected_kinds(sugs: list[Suggestion], kinds: set[str]) -> list[Suggestion]:
    """Drop a suggestion that only names a kind ("PostgreSQL") once a source of that kind is
    connected (#19); a named one ("erp_db") stays until it is connected under that name."""

    def bare(x: Suggestion) -> bool:
        words = {w for w in re.split(r"[^a-z0-9]+", (x.name or x.term).lower()) if w}
        return bool(words) and words <= _KIND_WORDS.get(x.kind, set())

    return [x for x in sugs if not (x.kind in kinds and bare(x))]
