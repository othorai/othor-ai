# SPDX-License-Identifier: Apache-2.0
"""View models for the Sources screens (2j–2ke, 10a–10c, 6c–6i, 10m, 10r).

Everything on screen is computed here from the metadata DB; templates only lay it out. Callers
hold the tenant scope. Numbers go through othor.core.fmt.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.connectors.classify import PERSONAL_LABEL, personal_label
from othor.connectors.models import (
    Scan,
    SheetTab,
    Source,
    SourceColumn,
    SourceSchema,
    SourceTable,
)
from othor.connectors.service import USE_LABEL, MenuState, menu_state, reason_label
from othor.connectors.sheets.detect import date_format
from othor.core import fmt
from othor.core.config import Settings

PAGE_SIZE = 50
KIND_TILE = {"postgres": "PG", "mysql": "MY", "gsheets": "GS"}
KIND_NAME = {"postgres": "PostgreSQL", "mysql": "MySQL", "gsheets": "Google Sheets"}
CONNECTORS = [
    ("postgres", "PostgreSQL", "Database · read in place"),
    ("mysql", "MySQL", "Database · read in place"),
    ("gsheets", "Google Sheets", "Sheets you pick · read-only"),
]
TABS = ("candidates", "ignored", "excluded")
TAB_LABEL = {
    "candidates": "Candidates",
    "ignored": "Ignored",
    "excluded": "Excluded",
    "all": "All matches",
}
SORTED_TEXT = {
    "metric_source": "Facts with amounts and dates",
    "dimension": "Lists to split numbers by",
}
IGNORE_ONE = {
    "empty": "empty",
    "backup": "backup or temp",
    "staging": "staging copy",
    "log": "log",
    "old": "old data",
    "not_needed": "not needed",
    "copy": "copy of another tab",
    "no_table": "no table",
    "duplicate": "duplicate",
    "test_old": "test or old data",
}
IGNORE_PLURAL = {
    "empty": "empty",
    "backup": "backup or temp",
    "staging": "staging copies",
    "log": "logs",
    "old": "old data",
    "not_needed": "not needed",
    "copy": "copies of another tab",
    "no_table": "no table",
    "duplicate": "duplicates",
    "test_old": "test or old data",
}


def rows_text(n: int | None, estimated: bool = False) -> str:
    if n is None:
        return "–"
    return ("~" if estimated else "") + fmt.num(n)


def short_rows(n: int | None) -> str:
    """6d: '1.2 M', '84 k', '4,112' (compact, from the computed count)."""
    if n is None:
        return "–"
    if n >= 1_000_000:
        return f"{fmt.num(n / 1_000_000, decimals=1)} M"
    if n >= 100_000:
        return f"{fmt.num(n / 1_000)} k"
    return fmt.num(n)


def eta_text(seconds: int) -> str:
    if seconds <= 0:
        return "done"
    if seconds < 50:
        return "less than a minute left"
    m = round(seconds / 60)
    return f"about {m} minute{'s' if m != 1 else ''} left"


def use_chip(t: SourceTable) -> dict[str, str]:
    """Label and dot for the Use chip."""
    st = menu_state(t)
    if st.kind == "not_ready":
        return {
            "label": "Reading" if t.state == "reading" else "Queued",
            "dot": "",
            "kind": "not_ready",
        }
    if t.use == "excluded":
        return {"label": "Excluded · Personal data", "dot": "neg", "kind": "excluded"}
    if t.use == "ignored":
        return {"label": f"Ignored · {reason_label(t.use_reason)}", "dot": "muted", "kind": st.kind}
    label = USE_LABEL.get(t.use or "", "")
    if t.without_personal:
        label += " · without personal"
    return {"label": label, "dot": "ok", "kind": "candidate"}


@dataclass
class Row:
    t: SourceTable
    chip: dict[str, str]
    menu: MenuState
    rows: str
    personal: str = ""
    read: list[str] = field(default_factory=list)
    never: list[str] = field(default_factory=list)
    who: str = ""

    @property
    def short(self) -> str:
        return short_rows(self.t.row_count)

    @property
    def qualified(self) -> str:
        return f"{self.t.schema}.{self.t.name}"


def tab_of(t: SourceTable) -> str:
    if t.use == "excluded":
        return "excluded"
    if t.use == "ignored":
        return "ignored"
    return "candidates"


def _personal_words(cols: list[SourceColumn]) -> str:
    return personal_label(tuple((c.name, c.personal_kind or "") for c in cols if c.is_personal))


@dataclass
class TableList:
    source: Source
    tab: str
    q: str
    page: int
    counts: dict[str, int]
    match_counts: dict[str, int]
    rows: list[Row]
    total: int
    scanned: int
    pages: int
    expand: uuid.UUID | None = None

    @property
    def searching(self) -> bool:
        return bool(self.q)


def table_list(
    s: Session,
    src: Source,
    *,
    tab: str = "candidates",
    q: str = "",
    page: int = 1,
    expand: uuid.UUID | None = None,
    owner_names: dict[str, str] | None = None,
) -> TableList:
    tables = s.scalars(
        sa.select(SourceTable).where(SourceTable.source_id == src.id, SourceTable.gone_at.is_(None))
    ).all()
    done = [t for t in tables if t.state == "done"]
    counts = {k: 0 for k in TABS}
    for t in done:
        counts[tab_of(t)] += 1
    q = q.strip()
    ql = q.lower()
    if ql:
        col_hits = {
            r[0]
            for r in s.execute(
                sa.select(SourceColumn.table_id).where(
                    SourceColumn.table_id.in_([t.id for t in tables]),
                    SourceColumn.is_personal.is_(False),
                    sa.func.lower(SourceColumn.name).contains(ql),
                )
            )
        }
        pool = [t for t in tables if ql in f"{t.schema}.{t.name}".lower() or t.id in col_hits]
        match_counts = {k: 0 for k in TABS}
        for t in pool:
            if t.state == "done":
                match_counts[tab_of(t)] += 1
        if tab != "all":
            pool = [t for t in pool if t.state == "done" and tab_of(t) == tab]
    else:
        match_counts = {}
        pool = [t for t in done if tab_of(t) == tab]
        if tab == "candidates":  # tables still reading show under Candidates, last
            pool += [t for t in tables if t.state in ("queued", "reading")]
    pool.sort(key=lambda t: (t.state != "done", -(t.row_count or 0), t.schema, t.name))
    pages = max(1, -(-len(pool) // PAGE_SIZE))
    page = min(max(page, 1), pages)
    chunk = pool[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    cols: dict[uuid.UUID, list[SourceColumn]] = {}
    if chunk:
        for c in s.scalars(
            sa.select(SourceColumn)
            .where(SourceColumn.table_id.in_([t.id for t in chunk]))
            .order_by(SourceColumn.position)
        ):
            cols.setdefault(c.table_id, []).append(c)
    rows = []
    for t in chunk:
        cs = cols.get(t.id, [])
        who = ""
        if t.use_set_by == "user" and t.use_set_at:
            name = (owner_names or {}).get(t.use_set_by_user or "", "you")
            who = f"{name} · {fmt.day_month(t.use_set_at.date())}"
        rows.append(
            Row(
                t,
                use_chip(t),
                menu_state(t),
                rows_text(t.row_count, t.row_count_estimated),
                _personal_words(cs),
                [c.name for c in cs if not c.is_personal][:8],
                [c.name for c in cs if c.is_personal],
                who,
            )
        )
    return TableList(
        src, tab, q, page, counts, match_counts, rows, len(pool), len(done), pages, expand
    )


# --- scan progress (2k) ------------------------------------------------------------------------


@dataclass
class ScanView:
    source: Source
    scan: Scan | None
    running: bool
    total: int
    scanned: int
    schemas: list[dict[str, Any]]
    sorted: list[dict[str, Any]]
    line: str


def scan_view(s: Session, src: Source) -> ScanView:
    sc = s.scalars(
        sa.select(Scan).where(Scan.source_id == src.id).order_by(Scan.created_at.desc()).limit(1)
    ).first()
    running = sc is not None and sc.status in ("queued", "running")
    schemas = s.scalars(
        sa.select(SourceSchema).where(SourceSchema.source_id == src.id).order_by(SourceSchema.name)
    ).all()
    sch = []
    for x in schemas:
        done = min(x.scanned_count, x.table_count) if (running or x.skipped) else x.table_count
        if x.skipped:
            status, dot = "Skipped", "muted"
        elif done >= x.table_count:
            status, dot = "Done", "ok"
        elif done or running:
            status, dot = "Scanning" if done else "Queued", "warn" if done else "muted"
        else:
            status, dot = "Done", "ok"
        sch.append(
            {
                "name": x.name,
                "done": done,
                "total": x.table_count,
                "status": status,
                "dot": dot,
                "pct": int(100 * done / x.table_count) if x.table_count else 100,
                "skippable": running and not x.skipped and done < x.table_count,
            }
        )
    # M15.1: the five columns this view needs, not every scanned table as an ORM object
    tables = s.execute(
        sa.select(
            SourceTable.id,
            SourceTable.use,
            SourceTable.use_reason,
            SourceTable.name,
            SourceTable.row_count,
        ).where(
            SourceTable.source_id == src.id,
            SourceTable.gone_at.is_(None),
            SourceTable.state == "done",
        )
    ).all()
    by_use: dict[str, list[Any]] = {
        "metric_source": [],
        "dimension": [],
        "ignored": [],
        "excluded": [],
    }
    for t in tables:
        by_use.setdefault(t.use or "ignored", []).append(t)
    reasons: dict[str, int] = {}
    for t in by_use["ignored"]:
        reasons[t.use_reason or "not_needed"] = reasons.get(t.use_reason or "not_needed", 0) + 1
    ign_text = " · ".join(
        f"{fmt.num(n)} {(IGNORE_ONE if n == 1 else IGNORE_PLURAL).get(r, r)}"
        for r, n in sorted(reasons.items(), key=lambda x: -x[1])
    )
    kinds: list[str] = []
    if by_use["excluded"]:
        for c in s.scalars(
            sa.select(SourceColumn).where(
                SourceColumn.table_id.in_([t.id for t in by_use["excluded"]]),
                SourceColumn.is_personal.is_(True),
            )
        ):
            if c.personal_kind and c.personal_kind not in kinds:
                kinds.append(c.personal_kind)
    exc_text = personal_label(tuple(("", k) for k in kinds[:3])) if kinds else ""
    dims = sorted(by_use["dimension"], key=lambda t: -(t.row_count or 0))[:3]
    dim_text = ", ".join(_dim_word(t.name) for t in dims) if dims else ""
    sorted_rows = [
        {
            "label": "Metric source",
            "dot": "ok",
            "n": len(by_use["metric_source"]),
            "text": SORTED_TEXT["metric_source"],
        },
        {
            "label": "Dimension",
            "dot": "ok",
            "n": len(by_use["dimension"]),
            "text": dim_text.capitalize() if dim_text else SORTED_TEXT["dimension"],
        },
        {"label": "Ignore", "dot": "muted", "n": len(by_use["ignored"]), "text": ign_text},
        {
            "label": "Excluded · personal data",
            "dot": "neg",
            "n": len(by_use["excluded"]),
            "text": exc_text,
        },
    ]
    p = (sc.progress if sc else {}) or {}
    total = int(p.get("total") or sum(x["total"] for x in sch if x["status"] != "Skipped"))
    scanned = int(p.get("scanned") or len(tables))
    n_sch = len(sch)
    sch_done = sum(1 for x in sch if x["status"] in ("Done", "Skipped"))
    line = (
        f"{fmt.num(min(scanned, total))} of {fmt.num(total)} tables · "
        f"{fmt.num(sch_done)} of {fmt.num(n_sch)} schema{'s' if n_sch != 1 else ''} done"
        + (" · " + eta_text(int(p.get("eta_s") or 0)) if running else "")
    )
    return ScanView(src, sc, running, total, scanned, sch, sorted_rows, line)


def schema_rows(s: Session, src: Source) -> list[dict[str, Any]]:
    """The Schemas panel (M11c): each schema the role can see, its tables, how many are used,
    and whether it is read. Counts come from the last scan."""
    used: dict[str, int] = {}
    for schema, n in s.execute(
        sa.select(SourceTable.schema, sa.func.count())
        .where(
            SourceTable.source_id == src.id,
            SourceTable.gone_at.is_(None),
            SourceTable.state == "done",
            SourceTable.use.in_(("metric_source", "dimension")),
        )
        .group_by(SourceTable.schema)
    ):
        used[str(schema)] = int(n)
    return [
        {
            "name": x.name,
            "tables": x.table_count,
            "used": used.get(x.name, 0),
            "read": not x.skipped,
        }
        for x in s.scalars(
            sa.select(SourceSchema)
            .where(SourceSchema.source_id == src.id)
            .order_by(SourceSchema.name)
        )
    ]


def scan_steps(s: Session, src: Source, view: ScanView) -> list[dict[str, str]]:
    """The scanning panel's lines (M11c, p2): connected, what was found, reading, sorting.
    Each has a state (done · now · next) and a computed note on the right."""
    test = src.last_test or {}
    sheets = src.kind == "gsheets"
    lat = test.get("latency_ms")
    steps = [
        {
            "label": "Connected · " + ("read-only access" if sheets else "read-only role"),
            "note": f"{fmt.num(lat)} ms" if lat is not None else "",
            "state": "done",
        }
    ]
    if sheets:
        files = [str(f.get("title") or "") for f in (src.config or {}).get("files", [])]
        steps.append(
            {
                "label": "Spreadsheets: " + ", ".join(f for f in files if f),
                "note": "",
                "state": "done",
            }
        )
    else:
        names = [x["name"] for x in view.schemas]
        read = [x["name"] for x in view.schemas if x["status"] != "Skipped"]
        if src.kind == "mysql":
            found = {"label": f"Database: {(src.config or {}).get('database', '')}", "note": ""}
        else:
            found = {
                "label": "Schemas found: " + ", ".join(read),
                "note": f"{fmt.num(len(read))} of {fmt.num(len(names))} the role can see",
            }
        steps.append({**found, "state": "done" if names else "now"})
        if not names:
            steps[-1]["label"] = "Finding the schemas the role can see"
    unit = "tabs" if sheets else "tables"
    reading_done = not view.running
    steps.append(
        {
            "label": f"Reading {unit} and columns",
            "note": f"{fmt.num(min(view.scanned, view.total))} of {fmt.num(view.total)}"
            if view.total
            else "",
            "state": "done" if reading_done else ("now" if view.schemas or sheets else "next"),
        }
    )
    by = {x["label"]: x["n"] for x in view.sorted}
    used = by.get("Metric source", 0) + by.get("Dimension", 0)
    steps.append(
        {
            "label": f"Sorting {unit}: used, ignored, excluded",
            "note": (
                f"{fmt.num(used)} used · {fmt.num(by.get('Ignore', 0))} ignored · "
                f"{fmt.num(by.get('Excluded · personal data', 0))} excluded"
            )
            if reading_done
            else "",
            "state": "done" if reading_done else "next",
        }
    )
    return steps


def _dim_word(name: str) -> str:
    from othor.connectors.classify import _stem

    w = _stem(name).replace("_", " ")
    return w + "s" if not w.endswith("s") else w


# --- sources list and summary (2l, 6c, 6d, 10r) --------------------------------------------------


@dataclass
class SourceCard:
    src: Source
    tile: str
    title: str
    sub: str
    status: tuple[str, str]  # label, dot
    tables_total: int
    tables_used: int
    rows_used: int
    excluded: int
    ignored: int
    schemas: list[str]
    last_read: str
    connected_by: str
    feeds: list[str] = field(default_factory=list)  # confirmed metrics read from this source
    briefs: list[str] = field(default_factory=list)  # briefs that use those metrics (M12b)


def status_of(src: Source) -> tuple[str, str]:
    if src.status == "connected" and src.last_read_at is None:
        return ("First read tonight", "muted")
    return {
        "connected": ("Healthy", "ok"),
        "scanning": ("Scanning", "warn"),
        "paused": ("Paused", "muted"),
        "disconnected": ("Disconnected", "neg"),
        "draft": ("Not connected", "warn"),
    }.get(src.status, (src.status.capitalize(), "muted"))


def source_card(
    s: Session, src: Source, settings: Settings | None = None, names: dict[str, str] | None = None
) -> SourceCard:
    from othor.ontology.compiler.catalog import unchosen_tabs

    q = sa.select(SourceTable.use, sa.func.count(), sa.func.sum(SourceTable.row_count)).where(
        SourceTable.source_id == src.id,
        SourceTable.gone_at.is_(None),
        SourceTable.state == "done",
    )
    if src.kind == "gsheets" and (skip := unchosen_tabs(s)):
        q = q.where(SourceTable.id.not_in(skip))  # only the tabs the owner chose (10b)
    counts = s.execute(q.group_by(SourceTable.use)).all()
    by = {u: (int(n), int(r or 0)) for u, n, r in counts}
    used = sum(by.get(u, (0, 0))[0] for u in ("metric_source", "dimension"))
    rows_used = sum(by.get(u, (0, 0))[1] for u in ("metric_source", "dimension"))
    total = sum(n for n, _ in by.values())
    c = src.config or {}
    if src.kind == "gsheets":
        files = c.get("files", [])
        title = files[0].get("title", "Google Sheets") if files else "Google Sheets"
        sub = f"via Google Sheets · {c.get('account', '')}".rstrip(" ·")
        schemas = [f.get("title", "") for f in files]
    else:
        title = f"{KIND_NAME[src.kind]} · {c.get('database', src.name)}"
        # the schemas read: the scan's (M11c, read from the role), else the config's
        read = [
            str(n)
            for n in s.scalars(
                sa.select(SourceSchema.name)
                .where(SourceSchema.source_id == src.id, SourceSchema.skipped.is_(False))
                .order_by(SourceSchema.name)
            )
        ]
        schemas = read or list(c.get("schemas") or [c.get("database", "")])
        # the schemas read, unless the only one is the database itself (MySQL); a schema that
        # shares the database's name is still listed when there are others (sales_dw, 2l)
        extra = (
            [x for x in schemas if x]
            if len(schemas) > 1
            else [x for x in schemas if x and x != c.get("database")]
        )
        sub = " · ".join(
            p for p in (c.get("database", ""), ", ".join(extra), f"{fmt.num(total)} tables") if p
        )
    last = "–"
    if src.last_read_at:
        # stored in UTC; shown in the company's zone (it showed UTC: 18:01 for 23:31 IST, 11 Nov)
        last = f"{fmt.day_month(fmt.local(src.last_read_at).date())} {fmt.clock(src.last_read_at)}"
    who = (names or {}).get(src.connected_by or "", "")
    return SourceCard(
        src,
        KIND_TILE.get(src.kind, "?"),
        title,
        sub,
        status_of(src),
        total,
        used,
        rows_used,
        by.get("excluded", (0, 0))[0],
        by.get("ignored", (0, 0))[0],
        schemas,
        last,
        who,
        feeds(s, src),
        briefs_using(s, src),
    )


def sources(s: Session, *, in_setup: bool | None = None) -> list[Source]:
    q = sa.select(Source).where(Source.deleted_at.is_(None), Source.status != "draft")
    if in_setup is not None:
        q = q.where(Source.in_setup.is_(in_setup))
    return list(s.scalars(q.order_by(Source.created_at)).all())


def personal_kinds_text(kinds: list[str]) -> str:
    return ", ".join(PERSONAL_LABEL.get(k, k) for k in kinds)


# --- Sheets tabs (10b, 6eb, 6f) and the check (10c) -----------------------------------------------


@dataclass
class TabRow:
    tab: SheetTab
    table: SourceTable | None
    found: str
    chip: dict[str, str]
    needs_check: bool
    disabled: bool


def tab_rows(s: Session, src: Source) -> list[TabRow]:
    tabs = s.scalars(
        sa.select(SheetTab)
        .where(SheetTab.source_id == src.id)
        .order_by(SheetTab.spreadsheet_title, SheetTab.sheet_id)
    ).all()
    tables = {
        t.id: t for t in s.scalars(sa.select(SourceTable).where(SourceTable.source_id == src.id))
    }
    out = []
    for tab in tabs:
        t = tables.get(tab.table_id) if tab.table_id else None
        shape = tab.shape or {}
        kind = shape.get("kind")
        if kind == "table":
            n_rows = fmt.num(shape.get("row_count", 0))
            found = f"{n_rows} rows · header on row {shape.get('header_row')}"
            if shape.get("total_rows"):
                found += " · total row at the end"
        elif kind == "pivot":
            found = "Pivot of another tab"
        elif kind == "free_text":
            found = "Free text"
        elif kind == "stale":
            found = t.looks_like if t and t.looks_like else "Old"
        else:
            found = "Empty"
        needs = (
            tab.status in ("unchecked", "needs_check")
            and kind == "table"
            and (t is None or t.use != "excluded")
        )
        if tab.status == "access_removed":
            chip = {"label": "Access removed · Choose again", "dot": "neg"}
        elif needs and tab.selected:
            chip = {"label": "Check the header", "dot": "warn"}
        elif t is not None:
            chip = use_chip(t)
            if t.use == "ignored":
                chip = {**chip, "label": f"Ignored · {reason_label(t.use_reason).lower()}"}
        else:
            chip = {"label": "", "dot": ""}
        out.append(TabRow(tab, t, found, chip, needs, t is not None and t.use == "excluded"))
    return out


@dataclass
class CheckView:
    tab: SheetTab
    columns: list[dict[str, Any]]
    grid: list[dict[str, Any]]
    read: list[tuple[str, str, str]]
    last_row_label: str


def check_view(tab: SheetTab, settings: Settings | None = None) -> CheckView:
    shape = tab.shape or {}
    cols = shape.get("columns", [])
    preview = shape.get("preview", {"rows": [], "total_rows": 0})
    header = shape.get("header_row")
    first, last = shape.get("first_row"), shape.get("last_row")
    totals = set(shape.get("total_rows", []))
    titles = set(shape.get("title_rows", []))
    blank = set(shape.get("blank_cols", []))
    filled = {(c, r) for c, r in shape.get("filled_down", [])}
    grid: list[dict[str, Any]] = []
    prev_n = 0
    last_seen: dict[int, str] = {}
    main_fmt: dict[int, str | None] = {}
    for i in range(len(cols)):  # the format of the first data row is the main one
        first_row = next((cells for n, cells in preview["rows"] if n == first), None)
        if first_row and i < len(first_row):
            main_fmt[i] = date_format(first_row[i])
    for n, cells in preview["rows"]:
        if prev_n and n > prev_n + 1:
            grid.append(
                {"n": "…", "cls": "", "gap": True, "cells": [{"v": "…", "cls": ""} for _ in cols]}
            )
        prev_n = n
        cls = (
            "head"
            if n == header
            else ("out" if (n in titles or n in totals or (last and n > last)) else "")
        )
        row = []
        for i, c in enumerate(cols):
            v = cells[i] if i < len(cells) else ""
            ccls = []
            if c["letter"] in blank:
                ccls.append("blank")
            if (c["letter"], n) in filled:
                ccls.append("filled")
                v = v or last_seen.get(i, "")  # shown as read: the merged value, filled down
            fmts = c.get("date_formats") or []
            if len(fmts) > 1 and cls == "" and v and date_format(v) not in (None, main_fmt.get(i)):
                ccls.append("alt-fmt")  # the tab's second date format, read as a date too
            if v:
                last_seen[i] = v
            row.append({"v": v, "cls": " ".join(ccls)})
        grid.append({"n": n, "cls": cls, "cells": row, "gap": False})
    read: list[tuple[str, str, str]] = []
    if header:
        sub = ""
        if titles:
            rs = sorted(titles)
            sub = (
                f"Rows {rs[0]}–{rs[-1]} are a title, left out"
                if len(rs) > 1
                else f"Row {rs[0]} is a title, left out"
            )
        read.append(("Header", f"Row {header}", sub))
    if first and last:
        n_rows = last - first + 1 - len([t for t in totals if first <= t <= last])
        sub = ", ".join(f"Row {t} is a total, left out" for t in sorted(totals)) if totals else ""
        read.append(("Range", f"{shape.get('range')} · {fmt.num(n_rows)} rows", sub))
    for letter in shape.get("blank_cols", []):
        read.append((f"Column {letter}", "Blank, left out", ""))
    for letter in shape.get("notes_cols", []):
        read.append((f"Column {letter}", "Notes beside the table, left out", ""))
    merged: dict[str, list[int]] = {}
    for letter, r in shape.get("filled_down", []):
        merged.setdefault(letter, []).append(r)
    for letter, rws in merged.items():
        head = next((c["header"] for c in cols if c["letter"] == letter), letter)
        read.append(
            (
                head,
                f"{fmt.num(len(rws))} merged cell{'s' if len(rws) != 1 else ''}, filled down",
                "Rows " + " and ".join(fmt.num(r) for r in sorted(rws)),
            )
        )
    for c in cols:
        if len(c.get("date_formats", [])) > 1:
            read.append(
                (
                    c["header"],
                    f"{_number_word(len(c['date_formats']))} formats, read as dates",
                    " and ".join(c["date_formats"]),
                )
            )
    if shape.get("yearless"):
        noun, first, last = shape["yearless"]
        span = first if first == last else f"{first} – {last}"
        read.append((noun, f"{noun} have no year; we read them as {span}.", "Please confirm"))
    totals_by = shape.get("totals", {})
    for c in cols:
        if c.get("unit") in ("lakh", "crore"):
            sub = ""
            if c["header"] in totals_by:
                unit = "l" if c["unit"] == "lakh" else "cr"
                sub = f"{fmt.inr(Decimal(totals_by[c['header']]), unit=unit)} in total"
            read.append((c["header"], f"Read as {fmt.currency_symbol()} {c['unit']}", sub))
    for c in cols:
        if c["kind"] == "text" and 1 < c.get("distinct", 0) <= 8 and c["header"]:
            read.append((c["header"], f"{fmt.num(c['distinct'])} values", ""))
    last_label = "Before the total" if totals else (f"Row {last}" if last else "")
    return CheckView(tab, cols, grid, read, last_label)


def _number_word(n: int) -> str:
    return {1: "One", 2: "Two", 3: "Three", 4: "Four"}.get(n, fmt.num(n))


def today_utc() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def affects(s: Session, settings: Settings, src: Source) -> list[dict[str, str]]:
    """10m "What it affects": each confirmed number this source feeds, its value as of the last
    good read (the tile's own value and stale mark, computed), and the briefs it's in."""
    from othor.web import ontology_view as ov

    keys = feed_keys(s, src, unlisted=True)  # an outage affects every number, listed or not (10k)
    if not keys:
        return []
    rd = ov.run_data(s, settings)
    used = ov.in_briefs(s)
    out = []
    for key, name in keys:
        t = ov.tile_for(rd, key)
        value = "—"
        if t is not None:
            value = t["value"] + (f" · {t['stale']}" if t.get("stale") else "")
        briefs = ", ".join(sorted(used.get(key, []))) or "Not in a brief"
        out.append({"name": name, "value": value, "briefs": briefs})
    return out


def briefs_using(s: Session, src: Source) -> list[str]:
    """The briefs that show a number this source feeds (6d impact, 10r, M12b)."""
    from othor.web.ontology_view import in_briefs

    used = in_briefs(s)
    keys = [k for k, _n in feed_keys(s, src, unlisted=True)]
    return sorted({b for k in keys for b in used.get(k, [])})


def feeds(s: Session, src: Source) -> list[str]:
    """Confirmed metrics this source feeds (names); see feed_keys."""
    return [name for _key, name in feed_keys(s, src)]


def feed_keys(s: Session, src: Source, unlisted: bool = False) -> list[tuple[str, str]]:
    """Confirmed metrics this source feeds, from the spec of the current Ontology version: its
    own metrics and those derived from them (listed ones; components stay hidden as on 6a)."""
    from othor.ontology import service as ontology
    from othor.ontology.models import Spec

    v = ontology.current_number(s)
    if not v:
        return []
    # the current version's spec; until the next run compiles it, the latest compiled one, so
    # Feeds and the detail pane are never empty between versions (owner, 11 Nov 2026)
    spec = s.scalars(
        sa.select(Spec)
        .where(Spec.ontology_version <= v)
        .order_by(Spec.ontology_version.desc(), Spec.created_at.desc())
    ).first()
    if spec is None:
        return []
    doc = spec.compiled
    name = next((n for n, m in doc.get("sources", {}).items() if m.get("id") == str(src.id)), None)
    if name is None:
        return []
    out = []
    for key in doc.get("order", []):
        m = doc["metrics"].get(key, {})
        if m.get("status") != "active" or (m.get("listed") is False and not unlisted):
            continue
        if name in (m.get("sources") or []):
            out.append((key, m.get("name") or key))
    return out


# --- cross-source matching on the detail pane (6c/6h, decided 5 Nov 2026) -----------------


def matches(s: Session, settings: Settings, src: Source) -> dict[str, Any]:
    """This source's matches with the others, from the latest nightly run's outputs, and its
    one join proposal (each tab with its rate and status). Empty until the first nightly read."""
    from othor.engine.models import DETECTING_KINDS, Proposal, Run
    from othor.engine.proposals import tab_line
    from othor.engine.store import RunFiles, read_json

    run = s.scalars(
        sa.select(Run)
        .where(Run.status.in_(("done", "partial")), Run.kind.in_(DETECTING_KINDS))
        .order_by(Run.number.desc())
    ).first()
    if run is None:
        return {"rows": [], "proposal": None}
    doc = (read_json(RunFiles(settings.runs_dir(run.company_id), run.id).outputs_json) or {}).get(
        "sweep"
    ) or {}
    items = []
    for m in doc.get("matches") or []:
        if m["source"] != src.name:
            continue
        by = "GSTIN" if m["grade"] == "identity" else "name"
        items.append(
            {
                "text": (
                    f"{m['table']}: {fmt.pct(m['share'])} of {m['source']} {m['entity']}s match "
                    f"{m['to_source']} {m['entity']}s by {by} "
                    f"({fmt.num(m['matched'])} of {fmt.num(m['total'])})."
                ),
                "unmatched": m["unmatched"],
                "examples": m["examples"],
                "ambiguous": m["ambiguous"],
                "ambiguous_count": m["ambiguous_count"],
                "joined": m["joined"],
            }
        )
    p = next(
        (
            x
            for x in s.scalars(sa.select(Proposal).where(Proposal.kind == "join"))
            if (x.origin or {}).get("source") == src.name
        ),
        None,
    )
    proposal = None
    if p is not None:
        tabs = p.evidence.get("tabs", [])
        proposal = {
            "id": str(p.id),
            "text": p.text,
            "status": p.status,
            "open": sum(1 for x in tabs if x["status"] == "open"),
            "tabs": [
                {
                    "key": x["key"],
                    "line": tab_line(x),
                    "status": x["status"],
                    "by_name": x["grade"] == "name",
                }
                for x in tabs
            ],
        }
    return {"rows": items, "proposal": proposal}
