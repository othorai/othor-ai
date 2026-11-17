# SPDX-License-Identifier: Apache-2.0
"""Read an upload by code where code can (docs/01 §3 Upload, decisions 4 Nov 2026):

- XLSX and CSV: each tab through the Sheets tab detector (header row, units, dates, totals)
- SQL: tables, filters (one per line) and named definitions, with line numbers
- PDF with a text layer: read as text, one ref per page ("Read as text · drafted by rules")
- PDF without text, images: need the model ("Can't be read without the model")

Refs are what the Ontology cites: "tab Sales!D1", "line 12", "p. 7". A number a model reads is
never used as a value: for a PDF it must appear in the PDF's own text, for an image it is cut.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any

from othor.ai.guards import numerals
from othor.connectors.sheets.detect import detect

MIN_TEXT_CHARS = 200
KINDS = {
    "xlsx": "xlsx",
    "xlsm": "xlsx",
    "csv": "csv",
    "sql": "sql",
    "pdf": "pdf",
    "docx": "docx",
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "webp": "image",
    "gif": "image",
    "txt": "text",
    "md": "text",
}
MEDIA = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}


def kind_of(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return KINDS.get(ext, "other")


def media_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return MEDIA.get(ext, "application/octet-stream")


@dataclass(frozen=True)
class Ref:
    kind: str  # column table filter definition page target metric dimension term name
    locator: str
    text: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Parsed:
    status: str  # read · read_as_text · needs_model · unreadable
    read_by: str | None
    summary: dict[str, Any]
    refs: list[Ref]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# --- spreadsheets ---------------------------------------------------------------------------


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if hasattr(v, "strftime"):
        return str(v.strftime("%Y-%m-%d"))
    return str(v)


def _grid_refs(grid: list[list[str]], tab: str) -> tuple[list[Ref], int, str]:
    from othor.connectors.classify import blank_personal

    # personal columns' values are blanked before detect reads the grid (non-negotiable 3);
    # their headers are left out of the refs too
    grid, personal = blank_personal(grid)
    shape = detect({"properties": {"title": tab}, "values": grid}, today=_today())
    if shape.kind not in ("table", "stale"):
        return [], 0, shape.kind
    refs = []
    numbers = 0
    for c in shape.data_columns:
        if not c.header or c.header in personal:
            continue
        is_num = c.kind == "number"
        numbers += is_num
        refs.append(
            Ref(
                "column",
                f"tab {tab}!{c.letter}{shape.header_row}",
                c.header,
                {
                    "tab": tab,
                    "unit": c.unit,
                    "kind": c.kind,
                    "number": is_num,
                    "rows": shape.row_count,
                    "stale": shape.kind == "stale",
                },
            )
        )
    return refs, numbers, shape.kind


def _today() -> Any:
    import datetime as dt

    return dt.date.today()


def parse_xlsx(raw: bytes) -> Parsed:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    refs: list[Ref] = []
    metrics = 0
    tabs = 0
    for ws in wb.worksheets:
        tabs += 1
        grid = [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)]
        r, n, _kind = _grid_refs(grid, ws.title)
        refs += r
        metrics += n
    wb.close()
    return Parsed(
        "read",
        "code",
        {
            "tabs": tabs,
            "metrics": metrics,
            "text": f"{_plural(metrics, 'metric')}, {_plural(tabs, 'tab')}",
        },
        refs,
    )


def parse_csv(raw: bytes, name: str) -> Parsed:
    text = raw.decode("utf-8-sig", errors="replace")
    grid = [row for row in csv.reader(io.StringIO(text))]
    refs, n, _ = _grid_refs(grid, name)
    return Parsed(
        "read", "code", {"tabs": 1, "metrics": n, "text": f"{_plural(n, 'metric')}, 1 table"}, refs
    )


# --- SQL -----------------------------------------------------------------------------------


_FILTER_LINE = re.compile(r"^\s*(WHERE|AND|OR|HAVING)\b", re.I)


def parse_sql(raw: bytes) -> Parsed:
    import sqlglot
    from sqlglot import exp

    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    refs: list[Ref] = []
    tree = None
    for dialect in ("mysql", "postgres", None):
        try:
            tree = sqlglot.parse_one(text, read=dialect)
            break
        except Exception:  # noqa: S112 - try the next dialect
            continue
    if tree is None:
        return Parsed("unreadable", None, {"text": "Couldn't read this SQL"}, [])

    def line_of(needle: str) -> int:
        for i, line in enumerate(lines, 1):
            if needle.lower() in line.lower():
                return i
        return 1

    seen: set[str] = set()
    for t in tree.find_all(exp.Table):
        name = f"{t.db}.{t.name}" if t.db else t.name
        if name not in seen:
            seen.add(name)
            refs.append(
                Ref("table", f"line {line_of(t.name)}", name, {"schema": t.db, "table": t.name})
            )
    defs = 0
    for a in tree.find_all(exp.Alias):
        if isinstance(a.parent, exp.Select) and not isinstance(a.this, exp.Column):
            defs += 1
            refs.append(
                Ref(
                    "definition",
                    f"line {line_of(a.alias)}",
                    a.alias,
                    {"sql": a.this.sql(dialect="mysql")},
                )
            )
    for i, line in enumerate(lines, 1):
        if _FILTER_LINE.match(line):
            refs.append(Ref("filter", f"line {i}", line.strip()))
    for i, line in enumerate(lines, 1):
        if line.strip().startswith("--"):
            refs.append(Ref("term", f"line {i}", line.strip().lstrip("- ").strip()))
    return Parsed(
        "read",
        "code",
        {"definitions": defs, "tables": len(seen), "text": _plural(defs, "definition")},
        refs,
    )


# --- PDF and text ---------------------------------------------------------------------------


def pdf_pages(raw: bytes) -> list[str]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(raw))
        return [(p.extract_text() or "") for p in reader.pages]
    except Exception:
        return []


def pdf_text(raw: bytes) -> str:
    return "\n".join(pdf_pages(raw))


def parse_pdf_as_text(raw: bytes) -> Parsed:
    pages = pdf_pages(raw)
    if sum(len(p.strip()) for p in pages) < MIN_TEXT_CHARS:
        return Parsed("needs_model", None, {"text": "Can't be read without the model"}, [])
    refs = [
        Ref("page", f"p. {i}", p.strip(), {"page": i}) for i, p in enumerate(pages, 1) if p.strip()
    ]
    return Parsed(
        "read_as_text",
        "text",
        {"pages": len(pages), "text": "Read as text · drafted by rules"},
        refs,
    )


def parse_docx(raw: bytes) -> Parsed:
    """A Word document read by code (python-docx): text under each heading, cited by heading;
    paragraphs before any heading by number."""
    from docx import Document

    doc = Document(io.BytesIO(raw))
    sections: list[tuple[str, list[str]]] = []
    for i, p in enumerate(doc.paragraphs, 1):
        text = p.text.strip()
        if not text:
            continue
        if ((p.style.name if p.style else "") or "").lower().startswith(("heading", "title")):
            sections.append((text[:80], []))
        elif sections:
            sections[-1][1].append(text)
        else:
            sections.append((f"para {i}", [text]))
    for t in doc.tables:
        rows = [" · ".join(c.text.strip() for c in r.cells) for r in t.rows]
        sections.append((f"table {len(sections) + 1}", [x for x in rows if x.strip(" ·")]))
    refs = [Ref("page", loc, "\n".join(body), {}) for loc, body in sections if body]
    if sum(len(r.text) for r in refs) < MIN_TEXT_CHARS // 4:
        return Parsed("unreadable", None, {"text": "No text found in this document"}, [])
    return Parsed(
        "read_as_text",
        "text",
        {"sections": len(refs), "text": "Read as text · drafted by rules"},
        refs,
    )


def parse_text(raw: bytes) -> Parsed:
    text = raw.decode("utf-8", errors="replace")
    refs = [
        Ref("page", f"line {i}", line.strip())
        for i, line in enumerate(text.splitlines(), 1)
        if line.strip()
    ]
    return Parsed(
        "read_as_text",
        "text",
        {"lines": len(refs), "text": "Read as text · drafted by rules"},
        refs,
    )


def parse_file(raw: bytes, kind: str, name: str) -> Parsed:
    """Code only: what can be read without a model."""
    try:
        if kind == "xlsx":
            return parse_xlsx(raw)
        if kind == "csv":
            return parse_csv(raw, name)
        if kind == "sql":
            return parse_sql(raw)
        if kind == "pdf":
            return parse_pdf_as_text(raw)
        if kind == "docx":
            return parse_docx(raw)
        if kind == "text":
            return parse_text(raw)
    except Exception:
        return Parsed("unreadable", None, {"text": "Couldn't read this file"}, [])
    if kind == "image":
        return Parsed("needs_model", None, {"text": "Can't be read without the model"}, [])
    return Parsed("unreadable", None, {"text": "Not a file Othor AI reads"}, [])


# --- what a model read ----------------------------------------------------------------------

_MONEY = re.compile(
    r"\s*(₹|Rs\.?|INR|€|\$)?\s*[−-]?\d[\d,]*(\.\d+)?\s*(Cr|crore|L|lakh|k|M|%|days?|d)?\b", re.I
)


def refs_from_model(out: dict[str, Any], *, kind: str, source_text: str | None) -> list[Ref]:
    """Model-read items as refs. PDF: an item whose numbers aren't in the PDF text is dropped.
    Image: numbers are cut from every item (names and hints only)."""
    allowed = numerals(source_text or "")
    refs = []
    for item in out.get("items", []):
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        if kind == "image":
            text = re.sub(r"\s{2,}", " ", _MONEY.sub(" ", text)).strip(" ·,-")
            if not text:
                continue
        elif not numerals(text) <= allowed:
            continue
        refs.append(
            Ref(
                str(item.get("kind", "term"))[:16],
                str(item.get("locator", ""))[:100],
                text,
                {"by": "model"},
            )
        )
    return refs
