# SPDX-License-Identifier: Apache-2.0
"""Helpful questions (v0.12, docs/plans/helpful-questions.md): each finder proposes the sample
company's planted answer from names, kinds and computed counts, never from a personal column;
the same input gives the same cards in the same order; every number on a card is computed;
Let Othor AI decide picks the top card only above its bar, else says why and the next step.

The counts the engine reads by SQL (`othor/engine/suggest_facts.py`) are computed here from the
generator's own frames, the way the SQL would count them; the real path runs in the web tests
(`tests/web/test_helpful_questions.py`, the 1 Nov night)."""

from __future__ import annotations

import datetime as dt
import json
import random
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from othor.ai.guards import numerals
from othor.core import fmt
from othor.engine.store import Point
from othor.ontology import suggest as sg
from othor.ontology.compiler import compile_records
from othor.ontology.types import validate_body
from tests.unit.ontology.test_compiler import _records

RUN = dt.date(2026, 11, 1)


# --- pairs, on synthetic weekly series ---------------------------------------------------------


def _spec(*keys: str, pairs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "metrics": {
            k: {"name": k.replace("_", " ").capitalize(), "status": "active", "unit": "inr"}
            for k in keys
        },
        "pairs": pairs or [],
    }


def _weeks(n: int) -> list[dt.date]:
    last = dt.date(2026, 10, 26)  # a Monday
    return [last - dt.timedelta(weeks=n - 1 - i) for i in range(n)]


def _points(series: dict[str, list[float]]) -> list[Point]:
    out = []
    for m, vals in series.items():
        for d, v in zip(_weeks(len(vals)), vals, strict=True):
            out.append(Point(m, "week", "", None, d, Decimal(str(round(v, 2)))))
    return out


def _walk(seed: int, n: int) -> list[float]:
    r = random.Random(seed)
    x, out = 100.0, []
    for _ in range(n):
        x += r.uniform(-10, 10)
        out.append(x)
    return out


def _planted(n: int = 60) -> dict[str, list[float]]:
    orders = _walk(1, n)
    r = random.Random(2)
    return {
        "orders": orders,
        "invoices": [3 * v + r.uniform(-1, 1) for v in orders],  # moves with orders
        "cash": _walk(3, n),  # its own way
        "returns": _walk(4, n),
    }


def test_pairs_find_the_planted_pair_and_say_why_from_computed_values() -> None:
    spec = _spec("orders", "invoices", "cash", "returns")
    f = sg.pairs(spec, _points(_planted()), RUN)
    top = f.cards[0]
    assert top.change["body"]["over"] == ["invoices", "orders"]
    assert top.change["body"]["relation"] == "together" and top.strong
    assert f.pick == top.key  # Let Othor AI decide takes it: it clears the bar
    ev = top.evidence
    assert ev["agree"] >= sg.PAIR_DECIDE and ev["weeks"] == sg.PAIR_WEEKS
    assert top.sub == f"Moved the same way in {ev['agree']} of the last 52 weeks"
    assert validate_body("rule", top.change["body"])  # a valid pair rule


def test_the_same_input_gives_the_same_cards_in_the_same_order() -> None:
    spec = _spec("orders", "invoices", "cash", "returns")
    pts = _points(_planted())
    shuffled = list(pts)
    random.Random(7).shuffle(shuffled)
    a, b = sg.pairs(spec, pts, RUN), sg.pairs(spec, shuffled, RUN)
    assert a.as_subject() == b.as_subject()
    assert json.dumps(a.as_subject(), sort_keys=True) == json.dumps(b.as_subject(), sort_keys=True)


def test_a_lead_is_found_and_said() -> None:
    base = _walk(5, 64)
    spec = _spec("deals_won", "invoiced")
    later = [base[0]] * 3 + base[:-3]  # invoiced follows deals by 3 weeks
    f = sg.pairs(spec, _points({"deals_won": base, "invoiced": later}), RUN)
    (c,) = f.cards
    assert c.change["body"] == {
        "kind": "pair",
        "text": "Deals won leads invoiced",
        "over": ["deals_won", "invoiced"],
        "relation": "leads",
    }
    assert c.sub.startswith("Deals won moves about 3 weeks ahead; the same way in ")


def test_a_pair_already_in_the_ontology_or_a_part_of_a_number_is_never_suggested() -> None:
    s = _planted()
    spec = _spec("orders", "invoices", "cash", "returns")
    spec["pairs"] = [{"a": "orders", "b": "invoices", "relation": "together"}]
    assert all(c.key != "pair:invoices:orders" for c in sg.pairs(spec, _points(s), RUN).cards)
    spec = _spec("orders", "invoices", "cash", "returns")
    spec["metrics"]["invoices"]["components"] = ["orders"]
    assert all(c.key != "pair:invoices:orders" for c in sg.pairs(spec, _points(s), RUN).cards)


def test_too_little_history_says_so_and_when_to_ask_again() -> None:
    s = {k: v[:20] for k, v in _planted().items()}
    f = sg.pairs(_spec(*s), _points(s), RUN)
    assert f.pick is None and not any(c.strong for c in f.cards)
    assert f.why == "Pairs need a year of weekly history to be sure; you have 19 weeks."
    assert f.next == (
        f"Ask again after {fmt.day_month_year(RUN + dt.timedelta(weeks=33))}, "
        "or pick a pair yourself."
    )


def test_no_pair_holds_says_so_with_the_closest_and_the_next_step() -> None:
    s = {"cash": _walk(3, 60), "returns": _walk(4, 60), "stock": _walk(9, 60)}
    f = sg.pairs(_spec(*s), _points(s), RUN)
    assert f.cards == [] and f.pick is None
    assert f.why.startswith("None of your 3 numbers moved together steadily over the last year.")
    assert "The closest, " in f.why
    assert f.next == "If you know two that should, type them under Something else."


def test_othor_ais_ranking_breaks_ties_and_adds_a_reason_never_beats_evidence() -> None:
    spec = _spec("orders", "invoices", "cash", "returns")
    f = sg.pairs(spec, _points(_planted()), RUN)
    sub = {"needs": "pair", **f.as_subject()}
    ranked = [
        {"a": "cash", "b": "returns", "reason": "Not in the pool"},  # no evidence: ignored
        {"a": "orders", "b": "invoices", "reason": "Orders become invoices"},
    ]
    merged = sg.merge_ranked(spec, sub, ranked)
    top = merged["suggestions"][0]
    assert top["key"] == "pair:invoices:orders" and top["by"] == "code+ai"
    assert top["sub"].endswith(" · Orders become invoices")
    assert merged["decide"]["pick"] == top["key"]
    assert sg.merge_ranked(spec, sub, None)["suggestions"] == sub["suggestions"]


# --- the sample company: reconcile, entity, distribution, threshold ------------------------------


def _without(records: dict, *drop: tuple[str, str]) -> dict:
    out = {k: v for k, v in records.items() if k not in drop}
    b = dict(out[("breakdown", "customer")])
    b.pop("entity", None)
    out[("breakdown", "customer")] = b
    a = dict(out[("metric", "average_order_value")])
    a.pop("distribution", None)
    out[("metric", "average_order_value")] = a
    return out


def _sample(world) -> tuple[dict, Any, dict]:
    records, cat, company = _records(world)
    records = _without(
        records, ("rule", "dispatch_invoice"), ("rule", "approval_limit"), ("rule", "mis_erp")
    )
    return records, cat, compile_records(records, cat, company, 1)


def _keys(df: Any, col: str) -> set[str]:
    return {str(v).strip() for v in df[col].dropna()} - {""}


def test_reconcile_finds_orders_in_sales_dw_against_invoices_in_erp_db(world, sample) -> None:
    records, cat, spec = _sample(world)
    pairs = sg.key_pairs(cat, spec)
    keys = [kp.key for kp in pairs]
    want = "sales_dw.fact_order.order_no>erp_db.invoices.order_ref"
    assert want in keys
    for kp in pairs:  # never a personal column
        assert not kp.left.columns[kp.lcol.lower()].personal
        assert not kp.right.columns[kp.rcol.lower()].personal
    frames = {"sales_dw": sample.dw["sales_dw"], "erp_db": sample.erp}
    facts = {}
    for kp in pairs:  # as the SQL counts: the left's keys of the last year, the right's all
        df = frames[kp.left.source_name][kp.left.name]
        dates = df[kp.date].map(lambda v: v.date() if hasattr(v, "date") else v)
        recent = df[dates >= sample.as_of - dt.timedelta(days=365)]
        left = _keys(recent, kp.lcol)
        right = _keys(frames[kp.right.source_name][kp.right.name], kp.rcol)
        facts[kp.key] = {"left": len(left), "found": len(left & right)}
    f = sg.reconciles(cat, spec, facts)
    card = next(c for c in f.cards if c.key == f"reconcile:{want}")
    n, found = facts[want]["left"], facts[want]["found"]
    share = (Decimal(found) / Decimal(n)).quantize(Decimal("0.0001"))
    assert card.sub == (
        f"{fmt.pct(share)} of order numbers in sales_dw are in erp_db "
        f"({fmt.num(found)} of {fmt.num(n)})"
    )
    body = card.change["body"]
    assert body["on"] == [["order_no", "order_ref"]] and body["kind"] == "reconcile"
    validate_body("rule", body)
    assert compile_records({**records, ("rule", card.change["key"]): body}, *_cat_co(world), 1)
    assert sg.reconciles(cat, spec, facts).as_subject() == f.as_subject()  # same order


def _cat_co(world) -> tuple[Any, dict]:
    _r, cat, company = _records(world)
    return cat, company


def test_reconcile_with_one_source_says_connect_the_other() -> None:
    spec = {"sources": {"erp_db": {}}, "probes": {}}
    f = sg.reconciles(_Cat({}), spec, {})
    assert f.cards == [] and f.href == "/sources"
    assert f.why == (
        "Your numbers come from one source only (erp_db), so there's nothing to match them against."
    )


class _Cat:
    def __init__(self, tables: dict) -> None:
        self.tables = tables


def test_entity_finds_the_customer_list_by_its_status_column_never_a_personal_one(
    world, sample
) -> None:
    records, cat, spec = _sample(world)
    (ask,) = sg.entity_asks(cat, spec, records)
    assert (ask.table.source_name, ask.table.name, ask.status) == ("erp_db", "customers", "status")
    assert ask.breakdown == "customer"
    cust = sample.erp["customers"]
    counts = cust["status"].value_counts()
    values = sorted(([str(v), int(n)] for v, n in counts.items()), key=lambda x: (-x[1], x[0]))
    f = sg.entities(cat, spec, records, {ask.key: {"rows": len(cust), "values": values}})
    (card,) = f.cards
    n_active = int(counts.get("active", 0))
    assert card.sub == (
        f"{fmt.num(len(cust))} customers · has a status column · {fmt.num(n_active)} marked active"
    )
    e = card.change["body"]["entity"]
    assert e["status_column"] == "status" and e["active_values"] == ["active"]
    personal = {c.name for c in ask.table.columns.values() if c.personal}
    assert personal and not personal & set(json.dumps(card.as_dict()).split('"'))
    assert f.pick == card.key
    validate_body("breakdown", card.change["body"])


def test_distribution_finds_order_value_with_one_row_per_order(world, sample) -> None:
    records, cat, spec = _sample(world)
    asks = {a.metric: a for a in sg.distribution_asks(cat, spec, records)}
    ask = asks["orders_value"]
    assert (ask.table.name, ask.column) == ("fact_order", "order_no")
    df = sample.dw["sales_dw"]["fact_order"]
    days = (df["order_date"].max() - df["order_date"].min()).days + 1
    facts = {ask.key: {"rows": len(df), "keys": df["order_no"].nunique(), "days": days}}
    f = sg.distributions(cat, spec, records, facts)
    (card,) = f.cards
    per_day = (Decimal(facts[ask.key]["keys"]) / Decimal(days)).quantize(Decimal("1"))
    assert card.sub == f"One row per order · about {fmt.num(per_day)} a day"
    assert card.change["body"]["distribution"] == {"metric": "orders_value", "key": "order_no"}
    validate_body("metric", card.change["body"])


def test_threshold_finds_the_approval_limit_from_the_datas_own_value(world, sample) -> None:
    _, cat, spec = _sample(world)
    (ask,) = sg.threshold_asks(cat, spec)
    assert (ask.table.name, ask.limit, ask.amount) == (
        "approvals",
        "approval_limit",
        "discount_amount",
    )
    ap = sample.erp["approvals"]
    counts = ap["approval_limit"].value_counts()
    values = sorted(([str(v), int(n)] for v, n in counts.items()), key=lambda x: (-x[1], x[0]))
    f = sg.thresholds(cat, spec, {ask.key: {"rows": len(ap), "values": values}})
    (card,) = f.cards
    body = card.change["body"]
    assert Decimal(body["limit"]) == Decimal("500000") and Decimal(body["band"]) == Decimal("50000")
    assert (
        card.sub
        == f"approvals.approval_limit is {fmt.inr(500000)} on 100% of {fmt.num(len(ap))} rows"
    )
    assert f.pick == card.key
    validate_body("rule", body)


def test_nothing_found_says_so_and_gives_the_next_step() -> None:
    spec = {"sources": {"a": {}, "b": {}}, "metrics": {}, "probes": {}}
    for f in (
        sg.reconciles(_Cat({}), spec, {}),
        sg.entities(_Cat({}), spec, {}, {}),
        sg.distributions(_Cat({}), spec, {}, {}),
        sg.thresholds(_Cat({}), spec, {}),
    ):
        assert f.cards == [] and f.pick is None
        assert f.why and f.next, f.need


# --- the reasons' numbers are computed ---------------------------------------------------------


def test_every_number_on_a_card_comes_from_its_computed_evidence() -> None:
    spec = _spec("orders", "invoices", "cash", "returns")
    for c in sg.pairs(spec, _points(_planted()), RUN).cards:
        allowed = numerals(json.dumps(c.evidence))
        assert numerals(c.sub) <= allowed, (c.sub, c.evidence)


def test_the_finders_and_panel_text_type_no_figure() -> None:
    """Reasons are built from computed values; the only digits typed in the finders are the
    fixed bars and the plan's 30 days, and the panel's new macros type none."""
    src = Path(sg.__file__).read_text()
    strings = re.findall(r'(?<![\w"])f"([^"\n]*)"', src)  # the f-strings: every line a card says
    words = [re.sub(r"\{[^}]*\}", "", s) for s in strings]  # the text, not the values put in
    typed = {n for w in words for n in re.findall(r"\d+", w)}
    assert typed <= {"30"}, typed  # the reconcile rule's lag, as the sample rule has it
    tpl = (
        Path(sg.__file__).parents[1] / "web" / "templates" / "components" / "home.html"
    ).read_text()
    for name in ("qp_found", "qp_decided"):
        block = tpl.split(f"macro {name}(", 1)[1].split("endmacro", 1)[0]
        text = re.sub(r"<[^>]+>|\{[{%#].*?[}%#]\}", "", block)
        assert not re.search(r"\d", text), name
