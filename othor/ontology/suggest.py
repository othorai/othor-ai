# SPDX-License-Identifier: Apache-2.0
"""Helpful questions (v0.12, docs/plans/helpful-questions.md): suggested answers for the five
questions raised when a Sweep check can't run for want of an Ontology record, and what "Let Othor
AI decide" would pick (docs/plans/let-othor-ai-decide.md).

Pure functions over the compiled spec, the stored series, the scanned catalog and the Ontology
records. Counts and shares that need the sources (how many order numbers are in both systems, a
status column's values, the value a limit column holds) are computed by SQL in the engine
(`othor/engine/suggest_facts.py`, read-only, through the query log) and passed in as `facts`.
Names, kinds and computed numbers only: never a personal column, never a row to a model.

Each finder returns a `Finding`: up to three cards, each with the record it would draft and one
line of computed reason, plus what Let Othor AI decide picks (or why it can't, and the next step).
Every number on a card is formatted here from a computed value; the same input gives the same
cards in the same order.
"""

from __future__ import annotations

import datetime as dt
import itertools
import re
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from othor.core import fmt

TOP = 3

# the question, in the owner's terms, and what the check does, in one plain line (plan's table)
QUESTION = {
    "pair": "Should these two numbers move together?",
    "reconcile": "Should these records match between your sources?",
    "entity": "Is this your customer list, with who is active?",
    "distribution": "Can Sweep look at each order's value here?",
    "threshold": "Is there an approval limit on these?",
}
EXPLAIN = {
    "pair": (
        "Sweep watches pairs that should move together; when one moves and the other doesn't, "
        "it tells you."
    ),
    "reconcile": (
        "Sweep checks that what one system records shows up in the other; when it doesn't, "
        "it tells you."
    ),
    "entity": "Sweep watches each active customer; when one goes quiet, it tells you.",
    "distribution": (
        "Sweep looks at each order's value, not only the average; when orders split into two "
        "kinds, it tells you."
    ),
    "threshold": (
        "Sweep checks whether amounts bunch just under a limit, a sign of requests split to "
        "stay under it."
    ),
}

# --- the bars (fixed; docs/plans/let-othor-ai-decide.md) -----------------------------------------

PAIR_WEEKS = 52  # a year of weekly changes, as Broken relationship reads the pair
PAIR_SHOW = 35  # a card: the same way in at least 35 of the 52 weeks (two in three)
PAIR_DECIDE = 39  # Let Othor AI decide: at least 39 of 52 (three in four, "9 of 12")
PAIR_RHO = Decimal("0.7")  # and the changes' rank correlation at least 0.7
PAIR_SHOW_RHO = Decimal("0.5")  # a card needs at least 0.5 (below it, a lag is noise)
PAIR_MAX_LAG = 4  # weeks one number may lead the other
RECONCILE_SHOW = Decimal("0.5")
RECONCILE_DECIDE = Decimal("0.9")
DISTRIBUTION_UNIQUE = Decimal("0.95")  # keys / rows: one row per order
THRESHOLD_SHARE = Decimal("0.8")  # one limit value on at least 80% of the rows
TIE = Decimal("0.02")


@dataclass
class Card:
    key: str
    label: str
    sub: str  # why it's suggested, one line, from computed values
    change: dict[str, Any]  # {"type", "key", "body"}: the record picking it drafts
    evidence: dict[str, Any] = field(default_factory=dict)
    strong: bool = False  # clears Let Othor AI decide's bar
    by: str = "code"  # code · code+ai (Othor AI ranked it too)
    early: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "sub": self.sub,
            "change": self.change,
            "evidence": self.evidence,
            "strong": self.strong,
            "by": self.by,
        }


@dataclass
class Finding:
    need: str
    cards: list[Card] = field(default_factory=list)
    # nothing found, or Let Othor AI decide can't pick: why (computed facts) and the next step
    why: str = ""
    next: str = ""
    href: str = ""
    pick: str | None = None  # the card Let Othor AI decide applies
    # pairs: every pair above the bar, and what Othor AI's ranking needs to merge again after
    # the run (weeks of history, how many numbers, the closest pair, the run date)
    pool: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def as_subject(self) -> dict[str, Any]:
        return {
            "suggestions": [c.as_dict() for c in self.cards],
            "decide": {"pick": self.pick, "why": self.why, "next": self.next, "href": self.href},
            **({"pool": self.pool} if self.pool or self.meta else {}),
            **({"meta": self.meta} if self.meta else {}),
        }


def _slug(v: str, n: int = 60) -> str:
    return re.sub(r"_+", "_", "".join(c if c.isalnum() else "_" for c in v.lower())).strip("_")[:n]


def _name(spec: Mapping[str, Any], key: str) -> str:
    return str((spec.get("metrics") or {}).get(key, {}).get("name") or key.replace("_", " "))


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s[:2] != s[:2].upper() else s


def _decide(cards: list[Card], score: Any) -> tuple[str | None, str]:
    """The top card when it clears the bar and the next one isn't as good; else (None, 'tie' |
    'weak')."""
    if not cards or not cards[0].strong:
        return None, "weak"
    if len(cards) > 1 and cards[1].strong and abs(score(cards[0]) - score(cards[1])) <= TIE:
        return None, "tie"
    return cards[0].key, ""


# --- pair ------------------------------------------------------------------------------------


def _deps(metrics: Mapping[str, Any], m: str, seen: frozenset[str] = frozenset()) -> set[str]:
    out: set[str] = set()
    for c in metrics.get(m, {}).get("components") or []:
        if c not in seen:
            out |= {c} | _deps(metrics, c, seen | {m})
    return out


def weekly(spec: Mapping[str, Any], points: Iterable[Any]) -> dict[str, dict[dt.date, Decimal]]:
    """Each active number's complete weekly totals (the series the run stored)."""
    metrics = spec.get("metrics") or {}
    out: dict[str, dict[dt.date, Decimal]] = {}
    for p in points:
        if p.grain != "week" or p.breakdown != "" or p.value is None:
            continue
        if not getattr(p, "complete", True) or metrics.get(p.metric, {}).get("status") != "active":
            continue
        out.setdefault(p.metric, {})[p.period] = Decimal(p.value)
    return out


def _variant(metrics: Mapping[str, Any], m: str) -> bool:
    fam = metrics.get(m, {}).get("family")
    return bool(fam) and fam != m and fam in metrics


def _changes(xs: list[Decimal]) -> list[Decimal]:
    return [b - a for a, b in itertools.pairwise(xs)]


def _rho(x: list[Decimal], y: list[Decimal]) -> Decimal:
    try:
        r = statistics.correlation([float(v) for v in x], [float(v) for v in y], method="ranked")
    except (statistics.StatisticsError, ValueError):
        return Decimal(0)
    if r != r:  # nan
        return Decimal(0)
    return Decimal(str(round(r, 4)))


def pair_scores(
    spec: Mapping[str, Any], series: Mapping[str, Mapping[dt.date, Decimal]]
) -> tuple[list[dict[str, Any]], int]:
    """Every candidate pair with its computed evidence, best first, and the most weeks any pair
    had (for "too little history"). Pairs left out: two variants of one number, a number and a
    part of it (freight per metre and freight), a pair already in the Ontology, and two series
    that are the same number."""
    metrics = spec.get("metrics") or {}
    have = {frozenset((p["a"], p["b"])) for p in spec.get("pairs") or []}
    keys = sorted(k for k in series if not _variant(metrics, k))
    out: list[dict[str, Any]] = []
    most = 0
    need = PAIR_WEEKS + PAIR_MAX_LAG + 1
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            fa, fb = metrics[a].get("family"), metrics[b].get("family")
            if (fa and fa == fb) or frozenset((a, b)) in have:
                continue
            da, db = _deps(metrics, a), _deps(metrics, b)
            if a in db or b in da or (da & db):
                continue
            common = sorted(set(series[a]) & set(series[b]))
            most = max(most, max(len(common) - 1, 0))
            common = common[-need:]
            if len(common) < PAIR_WEEKS + 1:
                continue
            xa = [series[a][t] for t in common]
            xb = [series[b][t] for t in common]
            if xa == xb:
                continue
            ca, cb = _changes(xa), _changes(xb)
            best: dict[str, Any] | None = None
            for lag in range(PAIR_MAX_LAG + 1):
                for first, second, x, y in ((a, b, ca, cb), (b, a, cb, ca)):
                    if lag == 0 and first == b:
                        continue
                    u = x[: len(x) - lag][-PAIR_WEEKS:]
                    v = y[lag:][-PAIR_WEEKS:]
                    if len(u) < PAIR_WEEKS:
                        continue
                    agree = sum(1 for p, q in zip(u, v, strict=True) if p * q > 0)
                    got: dict[str, Any] = {
                        "a": first,
                        "b": second,
                        "lag": lag,
                        "agree": agree,
                        "weeks": PAIR_WEEKS,
                        "rho": str(_rho(u, v)),
                    }
                    if best is None:
                        best = got
                        continue
                    # a lead only when it is clearly better than together (0.1 more correlation)
                    margin = Decimal("0.1") if best["lag"] == 0 else Decimal(0)
                    if Decimal(got["rho"]) > Decimal(best["rho"]) + margin:
                        best = got
            if best is not None:
                out.append(best)
    out.sort(key=lambda p: (-Decimal(p["rho"]), -p["agree"], p["a"], p["b"]))
    return out, most


def _pair_line(spec: Mapping[str, Any], p: Mapping[str, Any]) -> str:
    same = f"the same way in {fmt.num(p['agree'])} of the last {fmt.num(p['weeks'])} weeks"
    if p["lag"]:
        ahead = "a week" if p["lag"] == 1 else f"about {fmt.num(p['lag'])} weeks"
        return f"{_name(spec, p['a'])} moves {ahead} ahead; {same}"
    return "Moved " + same


def _pair_card(spec: Mapping[str, Any], p: Mapping[str, Any], said: str = "") -> Card:
    a, b = p["a"], p["b"]
    na, nb = _name(spec, a), _name(spec, b)
    relation = "leads" if p["lag"] else "together"
    text = f"{na} leads {_lower_first(nb)}" if p["lag"] else f"{na} moves with {_lower_first(nb)}"
    strong = p["agree"] >= PAIR_DECIDE and Decimal(p["rho"]) >= PAIR_RHO
    sub = _pair_line(spec, p) + (f" · {said}" if said else "")
    return Card(
        key=f"pair:{a}:{b}",
        label=f"{na} and {_lower_first(nb)}",
        sub=sub,
        change={
            "type": "rule",
            "key": _slug(f"pair_{a}_{b}", 80),
            "body": {"kind": "pair", "text": text, "over": [a, b], "relation": relation},
        },
        evidence={k: p[k] for k in ("agree", "weeks", "lag", "rho")},
        strong=strong,
        by="code+ai" if said else "code",
    )


def pairs(
    spec: Mapping[str, Any],
    points: Iterable[Any],
    run_date: dt.date,
    ranked: list[dict[str, str]] | None = None,
) -> Finding:
    """Code's pairs (weekly changes over a year, lead or lag up to 4 weeks), merged with Othor
    AI's ranking from the names (`ranked`: [{a, b, reason}], best first) when there is one: the
    evidence orders the cards, the model only breaks ties and adds a plain reason."""
    series = weekly(spec, points)
    scored, most = pair_scores(spec, series)
    pool = [p for p in scored if p["agree"] >= PAIR_SHOW and Decimal(p["rho"]) >= PAIR_SHOW_RHO]
    return pair_finding(spec, pool, most, len(series), run_date, ranked, scored[:1])


def pair_finding(
    spec: Mapping[str, Any],
    pool: list[dict[str, Any]],
    most: int,
    numbers: int,
    run_date: dt.date,
    ranked: list[dict[str, str]] | None = None,
    closest: list[dict[str, Any]] | None = None,
) -> Finding:
    model_rank: dict[frozenset[str], tuple[int, str]] = {}
    for i, r in enumerate(ranked or []):
        k = frozenset((str(r.get("a")), str(r.get("b"))))
        if len(k) == 2 and k not in model_rank:
            model_rank[k] = (i, str(r.get("reason") or ""))
    big = len(model_rank) + 1

    def order(p: Mapping[str, Any]) -> tuple[Any, ...]:
        strong = p["agree"] >= PAIR_DECIDE and Decimal(p["rho"]) >= PAIR_RHO
        rank = model_rank.get(frozenset((p["a"], p["b"])), (big, ""))[0]
        return (0 if strong else 1, -Decimal(p["rho"]), rank, -p["agree"], p["a"], p["b"])

    chosen: list[dict[str, Any]] = []
    used: set[str] = set()
    for p in sorted(pool, key=order):
        # one card per number: three different pairs, not one number three times
        if p["a"] in used and p["b"] in used:
            continue
        chosen.append(p)
        used |= {p["a"], p["b"]}
        if len(chosen) == TOP:
            break
    cards = [
        _pair_card(spec, p, model_rank.get(frozenset((p["a"], p["b"])), (0, ""))[1]) for p in chosen
    ]
    f = Finding("pair", cards, pool=pool)
    f.meta = {
        "weeks": most,
        "numbers": numbers,
        "closest": list(closest or [])[:1],
        "run_date": run_date.isoformat(),
    }
    if most < PAIR_WEEKS:
        left = PAIR_WEEKS - most
        when = run_date + dt.timedelta(weeks=left)
        f.why = f"Pairs need a year of weekly history to be sure; you have {fmt.num(most)} weeks."
        f.next = f"Ask again after {fmt.day_month_year(when)}, or pick a pair yourself."
        for c in f.cards:
            c.strong = False
        return f
    pick, why = _decide(cards, lambda c: Decimal(c.evidence["rho"]))
    f.pick = pick
    if not cards:
        close = closest[0] if closest else None
        f.why = (
            f"None of your {fmt.num(numbers)} numbers moved together steadily over the last year."
        )
        if close:
            f.why += (
                f" The closest, {_name(spec, close['a'])} and "
                f"{_lower_first(_name(spec, close['b']))}, moved the same way in "
                f"{fmt.num(close['agree'])} of {fmt.num(close['weeks'])} weeks."
            )
        f.next = "If you know two that should, type them under Something else."
    elif why == "tie":
        f.why = (
            f"Two pairs are equally strong: {cards[0].label}, and {_lower_first(cards[1].label)}."
        )
        f.next = "Pick the one Sweep should watch."
    elif why == "weak":
        top = cards[0].evidence
        f.why = (
            f"No pair is steady enough to pick for you: the strongest moved the same way in "
            f"{fmt.num(top['agree'])} of {fmt.num(top['weeks'])} weeks; Othor AI needs "
            f"{fmt.num(PAIR_DECIDE)}."
        )
        f.next = "Pick one yourself if it makes sense for your business."
    return f


def merge_ranked(
    spec: Mapping[str, Any], subject: Mapping[str, Any], ranked: list[dict[str, str]] | None
) -> dict[str, Any]:
    """A pair question's stored subject with Othor AI's ranking merged in (after the run): the
    same computed pool, re-ordered only where the evidence ties, plus the model's short reason
    (the caller drops any reason holding a number). No ranking: code's cards as they were."""
    meta = dict(subject.get("meta") or {})
    if not meta:
        return dict(subject)
    f = pair_finding(
        spec,
        list(subject.get("pool") or []),
        int(meta.get("weeks") or 0),
        int(meta.get("numbers") or 0),
        dt.date.fromisoformat(str(meta["run_date"])),
        ranked,
        list(meta.get("closest") or []),
    )
    return {**dict(subject), **f.as_subject()}


# --- reconcile -------------------------------------------------------------------------------

KEY_COL = re.compile(r"^(?P<stem>[a-z][a-z0-9]*?)_?(no|num|number|ref|reference|id|code|key)$")
GENERIC = {"line", "row", "entry", "batch", "run", "print", "item", "account", "uom", "cn"}


def _stem(col: str) -> str | None:
    m = KEY_COL.match(col.lower())
    if m is None:
        return None
    s = m.group("stem").rstrip("s")
    return s if len(s) >= 3 and s not in GENERIC else None


def _own(table: str, stem: str) -> bool:
    return stem in table.lower()


SUFFIX_RANK = {"no": 0, "num": 0, "number": 0, "ref": 1, "reference": 1, "code": 2}


def _suffix_rank(col: str) -> int:
    m = KEY_COL.match(col.lower())
    return SUFFIX_RANK.get(m.group(2), 3) if m else 9


@dataclass(frozen=True)
class KeyPair:
    key: str
    left: Any  # TableInfo
    lcol: str
    right: Any
    rcol: str
    date: str
    stem: str


def _business_date(t: Any) -> str | None:
    from othor.connectors.classify import Col

    for c in t.columns.values():
        if not c.personal and Col(c.name, c.kind).is_business_date:
            return str(c.name)
    return None


def _readable(t: Any) -> bool:
    return t.use not in ("excluded", "ignored")


def key_pairs(cat: Any, spec: Mapping[str, Any]) -> list[KeyPair]:
    """Tables in two sources that share a key by name and kind: a table's own key (order_no in
    fact_order) and a column with the same stem in another source (order_ref in invoices). Only
    sources the run reads, never a personal column, never an excluded table."""
    readable = set((spec.get("sources") or {}).keys())
    tables = sorted(
        (t for t in cat.tables.values() if t.source_name in readable and _readable(t)),
        key=lambda t: (t.source_name, t.schema, t.name),
    )
    have = {
        (p.get("left", {}).get("table"), p.get("right", {}).get("table"))
        for p in (spec.get("probes") or {}).get("reconcile") or []
        if p.get("kind") == "keys"
    }
    out: list[KeyPair] = []
    for lt in tables:
        if lt.use != "metric_source":
            continue
        date = _business_date(lt)
        if date is None:
            continue
        for lc in sorted(lt.columns.values(), key=lambda c: c.name):
            stem = _stem(lc.name)
            # a document number (text): system ids (int keys) rarely mean the same across systems
            if lc.personal or stem is None or lc.kind != "text" or not _own(lt.name, stem):
                continue
            rights = []
            for rt in tables:
                if rt.source_name == lt.source_name or (lt.name, rt.name) in have:
                    continue
                if rt.use != "metric_source":
                    continue
                for rc in sorted(rt.columns.values(), key=lambda c: c.name):
                    if rc.personal or rc.kind != "text" or _stem(rc.name) != stem:
                        continue
                    k = f"{lt.source_name}.{lt.name}.{lc.name}>{rt.source_name}.{rt.name}.{rc.name}"
                    # the other system's own record first (its invoice), then a reference to it
                    rank = (0 if _own(rt.name, stem) else 1, _suffix_rank(rc.name), rt.name)
                    rights.append((rank, KeyPair(k, lt, lc.name, rt, rc.name, date, stem)))
            # one table per other source: its invoices, not also its invoice lines
            seen: set[str] = set()
            for _r, kp in sorted(rights, key=lambda x: (x[0], x[1].key)):
                if kp.right.source_name not in seen:
                    seen.add(kp.right.source_name)
                    out.append(kp)
    return sorted(out, key=lambda kp: kp.key)[:8]


def _noun(stem: str) -> str:
    return stem.replace("_", " ")


def reconciles(
    cat: Any, spec: Mapping[str, Any], facts: Mapping[str, Mapping[str, int]]
) -> Finding:
    """`facts[pair.key]`: {"left": distinct keys on the left, "found": of those, in the right},
    counted by the engine. The card says the share; Let Othor AI decide takes the best match
    at 90% or more."""
    sources = sorted((spec.get("sources") or {}).keys())
    f = Finding("reconcile")
    if len(sources) < 2:
        f.why = (
            "Your numbers come from one source only"
            + (f" ({sources[0]})" if sources else "")
            + ", so there's nothing to match them against."
        )
        f.next = "Connect the other system that records them."
        f.href = "/sources"
        return f
    scored = []
    for kp in key_pairs(cat, spec):
        got = facts.get(kp.key)
        if not got or not got.get("left"):
            continue
        share = Decimal(got["found"]) / Decimal(got["left"])
        scored.append((kp, share.quantize(Decimal("0.0001")), int(got["left"]), int(got["found"])))
    scored.sort(key=lambda x: (-x[1], x[0].key))
    shown: list[tuple[KeyPair, Decimal, int, int]] = []
    tables: set[frozenset[str]] = set()
    for x in scored:
        # the same two tables the other way round: the better direction only
        two = frozenset(
            (
                f"{x[0].left.source_name}.{x[0].left.name}",
                f"{x[0].right.source_name}.{x[0].right.name}",
            )
        )
        if x[1] >= RECONCILE_SHOW and two not in tables and len(shown) < TOP:
            tables.add(two)
            shown.append(x)
    for kp, share, n, found in shown:
        lt, rt = kp.left, kp.right
        noun = _noun(kp.stem)
        f.cards.append(
            Card(
                key=f"reconcile:{kp.key}",
                label=(
                    f"{lt.name} in {lt.source_name} and {rt.name} in {rt.source_name}, "
                    f"by {noun} number"
                ),
                sub=(
                    f"{fmt.pct(share)} of {noun} numbers in {lt.source_name} are in "
                    f"{rt.source_name} ({fmt.num(found)} of {fmt.num(n)})"
                ),
                change={
                    "type": "rule",
                    "key": _slug(f"reconcile_{lt.name}_{rt.name}", 80),
                    "body": {
                        "kind": "reconcile",
                        "text": (
                            f"Every {noun} in {lt.source_name} is in {rt.source_name} "
                            "within 30 days"
                        ),
                        "table": {"source": lt.source_name, "schema": lt.schema, "table": lt.name},
                        "to": {"source": rt.source_name, "schema": rt.schema, "table": rt.name},
                        "on": [[kp.lcol, kp.rcol]],
                        "date": kp.date,
                        "days": 30,
                        "label": noun + "s",
                    },
                },
                evidence={"share": str(share), "found": found, "left": n},
                strong=share >= RECONCILE_DECIDE,
            )
        )
    pick, why = _decide(f.cards, lambda c: Decimal(c.evidence["share"]))
    f.pick = pick
    if not scored:
        f.why = "No table in one of your sources shares a key with a table in another."
        f.next = "If two systems record the same thing, say which under Something else."
    elif not f.cards:
        kp, share, *_ = scored[0]
        f.why = (
            f"{_noun(kp.stem).capitalize()} numbers match only {fmt.pct(share)} between "
            f"{kp.left.source_name} and {kp.right.source_name}: the formats may differ."
        )
        f.next = "Tell Othor AI the rule under Something else."
    elif why == "tie":
        f.why = (
            f"Two keys match equally well, about {fmt.pct(Decimal(f.cards[0].evidence['share']))}."
        )
        f.next = "Pick the one Sweep should check."
    elif why == "weak":
        f.why = (
            f"The best match is {fmt.pct(Decimal(f.cards[0].evidence['share']))}; Othor AI needs "
            f"{fmt.pct(RECONCILE_DECIDE)} to pick it for you."
        )
        f.next = "Pick it yourself if those records should match."
    return f


# --- entity ----------------------------------------------------------------------------------

ENTITY_TABLE = re.compile(r"(customer|dealer|part(y|ies)|account|client|distributor|buyer)")
STATUS_COL = re.compile(r"^(status|state|is_active|active|account_status|customer_status)$")
ACTIVE = re.compile(r"^(active|live|open|current|yes|y|true|1|a)$", re.I)


@dataclass(frozen=True)
class EntityAsk:
    key: str
    table: Any
    status: str
    breakdown: str
    metric: str


def _entity_metric(spec: Mapping[str, Any], breakdown: str) -> str | None:
    metrics = spec.get("metrics") or {}
    cands = [
        k
        for k, m in metrics.items()
        if m.get("status") == "active"
        and m.get("type") == "base"
        and m.get("kind", "flow") == "flow"
        and breakdown in (m.get("breakdowns") or [])
    ]
    cands.sort(key=lambda k: (metrics[k].get("unit") != "inr", metrics[k].get("grain") != "day", k))
    return cands[0] if cands else None


def entity_asks(
    cat: Any, spec: Mapping[str, Any], records: Mapping[tuple[str, str], Mapping[str, Any]]
) -> list[EntityAsk]:
    """Customer-like tables with a status-like column (by name), behind a breakdown the numbers
    already use, so picking one needs no new join."""
    readable = set((spec.get("sources") or {}).keys())
    out = []
    for (typ, bkey), b in sorted(records.items()):
        if typ != "breakdown" or b.get("entity") or b.get("vocabulary") or not b.get("table"):
            continue
        if not b.get("column"):  # an expression breakdown (0.29) names no customers
            continue
        # the breakdown whose values are the customers themselves (not their region)
        if not ENTITY_TABLE.search(str(b["table"]).lower()):
            continue
        if not ENTITY_TABLE.search(f"{bkey} {b.get('name', '')}".lower()):
            continue
        metric = _entity_metric(spec, bkey)
        if metric is None:
            continue
        for t in sorted(cat.tables.values(), key=lambda t: (t.source_name, t.schema, t.name)):
            if t.name != b["table"] or t.source_name not in readable or not _readable(t):
                continue
            status = next(
                (
                    c.name
                    for c in sorted(t.columns.values(), key=lambda c: c.name)
                    if not c.personal and STATUS_COL.match(c.name.lower())
                ),
                None,
            )
            if status is None or t.column(b["column"]) is None or t.column(b["column"]).personal:
                continue
            out.append(
                EntityAsk(f"{t.source_name}.{t.name}.{status}>{bkey}", t, status, bkey, metric)
            )
    return out


def entities(
    cat: Any,
    spec: Mapping[str, Any],
    records: Mapping[tuple[str, str], Mapping[str, Any]],
    facts: Mapping[str, Mapping[str, Any]],
) -> Finding:
    """`facts[ask.key]`: {"rows": n, "values": [[value, count], ...]} of the status column
    (counted by SQL; a status isn't personal)."""
    f = Finding("entity")
    for ask in entity_asks(cat, spec, records):
        got = facts.get(ask.key) or {}
        values = [(str(v), int(n)) for v, n in got.get("values") or [] if v is not None]
        active = sorted(v for v, _ in values if ACTIVE.match(v.strip()))
        rows = int(got.get("rows") or ask.table.row_count or 0)
        n_active = sum(n for v, n in values if v in active)
        t = ask.table
        noun = "customer" if rows == 1 else "customers"
        sub = f"{fmt.num(rows)} {noun} · has a {ask.status} column"
        if active:
            sub += f" · {fmt.num(n_active)} marked {active[0]}"
        body = {
            **records[("breakdown", ask.breakdown)],
            "entity": {
                "table": {"source": t.source_name, "schema": t.schema, "table": t.name},
                "metric": ask.metric,
                "status_column": ask.status,
                "active_values": active,
            },
        }
        f.cards.append(
            Card(
                key=f"entity:{ask.key}",
                label=f"{t.name} in {t.source_name}",
                sub=sub,
                # merge: at Apply only "entity" is set on the record as it is then (0.28.3)
                change={
                    "type": "breakdown",
                    "key": ask.breakdown,
                    "body": body,
                    "merge": ["entity"],
                },
                evidence={"rows": rows, "active": n_active, "values": len(values)},
                strong=bool(active),
            )
        )
    f.cards = f.cards[:TOP]
    f.pick, why = _decide(f.cards, lambda c: Decimal(0))
    if not f.cards:
        f.why = "None of the tables Othor AI reads is a customer list with a status column."
        f.next = "Connect the system that keeps your customer list."
        f.href = "/sources"
    elif why == "tie":
        f.why = f"Two lists could be it: {f.cards[0].label} and {f.cards[1].label}."
        f.next = "Pick the one that says who is active."
    elif why == "weak":
        f.why = (
            "The status column doesn't say which customers are active in a way Othor AI can read."
        )
        f.next = "Pick it yourself and say which status means active under Something else."
    return f


# --- distribution ----------------------------------------------------------------------------


@dataclass(frozen=True)
class DistributionAsk:
    key: str
    metric: str
    table: Any
    column: str
    date: str


def distribution_asks(
    cat: Any, spec: Mapping[str, Any], records: Mapping[tuple[str, str], Mapping[str, Any]]
) -> list[DistributionAsk]:
    """₹ numbers summed from a table whose own key names one event (order_no in fact_order)."""
    metrics = spec.get("metrics") or {}
    taken = {
        (r.get("distribution") or {}).get("metric")
        for (t, _k), r in records.items()
        if t == "metric" and r.get("distribution")
    }
    out = []
    for m in sorted(metrics):
        e = metrics[m]
        rec = records.get(("metric", m)) or {}
        if (
            e.get("status") != "active"
            or e.get("type") != "base"
            or e.get("agg") != "sum"
            or e.get("unit") != "inr"
            or e.get("kind", "flow") != "flow"
            or m in taken
            or not rec.get("date")
        ):
            continue
        tref = rec.get("table") or {}
        t = cat.table(tref.get("source"), tref.get("schema"), tref.get("table"))
        if t is None:
            continue
        col = next(
            (
                c.name
                for c in sorted(t.columns.values(), key=lambda c: (_suffix_rank(c.name), c.name))
                if not c.personal and (s := _stem(c.name)) and _own(t.name, s)
            ),
            None,
        )
        if col is None:
            continue
        out.append(DistributionAsk(f"{m}:{t.name}.{col}", m, t, col, str(rec["date"])))
    return out


def distributions(
    cat: Any,
    spec: Mapping[str, Any],
    records: Mapping[tuple[str, str], Mapping[str, Any]],
    facts: Mapping[str, Mapping[str, int]],
) -> Finding:
    """`facts[ask.key]`: {"rows", "keys", "days"} over the last year, counted by SQL."""
    f = Finding("distribution")
    found = []
    for ask in distribution_asks(cat, spec, records):
        got = facts.get(ask.key) or {}
        rows, keys, days = (int(got.get(k) or 0) for k in ("rows", "keys", "days"))
        if not rows or not keys or not days:
            continue
        unique = (Decimal(keys) / Decimal(rows)).quantize(Decimal("0.0001"))
        found.append((ask, rows, keys, days, unique))
    # one row per event first, then the busiest (more values a day, a better test)
    found.sort(key=lambda x: (x[4] < DISTRIBUTION_UNIQUE, -Decimal(x[2]) / Decimal(x[3]), x[0].key))
    for ask, rows, keys, days, unique in found[:TOP]:
        t = ask.table
        noun = _noun(_stem(ask.column) or "record")
        per_day = (Decimal(keys) / Decimal(days)).quantize(Decimal("1"))
        one = unique >= DISTRIBUTION_UNIQUE
        rec = dict(records.get(("metric", ask.metric)) or {})
        rec["distribution"] = {"metric": ask.metric, "key": ask.column}
        f.cards.append(
            Card(
                key=f"distribution:{ask.key}",
                label=f"{_name(spec, ask.metric)} · {t.name} in {t.source_name}",
                sub=(
                    (
                        f"One row per {noun}"
                        if one
                        else f"{fmt.num(keys)} {noun}s in {fmt.num(rows)} rows"
                    )
                    + f" · about {fmt.num(per_day)} a day"
                ),
                change={
                    "type": "metric",
                    "key": ask.metric,
                    "body": rec,
                    "merge": ["distribution"],
                },
                evidence={"rows": rows, "keys": keys, "days": days, "per_day": str(per_day)},
                strong=one and per_day >= 1,
            )
        )
    f.pick, why = _decide(f.cards, lambda c: Decimal(c.evidence["per_day"]))
    if not f.cards:
        f.why = "None of your numbers is read from a table with one row per order."
        f.next = "Connect the table your orders are kept in, one row each."
        f.href = "/sources"
    elif why == "tie":
        f.why = f"Two numbers could be it: {f.cards[0].label} and {f.cards[1].label}."
        f.next = "Pick the one whose single values Sweep should look at."
    elif why == "weak":
        f.why = "The table has more than one row per order, so single order values can't be read."
        f.next = "Pick it yourself if each row is one order."
    return f


# --- threshold -------------------------------------------------------------------------------

LIMIT_COL = re.compile(r"(limit|threshold|ceiling|cap)$")
AMOUNT_COL = re.compile(r"(amount|value|total|discount|price)")


@dataclass(frozen=True)
class ThresholdAsk:
    key: str
    table: Any
    limit: str
    amount: str


def threshold_asks(cat: Any, spec: Mapping[str, Any]) -> list[ThresholdAsk]:
    """A limit-like column (approval_limit, credit_limit) beside an amount column in the same
    table; approvals first."""
    readable = set((spec.get("sources") or {}).keys())
    have = {(t.get("key")) for t in (spec.get("probes") or {}).get("thresholds") or []}
    out = []
    for t in sorted(cat.tables.values(), key=lambda t: (t.source_name, t.schema, t.name)):
        if t.source_name not in readable or not _readable(t):
            continue
        cols = sorted(t.columns.values(), key=lambda c: c.name)
        num = [c for c in cols if not c.personal and c.kind in ("int", "decimal", "float")]
        for lc in num:
            if not LIMIT_COL.search(lc.name.lower()):
                continue
            amount = next(
                (
                    c.name
                    for c in num
                    if c.name != lc.name
                    and AMOUNT_COL.search(c.name.lower())
                    and not LIMIT_COL.search(c.name.lower())
                ),
                None,
            )
            if amount is None:
                continue
            k = f"{t.source_name}.{t.name}.{lc.name}>{amount}"
            if _slug(f"threshold_{t.name}_{amount}", 80) in have:
                continue
            out.append(ThresholdAsk(k, t, lc.name, amount))
    out.sort(key=lambda a: ("approv" not in (a.limit + a.table.name).lower(), a.key))
    return out


def thresholds(
    cat: Any, spec: Mapping[str, Any], facts: Mapping[str, Mapping[str, Any]]
) -> Finding:
    """`facts[ask.key]`: {"rows": n, "values": [[limit value, count], ...]} most common first,
    counted by SQL. The limit on a card is the data's own value, never one a model wrote."""
    f = Finding("threshold")
    levels = 0
    for ask in threshold_asks(cat, spec):
        got = facts.get(ask.key) or {}
        rows = int(got.get("rows") or 0)
        values = [(Decimal(str(v)), int(n)) for v, n in got.get("values") or [] if v is not None]
        values = [(v, n) for v, n in values if v > 0]
        if not rows or not values:
            continue
        limit, n = values[0]
        share = (Decimal(n) / Decimal(rows)).quantize(Decimal("0.0001"))
        if len(values) > 1 and Decimal(values[1][1]) / Decimal(rows) >= Decimal("0.2"):
            levels = max(levels, 2)
        t = ask.table
        limit_s = format(limit.normalize(), "f")
        band = format((limit / 10).normalize(), "f")
        amount_name = ask.amount.replace("_", " ")
        f.cards.append(
            Card(
                key=f"threshold:{ask.key}",
                label=(
                    f"{amount_name.capitalize()} over {fmt.inr(limit)} · "
                    f"{t.name} in {t.source_name}"
                ),
                sub=(
                    f"{t.name}.{ask.limit} is {fmt.inr(limit)} on {fmt.pct(share)} of "
                    f"{fmt.num(rows)} rows"
                ),
                change={
                    "type": "rule",
                    "key": _slug(f"threshold_{t.name}_{ask.amount}", 80),
                    "body": {
                        "kind": "threshold",
                        "text": f"{amount_name.capitalize()} above {fmt.inr(limit)} needs approval",
                        "table": {"source": t.source_name, "schema": t.schema, "table": t.name},
                        "column": ask.amount,
                        "limit": limit_s,
                        "band": band,
                        "label": f"{amount_name} limit",
                    },
                },
                evidence={"limit": limit_s, "share": str(share), "rows": rows},
                strong=share >= THRESHOLD_SHARE,
            )
        )
    f.cards = f.cards[:TOP]
    f.pick, why = _decide(f.cards, lambda c: Decimal(c.evidence["share"]))
    if not f.cards:
        f.why = "The tables Othor AI reads don't show a limit: no limit column beside an amount."
        f.next = "Type the limit under Something else, or say you don't have one."
    elif levels > 1 and why != "tie":
        f.pick = None
        f.why = "The limit column holds more than one level, maybe two approvers."
        f.next = "Pick the level Sweep should check."
    elif why == "tie":
        f.why = f"Two limits could be it: {f.cards[0].label} and {_lower_first(f.cards[1].label)}."
        f.next = "Pick the one Sweep should check."
    elif why == "weak":
        f.why = "The limit changes from row to row, so no single limit holds."
        f.next = "Pick it yourself, or type the limit under Something else."
    return f


# --- what the questions step stores ----------------------------------------------------------


def subject_for(need: str, found: Finding) -> dict[str, Any]:
    return {"needs": need, **found.as_subject()}
