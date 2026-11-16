# SPDX-License-Identifier: Apache-2.0
"""Find a Google Sheets tab's shape: where the table is and how to read it (docs/05, board 10c).

Input is one sheet as the Sheets API returns it from `spreadsheets.get` with grid data reduced to
formatted values: `{"properties": {...}, "values": [[str, ...], ...], "merges": [GridRange]}`.
Rows and columns in the result are 1-based like the sheet; column letters as on screen.

Detection uses the cells only to find structure (header, totals, merges, types, units). It never
sends cells anywhere; the scan stores the shape and statistics, not the rows.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from othor.connectors.classify import Col, Sorted, TableFacts, classify

TabKind = Literal["table", "free_text", "pivot", "stale", "empty"]
STALE_AFTER_DAYS = 365
HEADER_SCAN_ROWS = 12  # look for the header in the first rows only

_TOTAL = re.compile(r"^\s*(grand\s+)?(sub\s*)?total\b", re.I)
_PIVOT = re.compile(r"\b(pivot|summary)\b", re.I)
_NUM = re.compile(r"^[−-]?[₹$€]?\s*\(?[−-]?\d{1,3}(,\d{2,3})*(\.\d+)?\)?%?$|^[−-]?\d+(\.\d+)?%?$")
_UNITS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\(\s*₹\s*/\s*m\s*\)", re.I), "inr_per_m"),
    (re.compile(r"\(\s*₹\s*(l|lakh|lakhs|lac)\s*\)", re.I), "lakh"),
    (re.compile(r"\(\s*₹\s*(cr|crore|crores)\s*\)", re.I), "crore"),
    (re.compile(r"\(\s*₹\s*(k|thousand)\s*\)", re.I), "thousand"),
    (re.compile(r"\(\s*(₹|inr|rs\.?)\s*\)", re.I), "inr"),
    (re.compile(r"\(\s*%\s*\)"), "percent"),
]
UNIT_FACTOR = {"lakh": 100_000, "crore": 10_000_000, "thousand": 1_000, "inr": 1}
_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_DATE_FORMATS: list[tuple[str, re.Pattern[str], str]] = [
    ("dd-mm-yyyy", re.compile(r"^\d{2}-\d{2}-\d{4}$"), "%d-%m-%Y"),
    ("yyyy-mm-dd", re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d"),
    ("dd/mm/yyyy", re.compile(r"^\d{2}/\d{2}/\d{4}$"), "%d/%m/%Y"),
    ("d mmm yyyy", re.compile(rf"^\d{{1,2}} ({_MONTHS})\w* \d{{4}}$", re.I), "%d %b %Y"),
    ("mmm yyyy", re.compile(rf"^({_MONTHS})\w* \d{{4}}$", re.I), "%b %Y"),
    ("ddd d mmm", re.compile(rf"^\w{{3}} \d{{1,2}} ({_MONTHS})\w*$", re.I), "%a %d %b"),
    ("d mmm", re.compile(rf"^\d{{1,2}} ({_MONTHS})\w*$", re.I), "%d %b"),
]
_YEARLESS = {"ddd d mmm", "d mmm"}
_BLANKISH = {"", "—", "-", "–", "n/a", "na"}


def col_letter(i: int) -> str:
    """0-based index → A, B, …, Z, AA."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def blankish(v: str) -> bool:
    return v.strip().lower() in _BLANKISH


def parse_number(v: str) -> float | None:
    t = v.strip().replace("−", "-")
    if not _NUM.match(t):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = re.sub(r"[₹$€,()%\s]", "", t)
    try:
        n = float(t)
    except ValueError:
        return None
    return -n if neg else n


def date_format(v: str) -> str | None:
    t = v.strip()
    for name, pat, _ in _DATE_FORMATS:
        if pat.match(t):
            return name
    return None


def parse_date(v: str, fmt: str, *, year: int | None = None) -> dt.date | None:
    spec = next(s for n, _, s in _DATE_FORMATS if n == fmt)
    t = v.strip()
    try:
        if fmt in _YEARLESS:
            if year is None:
                return None
            if fmt == "ddd d mmm":
                t = t.split(" ", 1)[1]
                spec = "%d %b"
            # %b wants a 3-letter month; cut "Sept" etc.
            day, mon = t.split(" ", 1)
            return dt.datetime.strptime(f"{day} {mon[:3]} {year}", "%d %b %Y").date()
        if fmt in ("mmm yyyy", "d mmm yyyy"):
            parts = t.split(" ")
            parts[-2] = parts[-2][:3]
            t = " ".join(parts)
        return dt.datetime.strptime(t, spec).date()
    except ValueError:
        return None


def header_unit(header: str) -> str | None:
    for pat, unit in _UNITS:
        if pat.search(header):
            return unit
    return None


@dataclass(frozen=True)
class ColumnShape:
    letter: str
    header: str
    kind: str  # date · number · text · blank
    unit: str | None = None
    date_formats: tuple[str, ...] = ()
    distinct: int = 0
    nulls: int = 0


@dataclass(frozen=True)
class TabShape:
    tab: str
    kind: TabKind
    header_row: int | None = None
    title_rows: list[int] = field(default_factory=list)
    first_row: int | None = None
    last_row: int | None = None
    total_rows: list[int] = field(default_factory=list)
    columns: list[ColumnShape] = field(default_factory=list)
    blank_cols: list[str] = field(default_factory=list)
    notes_cols: list[str] = field(default_factory=list)
    filled_down: list[tuple[str, int]] = field(default_factory=list)
    newest_date: dt.date | None = None
    grid_rows: int = 0
    # dates written without a year on rows that carry none: read from the tab's order (10c asks
    # the owner to confirm), e.g. ("Weeks", "Oct 2026", "Jan 2027")
    yearless: tuple[str, str, str] | None = None

    @property
    def row_count(self) -> int:
        if self.first_row is None or self.last_row is None:
            return 0
        return self.last_row - self.first_row + 1

    @property
    def data_columns(self) -> list[ColumnShape]:
        return [c for c in self.columns if c.kind != "blank" and c.letter not in self.notes_cols]

    @property
    def range_a1(self) -> str:
        if not self.columns or self.first_row is None:
            return ""
        return f"A{self.first_row}:{self.columns[-1].letter}{self.last_row}"


def _grid(sheet: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = [
        [("" if v is None else str(v)) for v in r] for r in sheet.get("values", [])
    ]
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def _nonblank(row: Sequence[str]) -> list[str]:
    return [v for v in row if v.strip()]


def _find_header(grid: list[list[str]]) -> int | None:
    """0-based index of the first row with ≥ 2 filled cells, none of them numbers or dates."""
    for i, row in enumerate(grid[:HEADER_SCAN_ROWS]):
        cells = _nonblank(row)
        if len(cells) >= 2 and all(parse_number(c) is None and not date_format(c) for c in cells):
            # A header is followed by data, not by more text-only rows of the same width.
            return i
    return None


def _merges(sheet: dict[str, Any]) -> list[tuple[int, int, int, int]]:
    out = []
    for m in sheet.get("merges", []) or []:
        out.append(
            (
                int(m.get("startRowIndex", 0)),
                int(m.get("endRowIndex", 0)),
                int(m.get("startColumnIndex", 0)),
                int(m.get("endColumnIndex", 0)),
            )
        )
    return out


def row_years(grid: list[list[str]], rows: list[int], dated: list[int]) -> dict[int, int]:
    """A row's own year, from a dated cell that carries one ("Oct 2026" in a Month column)."""
    out: dict[int, int] = {}
    for r in rows:
        for i in dated:
            raw = grid[r][i] if i < len(grid[r]) else ""
            f = date_format(raw) if raw.strip() else None
            if f and f not in _YEARLESS:
                d = parse_date(raw, f)
                if d is not None:
                    out[r] = d.year
                    break
    return out


def order_years(
    grid: list[list[str]], rows: list[int], col: int, known: dict[int, int], today: dt.date
) -> tuple[dict[int, int], tuple[dt.date, dt.date] | None]:
    """Years for yearless dates on rows without their own (decided 5 Nov 2026): dates run down
    the tab, a month earlier than the row before rolls the year (Dec → Jan), and the last date
    is the latest one not after today. Returns the years and the span read."""
    seq: list[tuple[int, int, int]] = []
    for r in rows:
        if r in known:
            continue
        raw = grid[r][col] if col < len(grid[r]) else ""
        f = date_format(raw) if raw.strip() else None
        if f not in _YEARLESS:
            continue
        d = parse_date(raw, f, year=2000)  # a leap year: 29 Feb parses
        if d is not None:
            seq.append((r, d.month, d.day))
    if not seq:
        return {}, None
    offsets, off, prev = [], 0, None
    for _, m, _d in seq:
        if prev is not None and m < prev:
            off += 1
        offsets.append(off)
        prev = m
    last_r, last_m, last_d = seq[-1]
    base = today.year - offsets[-1]

    def at(year: int, m: int, d: int) -> dt.date:
        try:
            return dt.date(year, m, d)
        except ValueError:
            return dt.date(year, m, 28)

    if at(base + offsets[-1], last_m, last_d) > today:
        base -= 1
    years = {r: base + o for (r, _, _), o in zip(seq, offsets, strict=True)}
    first = at(years[seq[0][0]], seq[0][1], seq[0][2])
    return years, (first, at(years[last_r], last_m, last_d))


def _year_hint(grid: list[list[str]], title_rows: list[int]) -> int | None:
    for r in title_rows:
        m = re.search(r"FY\s?(\d{2})\b", " ".join(grid[r - 1]))
        if m:
            return 2000 + int(m.group(1)) - 1
    return None


def detect(
    sheet: dict[str, Any],
    *,
    today: dt.date,
    modified: dt.datetime | None = None,
    header_row: int | None = None,
    last_row: int | None = None,
    units: dict[str, str] | None = None,
) -> TabShape:
    """Find the table in one tab. `header_row` / `last_row` (1-based) are the owner's fixes
    from the 10c stepper and last-row selector; `units` (header → lakh, crore, thousand, inr)
    the owner's unit for a column ("values are in ₹ crore", 10c Fix with Othor AI)."""
    title = str(sheet.get("properties", {}).get("title", ""))
    grid = _grid(sheet)
    filled = [i for i, r in enumerate(grid) if _nonblank(r)]
    if not filled:
        return TabShape(title, "empty", grid_rows=len(grid))

    h = (header_row - 1) if header_row else _find_header(grid)
    if h is None:
        return TabShape(title, "free_text", grid_rows=len(grid))
    title_rows = [i + 1 for i in range(h) if _nonblank(grid[i])]
    header = grid[h]

    # data rows: after the header to the last filled row, totals at the end left out
    end = max(filled)
    if last_row:
        end = min(end, last_row - 1)
    totals: list[int] = []
    i = end
    while i > h:
        first = next((v for v in grid[i] if v.strip()), "")
        if _TOTAL.match(first):
            totals.append(i + 1)
            i -= 1
            continue
        if not _nonblank(grid[i]):
            i -= 1
            continue
        break
    data_end = i
    # subtotal rows inside the data are left out too
    for r in range(h + 1, data_end + 1):
        first = next((v for v in grid[r] if v.strip()), "")
        if _TOTAL.match(first):
            totals.append(r + 1)
    totals.sort()
    if last_row and last_row - 1 < max(filled):
        pass  # the owner chose the last row; rows after it are simply not read
    data_idx = [r for r in range(h + 1, data_end + 1) if (r + 1) not in totals]

    # merged cells inside the data: vertical merges are filled down
    filled_down: list[tuple[str, int]] = []
    for r0, r1, c0, c1 in _merges(sheet):
        if c1 - c0 == 1 and r1 - r0 > 1 and h < r0 <= data_end:
            filled_down += [(col_letter(c0), r + 1) for r in range(r0 + 1, min(r1, data_end + 1))]

    width = max(len(header), *(len(grid[r]) for r in data_idx)) if data_idx else len(header)
    # trim empty trailing columns
    while (
        width
        and not header[width - 1].strip()
        and all(not grid[r][width - 1].strip() for r in data_idx)
    ):
        width -= 1

    year = _year_hint(grid, title_rows) or today.year
    cols: list[ColumnShape] = []
    blank_cols: list[str] = []
    notes_cols: list[str] = []
    newest: dt.date | None = None
    for c in range(width):
        letter = col_letter(c)
        head = header[c].strip() if c < len(header) else ""
        vals = [grid[r][c] for r in data_idx]
        present = [v for v in vals if not blankish(v)]
        if not head and not present:
            blank_cols.append(letter)
            cols.append(ColumnShape(letter, "", "blank"))
            continue
        if not head:
            notes_cols.append(letter)
            cols.append(ColumnShape(letter, "", "text", nulls=len(vals) - len(present)))
            continue
        kind = "text"
        fmts: tuple[str, ...] = ()
        if present:
            found = [date_format(v) for v in present]
            if sum(1 for f in found if f) >= 0.8 * len(present):
                kind = "date"
                fmts = tuple(sorted({f for f in found if f}))
                for v, f in zip(present, found, strict=True):
                    if f:
                        d = parse_date(v, f, year=year)
                        if d and f not in _YEARLESS and (newest is None or d > newest):
                            newest = d
            elif sum(1 for v in present if parse_number(v) is not None) >= 0.8 * len(present):
                kind = "number"
        # merged cells count as present after fill-down
        merged_blanks = sum(1 for (L, r) in filled_down if letter == L)
        cols.append(
            ColumnShape(
                letter,
                head,
                kind,
                (units or {}).get(head)
                if (units or {}).get(head) in UNIT_FACTOR
                else header_unit(head),
                fmts,
                len(set(present)),
                max(0, len(vals) - len(present) - merged_blanks),
            )
        )

    yearless = None
    hint = _year_hint(grid, title_rows)
    dated = [i for i, c in enumerate(cols) if c.kind == "date"]
    for i, shape_col in enumerate(cols):
        if hint is None and shape_col.kind == "date" and set(shape_col.date_formats) & _YEARLESS:
            known = row_years(grid, data_idx, dated)
            _years, span = order_years(grid, data_idx, i, known, today)
            if span:
                noun = "Weeks" if "week" in shape_col.header.lower() else "Dates"
                yearless = (noun, f"{span[0]:%b %Y}", f"{span[1]:%b %Y}")
            break
    shape = TabShape(
        title,
        "table",
        h + 1,
        title_rows,
        (data_idx[0] + 1) if data_idx else None,
        (data_end + 1) if data_idx else None,
        totals,
        cols,
        blank_cols,
        notes_cols,
        sorted(filled_down),
        newest,
        len(grid),
        yearless,
    )
    if _PIVOT.search(title) or (title_rows and _PIVOT.search(" ".join(grid[title_rows[0] - 1]))):
        return _replace(shape, kind="pivot")
    stale_ref = newest
    if stale_ref is None and modified is not None:
        stale_ref = modified.date()
    if stale_ref and (today - stale_ref).days > STALE_AFTER_DAYS:
        return _replace(shape, kind="stale")
    return shape


def _replace(s: TabShape, **kw: Any) -> TabShape:
    from dataclasses import replace

    return replace(s, **kw)


def needs_check(s: TabShape) -> bool:
    """A tab is confirmed by the owner (10c) when its shape isn't the plain case."""
    if s.kind != "table":
        return False
    mixed_dates = any(len(c.date_formats) > 1 for c in s.columns)
    return bool(
        s.yearless
        or s.header_row != 1
        or s.total_rows
        or s.filled_down
        or s.blank_cols
        or s.notes_cols
        or mixed_dates
    )


_KIND_TO_COL = {"date": "date", "number": "decimal", "text": "text"}


def tab_use(s: TabShape) -> Sorted:
    """Default use of a tab, with the same rules as a database table."""
    if s.kind == "pivot":
        return Sorted("ignored", "copy")
    if s.kind in ("free_text", "empty"):
        return Sorted("ignored", "no_table")
    if s.kind == "stale":
        return Sorted("ignored", "old")
    cols = tuple(Col(c.header, _KIND_TO_COL[c.kind]) for c in s.data_columns)
    t = TableFacts("sheet", s.tab, cols, s.row_count, s.newest_date)
    got = classify(t, dt.date.max if s.newest_date is None else s.newest_date)
    if got.use == "excluded":
        return got
    # A tab with dates is something to count or add up; without dates, a list to split by.
    if any(c.kind == "date" for c in s.data_columns):
        return Sorted("metric_source", None, got.personal)
    return Sorted("dimension", None, got.personal)
