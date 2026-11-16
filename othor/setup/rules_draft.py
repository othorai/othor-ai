# SPDX-License-Identifier: Apache-2.0
"""Metric drafts without a model (decided 4 Nov 2026). Deterministic, in this order:

(a) upload columns (MIS, CSV) and document text matched to source columns by normalised name,
    a small fixed dictionary of business words, and the Ontology vocabulary and aliases;
    SQL files give definitions directly (e.g. "receivables over N days" from a due-date filter);
(b) per metric-source table, the sum of an amount-like column by its date column; for event
    tables (a date, no amount), a count of rows per date;
(c) the confirmed rules already in the Ontology (filters; book of record picks the source) are
    applied to both, and named in "Where it came from".

Every draft: confidence Low, state Needs confirmation, the rule that made it cited. At most 15,
ranked: a match to a current upload column or SQL definition, then a document mention or an old
tab, then the per-table drafts; within each, by table size. Never a personal column, never an
Excluded or ignored table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from othor.connectors.classify import AUDIT, KEYISH, _stem
from othor.ontology.compiler.catalog import Catalog, TableInfo
from othor.setup.parse import Ref

MAX_DRAFTS = 15
_AMOUNT = re.compile(
    r"(amount|value|net|gross|qty|quantity|mtrs|metres|meters|price|cost|debit|"
    r"credit|paid|revenue|sales|total)"
)
_REFERENCE = re.compile(r"(^|_)(fx_)?(rates?|prices?|calendar|holidays?)$")
_SNAPSHOT_DATES = {"as_of", "as_of_date", "snapshot_date"}
_NOT_ADDITIVE = re.compile(r"(balance|rate|fx|percent|pct|ratio|score|limit|days)")
_UNIT_TEXT = re.compile(
    r"\([^)]*\)|\bincl\b\.?|\bincluding\b|\bexcl\b\.?|\bgst\b|₹|\brs\b\.?|\binr\b", re.I
)


@dataclass(frozen=True)
class Concept:
    key: str
    name: str
    words: frozenset[str]
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    unit: str = "inr"
    grain: str = "month"
    description: str = ""
    dates: tuple[str, ...] = ()  # preferred date columns


CONCEPTS = (
    Concept(
        "net_revenue",
        "Net revenue",
        frozenset({"sales", "revenue", "turnover", "invoiced"}),
        ("invoices", "fact_invoice", "invoice"),
        ("net_amount", "net_value", "amount", "value"),
        description="Invoice value by invoice date",
    ),
    Concept(
        "metres_dispatched",
        "Metres dispatched",
        frozenset({"metres", "meters", "mtrs", "dispatched", "shipped", "dispatch"}),
        ("fact_dispatch", "dispatch", "dispatches"),
        ("dispatch_mtrs", "mtrs", "metres", "qty_mtr"),
        unit="metres",
        grain="day",
        description="Metres dispatched by dispatch date",
    ),
    Concept(
        "collections",
        "Collections",
        frozenset({"collections", "collected", "receipts"}),
        ("receivables", "receipts"),
        ("paid_amount",),
        grain="week",
        description="Receipts against invoices by paid date",
        dates=("paid_date",),
    ),
    Concept(
        "orders_value",
        "Orders booked",
        frozenset({"orders", "booked", "bookings"}),
        ("fact_order", "orders"),
        ("amount", "value"),
        grain="day",
        description="Order value by order date",
    ),
)


@dataclass
class RuleDraft:
    key: str
    body: dict[str, Any]
    origin: dict[str, Any]
    refs: list[dict[str, str]] = field(default_factory=list)
    confidence: str = "low"
    state: str = "needs_confirmation"
    rank: tuple[int, int, str] = (9, 0, "")


def _key(text: str) -> str:
    """A record key: lowercase letters, digits and underscores (a sheet tab has spaces)."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60] or "metric"


def tokens(text: str) -> set[str]:
    t = _UNIT_TEXT.sub(" ", text.lower())
    return {w for w in re.split(r"[^a-z0-9]+", t) if len(w) > 2}


def _label(upload: str) -> str:
    stem = upload.rsplit(".", 1)[0]
    return re.split(r"[-_ ]", stem)[0] or stem


def _usable(t: TableInfo, sheets: bool = False) -> bool:
    """Sheets tabs are drafted only for a source connected after the first draft (its tabs
    have been read by then); the first draft reads databases in place."""
    return t.use in ("metric_source",) and (sheets or t.source_kind != "gsheets")


def _date_col(t: TableInfo, prefer: tuple[str, ...] = ()) -> str | None:
    for p in prefer:
        c = t.column(p)
        if c is not None and c.kind in ("date", "timestamp") and not c.personal:
            return c.name
    cands = [
        c
        for c in t.columns.values()
        if c.kind in ("date", "timestamp") and not c.personal and not AUDIT.search(c.name.lower())
    ]
    cands.sort(key=lambda c: (not c.name.lower().endswith("date"), c.kind != "date", c.name))
    return cands[0].name if cands else None


def _amount_cols(t: TableInfo) -> list[str]:
    out = []
    for c in t.columns.values():
        n = c.name.lower()
        if (
            c.kind in ("decimal", "float", "int")
            and not c.personal
            and not KEYISH.search(n)
            and _AMOUNT.search(n)
            and not _NOT_ADDITIVE.search(n)
        ):
            out.append(c.name)

    def rank(n: str) -> tuple[int, str]:
        n_ = n.lower()
        if re.search(r"gst|tax|tds|vat", n_):
            return 3, n
        if "net" in n_:
            return 0, n
        return (1 if re.search(r"amount|mtrs|metres", n_) else 2), n

    return sorted(out, key=rank)


def _unit_of(col: str) -> str:
    n = col.lower()
    if re.search(r"mtr|metre|meter", n):
        return "metres"
    # whole name parts: "discount_amount" is money, not a count (2o, M12b)
    if re.search(r"(^|_)(qty|quantity|count|cnt|pcs|num)(_|$)", n):
        return "count"
    return "inr"


def _table_ref(t: TableInfo) -> dict[str, str]:
    return {"source": t.source_name, "schema": t.schema, "table": t.name}


ACRONYMS = {"ar", "ap", "gst", "dso", "dpo", "po", "so", "hr", "mis", "sku", "tds", "emi", "gl"}


def _humanise(s: str) -> str:
    """'ar_open_items' → 'AR open items' (acronyms stay upper case, 2o)."""
    words = s.replace("_", " ").strip().split()
    return " ".join(w.upper() if w.lower() in ACRONYMS else w for w in words)


def _first_upper(s: str) -> str:
    """Capitalise the first letter only; .capitalize() would lower 'AR' to 'Ar'."""
    return s[:1].upper() + s[1:]


# --- plain names (M11c): "fact_order.amount" → "Order value" ---------------------------------
# Words a table name carries that aren't what it holds; words that only say "an amount"; units
_TABLE_NOISE = {
    *("fact", "fct", "dim", "tbl", "agg", "stg", "ledger", "register", "master", "data"),
    *("daily", "weekly", "monthly", "yearly"),
}
_AMOUNT_WORDS = {"amount", "amt", "value", "val", "total", "sum", "net", "gross", "inr"}
_UNIT = {
    "mtr": "Metres",
    "mtrs": "Metres",
    "metre": "Metres",
    "metres": "Metres",
    "meter": "Metres",
    "meters": "Metres",
    "kg": "Kilos",
    "kgs": "Kilos",
    "qty": "Quantity",
    "quantity": "Quantity",
}
# a transaction's noun → what it does to an amount ("Discounts approved", "Metres dispatched")
_DONE = {
    "dispatch": "dispatched",
    "return": "returned",
    "approval": "approved",
    "shipment": "shipped",
    "receipt": "received",
    "purchase": "purchased",
    "payment": "paid",
    "refund": "refunded",
}
# nouns whose amount column is their value ("Order value", "Invoice value")
_VALUED = {"order", "invoice", "quote", "booking", "purchase"}


def _singular(w: str) -> str:
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("ss") or len(w) <= 3:
        return w
    return w[:-1] if w.endswith("s") else w


def _plural(w: str) -> str:
    if w.endswith("s") or w.upper() == w:
        return w
    return w[:-1] + "ies" if w.endswith("y") and w[-2:-1] not in "aeiou" else w + "s"


def plain_name(table: str, col: str | None) -> str:
    """A plain metric name from a table and column, for a rule-drafted metric (M11c). Words
    only: the key and the source column stay as they are, and nothing about the definition or
    the numbers changes. `col` None: a count of rows."""
    tw = [w for w in table.lower().split("_") if w and w not in _TABLE_NOISE]
    tw = tw or [table.lower()]
    tw[-1] = _singular(tw[-1])
    noun = tw[-1]
    if col is None:
        return _first_upper(_humanise(" ".join([*tw[:-1], _plural(noun)]))) + " · count"
    cw = [w for w in col.lower().split("_") if w]
    units = [_UNIT[w] for w in cw if w in _UNIT]  # "qty_mtr": the measure is metres
    unit = next((u for u in units if u != "Quantity"), units[0] if units else None)
    rest = [w for w in cw if w not in _AMOUNT_WORDS and w not in _UNIT and _singular(w) not in tw]
    if unit:  # "Metres dispatched", "Metres returned"
        done = _DONE.get(noun)
        return f"{unit} {done}" if done else f"{unit} · {_humanise(' '.join(tw))}"
    if rest:  # "Discounts approved", "Bank credits"
        what = _plural(rest[-1])
        if noun in _DONE:
            return _first_upper(_humanise(" ".join([*rest[:-1], what, _DONE[noun]])))
        return _first_upper(_humanise(" ".join([*tw, *rest[:-1], what])))
    if noun in _VALUED:  # "Order value", "Invoice value"
        return _first_upper(_humanise(" ".join([*tw, "value"])))
    return _first_upper(_humanise(" ".join([*tw[:-1], _plural(noun)])))  # "Costs", "Credit notes"


def _apply_rules(d: RuleDraft, t: TableInfo, rules: list[tuple[str, dict[str, Any]]]) -> None:
    """(c): table filters carried into the draft and named."""
    used = []
    for key, r in rules:
        ref = r.get("table") or {}
        if (ref.get("source"), ref.get("schema"), ref.get("table")) != (
            t.source_name,
            t.schema,
            t.name,
        ):
            continue
        if r.get("kind") == "filter" and r.get("expr"):
            d.body.setdefault("filters", []).append(r["expr"])
            used.append(key)
        elif r.get("kind") == "unit_conversion":
            used.append(key)
    if used:
        d.origin["rule"] += " · applies " + ", ".join(sorted(used))


def _pick(
    cat: Catalog, c: Concept, mentioned: set[str], rules: list[tuple[str, dict[str, Any]]]
) -> tuple[TableInfo, str] | None:
    book = next(
        (
            r
            for _, r in rules
            if r.get("kind") == "book_of_record"
            and r.get("concept") in (c.key, c.key.split("_")[-1], "revenue")
        ),
        None,
    )
    cands = []
    for t in cat.tables.values():
        if not _usable(t) or t.name.lower() not in c.tables:
            continue
        col = next(
            (x for x in c.columns if (ci := t.column(x)) is not None and not ci.personal), None
        )
        if col is None:
            continue
        cands.append((t, t.column(col).name))  # type: ignore[union-attr]
    if not cands:
        return None

    def score(item: tuple[TableInfo, str]) -> tuple[int, int, int, str]:
        t, _ = item
        return (
            0 if book and t.source_name == book.get("source") else 1,
            0 if t.source_name.lower() in mentioned or t.schema.lower() in mentioned else 1,
            c.tables.index(t.name.lower()),
            f"{t.source_name}.{t.schema}.{t.name}",
        )

    return sorted(cands, key=score)[0]


def rule_drafts(
    cat: Catalog,
    refs: list[tuple[str, Ref]],
    *,
    rules: list[tuple[str, dict[str, Any]]],
    vocabulary: list[dict[str, Any]],
    sheets: bool = False,
) -> list[RuleDraft]:
    """refs: (upload filename, ref). rules: (key, rule body). vocabulary: vocabulary/alias
    bodies."""
    refs = sorted(refs, key=lambda x: (x[0], x[1].locator, x[1].kind, x[1].text))
    mentioned = {r.text.split(".")[0].lower() for _, r in refs if r.kind == "table"}
    extra: dict[str, set[str]] = {}
    for v in vocabulary:
        means = tokens(str(v.get("means", "")) + " " + str(v.get("text", "")))
        for w in tokens(str(v.get("term", ""))):
            extra.setdefault(w, set()).update(means)
    drafts: dict[str, RuleDraft] = {}
    used_tables: set[str] = set()

    # (a) upload columns and document text → concepts
    for c in CONCEPTS:
        words = set(c.words)
        for w, means in extra.items():
            if means & words:
                words.add(w)
        hits = sorted(
            [
                (u, r)
                for u, r in refs
                if r.kind == "column" and r.data.get("number") and tokens(r.text) & words
            ],
            key=lambda x: (bool(x[1].data.get("stale")), x[0], x[1].locator),  # current tabs first
        )
        picked = _pick(cat, c, mentioned, rules)
        if picked is None or not hits:
            continue
        t, col = picked
        date = _date_col(t, c.dates)
        if date is None:
            continue
        u, r = hits[0]
        d = RuleDraft(
            c.key,
            {
                "name": c.name,
                "description": f"{c.description}: sum of {t.name}.{col}",
                "table": _table_ref(t),
                "measure": {"agg": "sum", "expr": {"col": col}},
                "date": date,
                "grain": c.grain,
                "unit": c.unit,
                "kind": "flow",
                "status": "active",
            },
            {
                "by": "rules",
                "rule": f"Rule: amount column by {date} · matched {_label(u)} column '{r.text}'",
            },
            [{"upload": u, "locator": r.locator, "text": r.text}],
            rank=(1 if r.data.get("stale") else 0, -(t_rows(t)), c.key),
        )
        _cite_text(d, refs, (col, t.name))
        _apply_rules(d, t, rules)
        drafts[c.key] = d
        used_tables.add(f"{t.source_name}.{t.schema}.{t.name}")

    # (a) SQL definitions: "over N days" on a due date
    for u, r in refs:
        m = re.search(r"due_date\)?\s*>\s*(\d+)", r.text) if r.kind == "filter" else None
        if not m:
            continue
        n = int(m.group(1))
        tbl = next((x for _, x in refs if x.kind == "table" and "receivable" in x.text), None)
        if tbl is None:
            continue
        found = cat.by_name(tbl.data.get("schema") or "", tbl.data.get("table") or "") or next(
            (x for x in cat.tables.values() if x.name == tbl.data.get("table") and _usable(x)), None
        )
        if found is None or not _usable(found) or found.column("due_date") is None:
            continue
        key = f"receivables_over_{n}"
        if key in drafts:
            continue
        drafts[key] = _receivables_over(found, n, u, r, tbl)
        _apply_rules(drafts[key], found, rules)
        used_tables.add(f"{found.source_name}.{found.schema}.{found.name}")

    # concepts mentioned only in document text (the data dictionary names dispatch_mtrs)
    for c in CONCEPTS:
        if c.key in drafts:
            continue
        picked = _pick(cat, c, mentioned, rules)
        if picked is None:
            continue
        t, col = picked
        pages = [(u, r) for u, r in refs if r.kind == "page" and col.lower() in r.text.lower()]
        date = _date_col(t, c.dates)
        if not pages or date is None:
            continue
        u, r = pages[0]
        d = RuleDraft(
            c.key,
            {
                "name": c.name,
                "description": f"{c.description}: sum of {t.name}.{col}",
                "table": _table_ref(t),
                "measure": {"agg": "sum", "expr": {"col": col}},
                "date": date,
                "grain": c.grain,
                "unit": c.unit,
                "kind": "flow",
                "status": "active",
            },
            {"by": "rules", "rule": f"Rule: amount column by {date} · named in {u} · {r.locator}"},
            [{"upload": u, "locator": r.locator, "text": _snippet(r.text, col)}],
            rank=(1, -t_rows(t), c.key),
        )
        _apply_rules(d, t, rules)
        drafts[c.key] = d
        used_tables.add(f"{t.source_name}.{t.schema}.{t.name}")

    # (b) every other metric-source table: its amount column by date, or a count of rows
    used_stems = {_stem(n.rsplit(".", 1)[-1]) for n in used_tables}
    for t in sorted(
        cat.tables.values(), key=lambda x: (-t_rows(x), x.source_name, x.schema, x.name)
    ):
        name = f"{t.source_name}.{t.schema}.{t.name}"
        if not _usable(t, sheets) or name in used_tables or _REFERENCE.search(t.name.lower()):
            continue
        date = _date_col(t)
        if date is None or date.lower() in _SNAPSHOT_DATES:
            continue  # a flow needs a business date; an as-of date is a snapshot stamp
        stem = _stem(t.name)
        if stem in used_stems:
            continue  # the same fact from another source: the book of record's table is drafted
        used_stems.add(stem)
        cols = _amount_cols(t)
        if cols:
            col = cols[0]
            key = _key(f"{stem}_{col}")
            body = {
                "name": plain_name(t.name, col),
                "description": f"Sum of {t.name}.{col} by {date}",
                "measure": {"agg": "sum", "expr": {"col": col}},
                "unit": _unit_of(col),
            }
            rule = f"Rule: amount column by {date}"
        else:
            key = _key(f"{stem}_count")
            body = {
                "name": plain_name(t.name, None),
                "description": f"Rows of {t.name} per {date}",
                "measure": {"agg": "count"},
                "unit": "count",
            }
            rule = f"Rule: count of rows by {date}"
        if key in drafts:
            continue
        d = RuleDraft(
            key,
            {
                **body,
                "table": _table_ref(t),
                "date": date,
                "grain": "month",
                "kind": "flow",
                "status": "active",
            },
            {"by": "rules", "rule": rule},
            [],
            rank=(2, -t_rows(t), key),
        )
        _cite_text(d, refs, (cols[0] if cols else t.name, t.name))
        _apply_rules(d, t, rules)
        drafts[key] = d

    ranked = sorted(drafts.values(), key=lambda d: d.rank)
    return ranked[:MAX_DRAFTS]


def t_rows(t: TableInfo) -> int:
    return int(t.row_count or 0)


def _snippet(text: str, needle: str) -> str:
    i = text.lower().find(needle.lower())
    if i < 0:
        return text[:120]
    a = text.rfind("\n", 0, i) + 1
    b = text.find("\n", i)
    return text[a : b if b > 0 else None].strip()[:200]


def _cite_text(d: RuleDraft, refs: list[tuple[str, Ref]], needles: tuple[str, str]) -> None:
    """Add a citation where a document names the column (a data dictionary page)."""
    for u, r in refs:
        if r.kind == "page" and needles[0].lower() in r.text.lower():
            d.refs.append({"upload": u, "locator": r.locator, "text": _snippet(r.text, needles[0])})
            return


def _receivables_over(t: TableInfo, n: int, u: str, r: Ref, tbl: Ref) -> RuleDraft:
    as_of = {"param": "as_of"}
    has = {c.lower() for c in t.columns}
    if {"paid_date", "amount", "paid_amount"} <= has:
        measure = {
            "case": [
                [
                    {"op": "is_null", "args": [{"col": "paid_date"}]},
                    {"op": "-", "args": [{"col": "amount"}, {"col": "paid_amount"}]},
                ]
            ],
            "else": {"col": "amount"},
        }
        open_filters: list[dict[str, Any]] = [
            {
                "op": "or",
                "args": [
                    {"op": "is_null", "args": [{"col": "paid_date"}]},
                    {"op": ">", "args": [{"col": "paid_date"}, as_of]},
                ],
            }
        ]
    else:
        measure = {"col": "amount"}
        open_filters = []
    if "invoice_date" in has:
        open_filters.insert(0, {"op": "<=", "args": [{"col": "invoice_date"}, as_of]})
    filters = [
        *open_filters,
        {
            "op": ">",
            "args": [{"fn": "days_between", "args": [as_of, {"col": "due_date"}]}, {"lit": n}],
        },
    ]
    return RuleDraft(
        f"receivables_over_{n}",
        {
            "name": f"Receivables over {n} days",
            "description": f"Open amounts more than {n} days past the due date, as of the day",
            "table": _table_ref(t),
            "measure": {"agg": "sum", "expr": measure},
            "filters": filters,
            "kind": "snapshot",
            "grain": "day",
            "unit": "inr",
            "status": "active",
        },
        {"by": "rules", "rule": f"Rule: {u} · {r.locator} (due date over {n} days)"},
        [
            {"upload": u, "locator": r.locator, "text": r.text},
            {"upload": u, "locator": tbl.locator, "text": tbl.text},
        ],
        rank=(0, -t_rows(t), f"receivables_over_{n}"),
    )
