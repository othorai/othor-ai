# SPDX-License-Identifier: Apache-2.0
"""Default table use and personal-column detection (docs/05 "Scan").

Rules only, from names, types and statistics. Values are never read to decide anything:
a personal column is found by its name, and a table's use by its name, its column names and
types, its row count and the newest business date the statistics query returned.

The AI may later label what a table "looks like"; it never decides use or personal data.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

Use = Literal["metric_source", "dimension", "ignored", "excluded"]
# Scan reasons for Ignored (2k); the user's own reasons are in USER_REASONS.
ScanReason = Literal["empty", "backup", "staging", "log", "old", "not_needed"]
SCAN_REASONS: dict[str, str] = {
    "empty": "Empty",
    "backup": "Backup",
    "staging": "Staging copy",
    "log": "Log",
    "old": "Old data",
    "not_needed": "Not needed",
}
USER_REASONS: dict[str, str] = {
    "not_needed": "Not needed",
    "duplicate": "Duplicate of another table",
    "test_old": "Test or old data",
}

NUMERIC = frozenset({"int", "decimal", "float"})
TEMPORAL = frozenset({"date", "timestamp"})

# Audit columns say when a row was written, not when the business event happened.
AUDIT = re.compile(
    r"^(created|updated|modified|inserted|loaded|deleted|synced|changed)(_at|_on|_date|_ts|_time)?$"
    r"|^(last_)?(modified|updated)$|^etl_|^_"
)
KEYISH = re.compile(r"(^id$|_id$|_key$|_no$|_code$|_num$|^line_no$|_seq$)")

# --- personal columns -------------------------------------------------------------------
# Name-based only. Each pattern matches whole underscore-separated words.
_PERSONAL: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"(^|_)e?_?mail(_id|_address)?($|_)|(^|_)email")),
    ("phone", re.compile(r"(^|_)(phone|mobile|mob|telephone|tel|whatsapp)(_no|_number)?($|_)")),
    ("pan", re.compile(r"(^|_)pan(_no|_number|_card)?$")),
    ("aadhaar", re.compile(r"(^|_)(aadhaar|aadhar|uid)(_no|_number)?$")),
    ("bank_account", re.compile(r"(^|_)(bank_)?(account|acct|ac)_(no|number|num)$|^bank_account")),
    ("ifsc", re.compile(r"(^|_)ifsc(_code)?$")),
    ("address", re.compile(r"(^|_)(address|addr|street)(_line)?(_?\d)?$|^(home|residential)_")),
    ("dob", re.compile(r"(^|_)(dob|date_of_birth|birth_date|birthday)$")),
    ("salary", re.compile(r"(^|_)(salary|ctc|wage|wages|basic_pay|gross_pay)$")),
    (
        "name",
        re.compile(
            r"^(full|first|last|middle|given|family|person|contact|employee|emp|staff|"
            r"father|mother|spouse|nominee)_?name$"
        ),
    ),
]
# A bare `name` is personal only in a table about people (a customer's name is a company).
_BARE_NAME = re.compile(r"^(name|names)$")
_PEOPLE_TABLE = re.compile(
    r"(^|_)(employee|employees|emp|staff|contact|contacts|person|persons|people|"
    r"user|users|member|members|payroll|hr)($|_)"
)


def is_people_table(name: str) -> bool:
    return bool(_PEOPLE_TABLE.search(name.lower()))


def personal_kind(column: str, *, people_table: bool) -> str | None:
    c = column.strip().lower().replace(" ", "_").replace("-", "_")
    if _BARE_NAME.match(c):
        return "name" if people_table else None
    for kind, pat in _PERSONAL:
        if pat.search(c):
            return kind
    return None


# --- table use --------------------------------------------------------------------------
_BACKUP = re.compile(r"(_bak|_backup|_copy|_old|_archive)(_|$)|^(bak|backup|copy)_")
_TEMP = re.compile(r"^(tmp|temp|scratch)(_|$)|_tmp$")
_STAGING = re.compile(r"^(stg|stage|staging)_|_(stg|staging)$")
_LOG = re.compile(r"(^|_)(log|logs|audit|errors|history_log)$|^(dq|log|audit)_|_log_")
_OLD = re.compile(r"^old_|_old_")
_YEAR = re.compile(r"(?:^|_)((?:19|20)\d\d)(?:$|_)")
_DIM = re.compile(r"^(dim|d|lkp|lookup|ref|master)_|_(master|lookup|types?|codes?)$")
_FACT = re.compile(r"^(fact|f|fct|agg)_")
_LINES = re.compile(r"_(line|lines|items|details)$")
_LABEL = re.compile(r"(name|desc|description|label|title|category|segment|region|family)$")
SMALL_LIST = 100  # rows; a short list with codes or labels is a breakdown
OLD_AFTER_DAYS = 730  # newest business date older than this → Old data


@dataclass(frozen=True)
class Col:
    name: str
    kind: str  # int · decimal · float · text · date · timestamp · bool · other

    @property
    def is_audit(self) -> bool:
        return bool(AUDIT.search(self.name.lower()))

    @property
    def is_measure(self) -> bool:
        return self.kind in NUMERIC and not KEYISH.search(self.name.lower())

    @property
    def is_business_date(self) -> bool:
        return self.kind in TEMPORAL and not self.is_audit


@dataclass(frozen=True)
class TableFacts:
    schema: str
    name: str
    columns: tuple[Col, ...]
    row_count: int
    newest_date: dt.date | None = None  # newest non-audit date, from the statistics query


@dataclass(frozen=True)
class Sorted:
    use: Use
    reason: str | None = None
    personal: tuple[tuple[str, str], ...] = field(default=())  # (column, kind)


_BARE_CONTACT = re.compile(r"^(phone|mobile|email|e_mail|email_id|phone_no|mobile_no)$")


def personal_columns(t: TableFacts) -> tuple[tuple[str, str], ...]:
    # A bare `name` beside a bare phone or email is a person's name; in a customers table with
    # `contact_email` it is the company's name.
    people = is_people_table(t.name) or any(
        _BARE_CONTACT.match(c.name.strip().lower()) for c in t.columns
    )
    found = []
    for c in t.columns:
        k = personal_kind(c.name, people_table=people)
        if k:
            found.append((c.name, k))
    return tuple(found)


def classify(t: TableFacts, today: dt.date) -> Sorted:
    """Default use. Excluded is set only here (only the scan can exclude)."""
    name = t.name.lower()
    personal = personal_columns(t)
    kinds = {k for _, k in personal}
    non_key = [c for c in t.columns if not KEYISH.search(c.name.lower())]
    mostly_personal = bool(non_key) and len(personal) * 2 >= len(non_key)
    if personal and (is_people_table(name) or len(kinds) >= 3 or mostly_personal):
        return Sorted("excluded", None, personal)

    def ignored(reason: str) -> Sorted:
        return Sorted("ignored", reason, personal)

    if t.row_count == 0:
        return ignored("empty")
    if _BACKUP.search(name) or _TEMP.search(name):
        return ignored("backup")
    if _STAGING.search(name):
        return ignored("staging")
    if _LOG.search(name):
        return ignored("log")
    year = _YEAR.search(name)
    if _OLD.search(name) or (year and int(year.group(1)) <= today.year - 2):
        return ignored("old")
    if t.newest_date and (today - t.newest_date).days > OLD_AFTER_DAYS:
        return ignored("old")

    measures = [c for c in t.columns if c.is_measure]
    dated = any(c.is_business_date for c in t.columns)
    labels = [
        c
        for c in t.columns
        if c.kind == "text"
        and (_LABEL.search(c.name.lower()) or c.name.lower() in ("code", "label"))
    ]
    if _DIM.search(name):
        return Sorted("dimension", None, personal)
    if _FACT.search(name) and measures:
        return Sorted("metric_source", None, personal)
    if t.row_count < SMALL_LIST and labels:
        return Sorted("dimension", None, personal)
    if measures and (dated or _LINES.search(name)):
        return Sorted("metric_source", None, personal)
    if labels or any(c.kind == "text" and KEYISH.search(c.name.lower()) for c in t.columns):
        return Sorted("dimension", None, personal)
    return ignored("not_needed")


def stable_key(schema: str, table: str) -> str:
    """Stable across scans and renames of nothing else: the table's qualified name."""
    return hashlib.sha256(f"{schema}.{table}".encode()).hexdigest()[:16]


def schema_fingerprint(tables: list[TableFacts]) -> str:
    """Changes when a table or column is added, removed, renamed or retyped."""
    h = hashlib.sha256()
    for t in sorted(tables, key=lambda x: (x.schema, x.name)):
        h.update(f"{t.schema}.{t.name}(".encode())
        for c in t.columns:
            h.update(f"{c.name}:{c.kind},".encode())
        h.update(b")")
    return h.hexdigest()[:12]


# --- "Looks like" (rules) ---------------------------------------------------------------
# The fallback label under the AI one (docs/06): built from names only.
_STRIP_PREFIX = re.compile(r"^(fact|dim|f|d|fct|agg|stg|tmp|lkp|ref)_")
_NOTABLE = re.compile(
    r"(amount|value|net|gross|total|qty|quantity|mtrs|metres|meters|rate|price|cost|balance|"
    r"debit|credit|customer|region|item|plant|segment|category|family|owner|stage|status|"
    r"uom|reason|currency|date)"
)
PERSONAL_LABEL = {
    "name": "Names",
    "phone": "phones",
    "email": "emails",
    "pan": "PAN",
    "aadhaar": "Aadhaar",
    "bank_account": "bank account",
    "ifsc": "IFSC",
    "address": "addresses",
    "dob": "dates of birth",
    "salary": "salaries",
}


def _stem(name: str) -> str:
    """fact_orders, orders, order → order (to find what a backup or staging table copies)."""
    n = _STRIP_PREFIX.sub("", name.lower())
    return n[:-1] if n.endswith("s") else n


def _words(name: str) -> str:
    return name.replace("_", " ").strip()


def _column_word(col: str) -> str:
    c = col.lower()
    for word, pat in (
        ("amount", r"amount|net_value|^value$|gross|_value$"),
        ("qty", r"qty|quantity"),
        ("metres", r"mtrs|metres|meters"),
        ("date", r"date$|_on$"),
        ("customer", r"^customer"),
        ("region", r"region"),
        ("item", r"^item"),
        ("rate", r"rate$"),
    ):
        if re.search(pat, c):
            return word
    c = re.sub(r"_(id|key|code|no|name|desc)$", "", c)
    return _words(c)


def personal_label(personal: tuple[tuple[str, str], ...]) -> str:
    kinds = list(dict.fromkeys(k for _, k in personal))
    words = [PERSONAL_LABEL.get(k, k) for k in kinds]
    text = ", ".join(words)
    return text[:1].upper() + text[1:]


ACRONYMS = {
    "ar",
    "ap",
    "gst",
    "tds",
    "grn",
    "crm",
    "erp",
    "sku",
    "po",
    "fx",
    "hr",
    "mis",
    "gl",
    "id",
    "kpi",
    "dso",
}


def sentence(words: str) -> str:
    """'ar open items' → 'AR open items': first letter up, known acronyms in capitals."""
    out = [w.upper() if w in ACRONYMS else w for w in words.split(" ")]
    if out and out[0] and not out[0].isupper():
        out[0] = out[0][:1].upper() + out[0][1:]
    return " ".join(out)


def looks_like(t: TableFacts, s: Sorted, base_tables: set[str] | None = None) -> str:
    """'Invoices · amount, customer, region, date', 'Backup of sales_dw.fact_invoice', …"""
    if s.use == "excluded":
        return personal_label(s.personal)
    if s.use == "ignored":
        if s.reason == "empty":
            return "No rows"
        if s.reason in ("backup", "staging"):
            base = re.sub(
                r"(_bak|_backup|_copy|_old)(_\d{4})?$|^(stg|stage|staging|tmp)_", "", t.name.lower()
            )
            # same schema first, then the rest of the source
            for cand in sorted(
                base_tables or (), key=lambda c: (not c.startswith(t.schema + "."), c)
            ):
                short = cand.split(".")[-1]
                if short != t.name.lower() and _stem(short) == _stem(base):
                    kind = "Backup" if s.reason == "backup" else "Copy"
                    return f"{kind} of {cand}"
            return "Backup or temporary copy" if s.reason == "backup" else "Staging copy"
        if s.reason == "log":
            return "Log · timestamps and events"
        if s.reason == "old":
            return f"Old data · up to {t.newest_date:%b %Y}" if t.newest_date else "Old data"
    noun = sentence(_words(_STRIP_PREFIX.sub("", t.name.lower())))
    personal = {p for p, _ in s.personal}
    words: list[str] = []
    for c in t.columns:
        if c.is_audit or c.name in personal or not _NOTABLE.search(c.name.lower()):
            continue
        w = _column_word(c.name)
        if w not in words:
            words.append(w)
    # amounts first, then what to split by, then the date (as on the scan boards)
    words.sort(key=lambda w: 0 if w in _MEASURE_WORDS else 2 if w == "date" else 1)
    picked = words[:4] if "date" not in words[4:] else [*words[:3], "date"]
    return f"{noun} · {', '.join(picked)}" if picked else noun


_MEASURE_WORDS = {
    "amount",
    "qty",
    "metres",
    "rate",
    "price",
    "cost",
    "balance",
    "debit",
    "credit",
    "total",
    "value",
}
