# SPDX-License-Identifier: Apache-2.0
"""What we found in your data (board 2n): Overlap, Currency, Dimension, Pattern, Gap.

Rules only: each finding is a scan statistic or an aggregate query run read-only on the source,
and each carries the Ontology rule it proposes (applied at Confirm). The sentence is built from
the computed values; no model writes it.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.connectors.classify import _stem
from othor.connectors.models import Source, SourceColumn, SourceTable
from othor.connectors.sql.query_log import QueryLog
from othor.core import fmt
from othor.ontology.compiler.catalog import Catalog, TableInfo

log = logging.getLogger(__name__)


@dataclass
class Discovery:
    type: str  # overlap currency dimension pattern gap
    key: str
    text: str
    evidence: dict[str, Any] = field(default_factory=dict)
    rules: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)  # (type, key, body)


def _q(dialect: str, name: str) -> str:
    return f"`{name}`" if dialect == "mysql" else f'"{name}"'


def _tbl(t: TableInfo) -> str:
    return f"{_q(t.dialect, t.schema)}.{_q(t.dialect, t.name)}"


def _ref(t: TableInfo) -> dict[str, str]:
    return {"source": t.source_name, "schema": t.schema, "table": t.name}


def _date_range(s: Session, t: TableInfo, col: str) -> tuple[dt.date, dt.date] | None:
    row = s.scalars(
        sa.select(SourceColumn)
        .join(SourceTable, SourceTable.id == SourceColumn.table_id)
        .join(Source, Source.id == SourceTable.source_id)
        .where(
            Source.name == t.source_name,
            SourceTable.schema == t.schema,
            SourceTable.name == t.name,
            SourceColumn.name == col,
        )
    ).first()
    st = (row.stats or {}) if row else {}
    if not st.get("min") or not st.get("max"):
        return None
    return dt.date.fromisoformat(str(st["min"])[:10]), dt.date.fromisoformat(str(st["max"])[:10])


def _month(d: dt.date) -> str:
    return f"{d:%b} {d.year}"


def overlap_and_gap(s: Session, cat: Catalog, mentioned: set[str]) -> list[Discovery]:
    """The same fact in two sources with overlapping dates → book of record; the longer
    history's first date → a gap before it."""
    out: list[Discovery] = []
    groups: dict[str, list[TableInfo]] = {}
    for t in cat.tables.values():
        if t.use == "metric_source" and t.source_kind != "gsheets":
            groups.setdefault(_stem(t.name), []).append(t)
    for stem in sorted(groups):
        ts = groups[stem]
        if len({t.source_name for t in ts}) < 2:
            continue
        ranges: dict[int, tuple[dt.date, dt.date]] = {}
        for i, t in enumerate(ts):
            col = next(
                (
                    c.name
                    for c in t.columns.values()
                    if c.name.lower().endswith("_date") and c.kind in ("date", "timestamp")
                ),
                None,
            )
            r = _date_range(s, t, col) if col else None
            if r:
                ranges[i] = r
        if len(ranges) < 2:
            continue
        ia, ib = sorted(ranges, key=lambda i: (ranges[i][0], ts[i].source_name))[:2]
        a, b = ts[ia], ts[ib]
        start = max(ranges[ia][0], ranges[ib][0])
        ibook = min(
            ranges, key=lambda i: (ts[i].source_name not in mentioned, -ranges[i][0].toordinal())
        )
        book = ts[ibook]
        other = a if ibook == ib else b
        noun = stem.replace("_", " ") + "s"
        out.append(
            Discovery(
                "overlap",
                f"overlap_{stem}",
                f"{noun.capitalize()} are in both {book.source_name} and {other.source_name} from "
                f"{_month(start)}. {book.source_name} is used as the book of record.",
                {
                    "tables": [f"{t.source_name}.{t.schema}.{t.name}" for t in (a, b)],
                    "from": start.isoformat(),
                },
                [
                    (
                        "rule",
                        f"{stem}_book_of_record",
                        {
                            "kind": "book_of_record",
                            "concept": "revenue" if stem == "invoice" else stem,
                            "source": book.source_name,
                            "over": [other.source_name],
                            "text": f"Where {book.source_name} and {other.source_name} overlap, "
                            f"{book.source_name} wins",
                        },
                    )
                ],
            )
        )
        ilong = min(ranges, key=lambda i: ranges[i][0])
        longest, first = ts[ilong], ranges[ilong][0]
        out.append(
            Discovery(
                "gap",
                f"gap_{longest.source_name}_{longest.name}",
                f"{longest.schema}.{longest.name} has no rows before {_month(first)}.",
                {
                    "table": f"{longest.source_name}.{longest.schema}.{longest.name}",
                    "first": first.isoformat(),
                },
                [
                    (
                        "business_fact",
                        f"history_{longest.name}",
                        {
                            "kind": "other",
                            "text": f"{longest.schema}.{longest.name} starts {_month(first)}",
                        },
                    )
                ],
            )
        )
    return out


def currency(cat: Catalog, run: Any) -> list[Discovery]:
    """A non-INR share of an amount column, with the rate column used to convert it."""
    out = []
    for t in sorted(cat.tables.values(), key=lambda x: (x.source_name, x.name)):
        if t.use != "metric_source" or t.column("currency") is None or t.source_kind == "gsheets":
            continue
        amount = next((n for n in ("net_amount", "net_value", "amount") if t.column(n)), None)
        rate = next((n for n in ("fx_rate", "exchange_rate", "rate") if t.column(n)), None)
        date = next((c.name for c in t.columns.values() if c.name.endswith("_date")), None)
        if amount is None:
            continue
        rows = run(
            t,
            f"SELECT {_q(t.dialect, 'currency')}, SUM({_q(t.dialect, amount)}) "  # noqa: S608
            f"FROM {_tbl(t)} GROUP BY {_q(t.dialect, 'currency')}",
        )
        total = sum(Decimal(str(v or 0)) for _, v in rows)
        foreign = [(c, Decimal(str(v or 0))) for c, v in rows if c and str(c).upper() != "INR"]
        if not total or not foreign:
            continue
        cur, val = max(foreign, key=lambda x: x[1])
        share = val / total
        conv = f" Converted at the {date.replace('_', '-')} rate." if rate and date else ""
        out.append(
            Discovery(
                "currency",
                f"currency_{t.name}",
                f"Invoices billed in {cur} are {fmt.pct(share)} of revenue.{conv}",
                {
                    "table": f"{t.source_name}.{t.schema}.{t.name}",
                    "currency": cur,
                    "share": str(share),
                },
                [
                    (
                        "rule",
                        f"{t.name}_currency",
                        {
                            "kind": "currency",
                            "table": _ref(t),
                            "column": amount,
                            "rate_column": rate or amount,
                            "text": f"{cur} amounts converted at the row's rate",
                        },
                    )
                ]
                if rate
                else [],
            )
        )
    # one finding: the table with the rate column (else the largest share)
    out.sort(key=lambda d: (not d.rules, -Decimal(d.evidence["share"])))
    return out[:1]


def dimension(cat: Catalog, run: Any) -> list[Discovery]:
    """Code prefixes that map to a dimension (item code SU- → Surat), from a table that has both."""
    out = []
    for t in sorted(cat.tables.values(), key=lambda x: (x.source_name, x.name)):
        code = next(
            (c.name for c in t.columns.values() if c.name.lower().endswith("item_code")), None
        )
        if not code or t.column("plant") is None or t.source_kind == "gsheets":
            continue
        rows = run(
            t,
            f"SELECT SUBSTRING({_q(t.dialect, code)}, 1, 3), {_q(t.dialect, 'plant')}, "  # noqa: S608
            f"COUNT(*) FROM {_tbl(t)} GROUP BY 1, 2",
        )
        best: dict[str, tuple[str, int]] = {}
        for prefix, plant, n in rows:
            ok = prefix and plant and re.fullmatch(r"[A-Z]{2}-", str(prefix))
            if ok and n > best.get(prefix, ("", -1))[1]:
                best[str(prefix)] = (str(plant), int(n))
        if len(best) < 2:
            continue
        entries = {p: v[0] for p, v in sorted(best.items())}
        prefixes = " and ".join(sorted(entries))
        plants = " and ".join(entries[p] for p in sorted(entries))
        joins = []
        for other in sorted(cat.tables.values(), key=lambda x: (x.source_name, x.name)):
            if (
                other.use == "metric_source"
                and other.column(code)
                and other.column("plant") is None
            ):
                joins.append(
                    (
                        "rule",
                        f"plant_from_{code}_{other.name}",
                        {
                            "kind": "join",
                            "table": _ref(other),
                            "column": code,
                            "vocabulary": "plant_codes",
                            "text": "Plant from the item code prefix",
                        },
                    )
                )
        out.append(
            Discovery(
                "dimension",
                "plant_codes",
                f"Item codes starting {prefixes} map to the {plants} plants.",
                {"table": f"{t.source_name}.{t.schema}.{t.name}", "entries": entries},
                [
                    (
                        "vocabulary",
                        "plant_codes",
                        {
                            "term": "Plants",
                            "kind": "prefix_map",
                            "column": code,
                            "entries": entries,
                        },
                    ),
                    (
                        "breakdown",
                        "plant",
                        {"name": "Plant", "column": "plant", "vocabulary": "plant_codes"},
                    ),
                    *joins,
                ],
            )
        )
        break
    return out


# --- splits: the ways numbers can be split, from names alone (12 Nov 2026) ----------------------
# Indigo Thread on production confirmed 16 numbers with no breakdown: 10 of 11 Sweep checks
# couldn't run and the first brief was quiet. A breakdown was only ever proposed from documents
# (by the model) or the item-code rule. This finds them from the scan: label columns on the
# tables metrics read, or one join away on a dimension table they share a key with. Column names
# and table uses only; never row values, never personal columns.

LABELS: dict[str, tuple[str, tuple[str, ...]]] = {
    "region": ("Region", ("region", "sales_region", "zone", "territory")),
    "segment": ("Segment", ("segment", "customer_segment", "market_segment")),
    "channel": ("Channel", ("channel", "sales_channel")),
    "product_family": ("Product family", ("family", "product_family", "category", "item_group")),
}
# an entity's name column, on a dimension table whose name is that entity
ENTITIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "customer": ("Customer", ("name", "customer_name")),
    "supplier": ("Supplier", ("name", "supplier_name", "vendor_name")),
}
KEY_SUFFIXES = ("_id", "_key", "_code")


def _entity_of(t: TableInfo) -> str:
    stem = _stem(t.name)
    return "supplier" if stem in ("vendor", "supplier") else stem


def _joins(cat: Catalog) -> dict[tuple[str, str], tuple[TableInfo, TableInfo, str]]:
    """(fact stable key, dimension name) → (fact, dimension, shared key): a metric table and a
    dimension table of the same source that share a key column named after the dimension."""
    tables = sorted(cat.tables.values(), key=lambda x: (x.source_name, x.schema, x.name))
    facts = [t for t in tables if t.use == "metric_source" and t.source_kind != "gsheets"]
    dims = [t for t in tables if t.use == "dimension" and t.source_kind != "gsheets"]
    out: dict[tuple[str, str], tuple[TableInfo, TableInfo, str]] = {}
    for f in facts:
        for d in dims:
            if d.source_name != f.source_name or d.schema != f.schema:
                continue
            ent = _entity_of(d)
            for c in sorted(f.columns.values(), key=lambda x: x.name):
                n = c.name.lower()
                if c.personal or not n.endswith(KEY_SUFFIXES):
                    continue
                if n.rsplit("_", 1)[0] != ent or d.column(c.name) is None:
                    continue
                out.setdefault((f.stable_key, d.name), (f, d, c.name))
                break
    return out


def splits(cat: Catalog, known: set[str] | None = None) -> list[Discovery]:
    """One finding: the breakdowns the scan offers and the joins they need (rules, applied at
    Confirm unless skipped). Breakdowns the Ontology already has are left alone; plant is the
    item-code rule's."""
    known = set(known or ()) | {"plant"}
    joins = _joins(cat)
    # where each breakdown can be read: (table, column), the metric tables' own first
    places: dict[str, list[tuple[str, str]]] = {}
    used_joins: set[tuple[str, str]] = set()
    facts = sorted(
        (t for t in cat.tables.values() if t.use == "metric_source" and t.source_kind != "gsheets"),
        key=lambda x: (x.source_name, x.schema, x.name),
    )
    for f in facts:
        for key, (_name, names) in LABELS.items():
            col = next(
                (c.name for c in f.columns.values() if c.name.lower() in names and not c.personal),
                None,
            )
            if col and (f.name, col) not in places.get(key, []):
                places.setdefault(key, []).append((f.name, col))
    for (fkey, dname), (_f, d, _k) in sorted(joins.items(), key=lambda x: x[0]):
        for key, (_name, names) in LABELS.items():
            col = next(
                (c.name for c in d.columns.values() if c.name.lower() in names and not c.personal),
                None,
            )
            if col:
                if (d.name, col) not in places.get(key, []):
                    places.setdefault(key, []).append((d.name, col))
                used_joins.add((fkey, dname))
        ent = _entity_of(d)
        if ent in ENTITIES:
            col = next(
                (
                    c.name
                    for c in d.columns.values()
                    if c.name.lower() in ENTITIES[ent][1] and not c.personal
                ),
                None,
            )
            if col:
                if (d.name, col) not in places.get(ent, []):
                    places.setdefault(ent, []).append((d.name, col))
                used_joins.add((fkey, dname))
    label_of = {**{k: v[0] for k, v in LABELS.items()}, **{k: v[0] for k, v in ENTITIES.items()}}
    records: list[tuple[str, str, dict[str, Any]]] = []
    found: list[str] = []
    for key in sorted(places, key=lambda k: list(label_of).index(k)):
        if key in known:
            continue
        (table, column), *rest = places[key]
        body: dict[str, Any] = {"name": label_of[key], "column": column, "table": table}
        if rest:
            body["alternatives"] = [{"table": t, "column": c} for t, c in rest]
        records.append(("breakdown", key, body))
        found.append(label_of[key])
    if not records:
        return []
    for fkey, dname in sorted(used_joins):
        f, d, k = joins[(fkey, dname)]
        records.append(
            (
                "rule",
                f"join_{f.name}_{d.name}",
                {"kind": "join", "table": _ref(f), "to": _ref(d), "on": [[k, k]]},
            )
        )
    listed = ", ".join(found[:-1]) + (" and " if len(found) > 1 else "") + found[-1]
    return [
        Discovery(
            "dimension",
            "splits",
            f"Your numbers can be split by {listed}.",
            {"breakdowns": found, "joins": len(used_joins)},
            records,
        )
    ]


def propose_splits(s: Session, company: Any) -> Any:
    """For a company already set up: the splits its numbers miss, as one Home proposal ("Split
    your numbers by …"), each metric given the breakdowns it can reach. Nothing changes until the
    owner accepts it. Inside the company's tenant scope; None when there is nothing to propose or
    it was proposed before (declined proposals are not repeated)."""
    from othor.core.events import SYSTEM_ACTOR, record_event
    from othor.engine.models import Proposal
    from othor.ontology import service
    from othor.ontology.compiler.api import company_settings
    from othor.ontology.compiler.catalog import load_catalog
    from othor.setup.compute import attach_breakdowns

    if not service.confirmed(s):
        return None
    cat = load_catalog(s)
    cur = {(r.type, r.key): r.body for r in service.current(s)}
    found = splits(cat, known={k for (t, k) in cur if t == "breakdown"})
    if not found:
        return None
    recs = {(t, k): b for t, k, b in found[0].rules}
    metrics = {tk: b for tk, b in cur.items() if tk[0] == "metric"}
    base = {tk: b for tk, b in cur.items() if tk[0] != "metric"}
    got = attach_breakdowns(base, {**recs, **metrics}, cat, company_settings(company))
    if not got:
        return None
    used = {b for bks in got.values() for b in bks}
    keep: list[tuple[str, str, dict[str, Any]]] = [
        (t, k, b) for (t, k), b in sorted(recs.items()) if t != "breakdown" or k in used
    ]
    names = [str(b["name"]) for t, _k, b in keep if t == "breakdown"]
    fp = "breakdowns:" + ",".join(sorted(k for t, k, _ in keep if t == "breakdown"))
    if s.scalars(sa.select(Proposal.id).where(Proposal.fingerprint == fp)).first():
        return None
    listed = ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1]
    n = len(got)
    p = Proposal(
        kind="breakdowns",
        text=f"Split your numbers by {listed}",
        evidence={
            "records": [[t, k, b] for t, k, b in keep],
            "metrics": got,
            "line": f"{fmt.num(n)} number{'s' if n != 1 else ''} get breakdowns, so Sweep can "
            "test them by each",
            "confidence": "high",
        },
        origin={"rule": "splits_from_scan"},
        fingerprint=fp,
        status="open",
        run_id=None,
    )
    s.add(p)
    s.flush()
    record_event(
        s,
        actor=SYSTEM_ACTOR,
        action="proposal.created",
        object_type="proposal",
        object_id=p.id,
        after={"kind": "breakdowns", "fingerprint": fp},
    )
    return p


def offer_splits(s: Session, company_id: Any) -> Any:
    """Propose "Split your numbers by …" on its own after a scan finishes or metrics are added
    (v0.11). Best effort in a savepoint: a failure is logged without values and never fails the
    scan or the Add; the proposal still waits for the owner's Accept."""
    from othor.core.models import Company

    company = s.scalars(sa.select(Company).where(Company.id == company_id)).first()
    if company is None:
        return None
    try:
        with s.begin_nested():
            return propose_splits(s, company)
    except Exception as e:
        log.warning("splits proposal skipped: %s", type(e).__name__)
        return None


def quarter_end(t: TableInfo | None, run: Any, today: dt.date) -> list[Discovery]:
    """Invoicing in the last 5 days of each quarter vs the other days (two years of days)."""
    if t is None:
        return []
    amount = next((n for n in ("net_amount", "net_value", "amount") if t.column(n)), None)
    date = next((c.name for c in t.columns.values() if c.name.endswith("_date")), None)
    if not amount or not date:
        return []
    since = today.replace(year=today.year - 2)
    rows = run(
        t,
        f"SELECT {_q(t.dialect, date)}, SUM({_q(t.dialect, amount)}) FROM {_tbl(t)} "  # noqa: S608
        f"WHERE {_q(t.dialect, date)} >= :since GROUP BY {_q(t.dialect, date)}",
        since=since,
    )
    last: list[Decimal] = []
    rest: list[Decimal] = []
    for d, v in rows:
        d = d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])
        q_end_month = ((d.month - 1) // 3 + 1) * 3
        nxt = dt.date(d.year + (q_end_month == 12), (q_end_month % 12) + 1, 1)
        (last if (nxt - d).days <= 5 else rest).append(Decimal(str(v or 0)))
    if len(last) < 10 or not rest:
        return []
    ratio = (sum(last, Decimal(0)) / len(last)) / (sum(rest, Decimal(0)) / len(rest))
    if ratio < Decimal("1.3"):
        return []
    return [
        Discovery(
            "pattern",
            "quarter_end_push",
            "Invoicing rises in the last 5 days of every quarter. Added as a known non-anomaly.",
            {"table": f"{t.source_name}.{t.schema}.{t.name}", "ratio": str(round(ratio, 2))},
            [
                (
                    "known_pattern",
                    "quarter_end_push",
                    {
                        "name": "Quarter-end invoicing push, last 5 days of each quarter",
                        "window": {"kind": "quarter_end_days", "days": 5},
                    },
                )
            ],
        )
    ]


def discover(
    s: Session, cat: Catalog, mentioned: set[str], query: Any, today: dt.date
) -> list[Discovery]:
    """`query(table, sql, **params) -> rows` runs read-only through the query log."""
    found = overlap_and_gap(s, cat, mentioned)
    found += currency(cat, query)
    found += dimension(cat, query)
    found += splits(cat)
    book = next((d for d in found if d.type == "overlap"), None)
    src = book.rules[0][2]["source"] if book else None
    t = next(
        (x for x in cat.tables.values() if x.source_name == src and _stem(x.name) == "invoice"),
        None,
    )
    found += quarter_end(t, query, today)
    order = {"overlap": 0, "currency": 1, "dimension": 2, "pattern": 3, "gap": 4}
    return sorted(found, key=lambda d: (order[d.type], d.key))


def make_query(log: QueryLog, engines: dict[str, sa.Engine]) -> Any:
    def query(t: TableInfo, sql: str, **params: Any) -> list[Any]:
        with engines[t.source_name].connect() as c:
            return log.run(c, sql, params)

    return query
