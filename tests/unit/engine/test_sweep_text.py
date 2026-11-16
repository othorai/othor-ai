# SPDX-License-Identifier: Apache-2.0
"""Sweep's words (M6b): every number in a card's text comes from the finding (non-negotiable 1),
the texts read like boards 5a–5c and 10p, and the same finding gives the same text."""

from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pytest

from othor.core import fmt
from othor.engine import sweep_text

FIX = json.loads((Path(__file__).parent / "fixtures" / "findings_1nov.json").read_text())
SPEC = FIX["spec"]
BY = {(f["detector"], f["breakdown_value"]): f for f in FIX["findings"]}
NUM = re.compile(r"\d+(?:[.,]\d+)*")
DEFINITIONAL = {"12"}  # "the last 12 months", "Last 12 m": the window's definition, not a value


def _values(o: Any) -> list[Any]:
    if isinstance(o, dict):
        return [v for x in o.values() for v in _values(x)]
    if isinstance(o, list):
        return [v for x in o for v in _values(x)]
    return [o]


def _forms(f: dict[str, Any]) -> set[str]:
    """Every way the formatters can write a value of the finding."""
    out: set[str] = set()
    for v in _values(f):
        if isinstance(v, bool) or v is None:
            continue
        s = str(v)
        try:
            day = dt.date.fromisoformat(s[:10])
            out.add(str(day.day))
            continue
        except ValueError:
            pass
        try:
            d = Decimal(s)
        except InvalidOperation:
            out |= set(NUM.findall(s))
            continue
        for x in (d, abs(d)):
            out.add(fmt.num(x))
            for k in (1, 2):
                out.add(fmt.num(x, decimals=k))
            out.add(fmt.pct(x).rstrip("%"))
            out.add(fmt.pct(x, decimals=1).rstrip("%"))
            out.add(str(round(x)))
            out |= set(NUM.findall(sweep_text.money(x)))
            out |= set(NUM.findall(sweep_text._cr(x)))  # a column in one unit (money_col)
            out |= set(NUM.findall(sweep_text.money(x / 12)))
    return {n.replace(",", "") for n in out}


def _texts(w: dict[str, Any]) -> list[str]:
    out = [w["title"], w["line"], w["consequence"], w["why_unseen"], w["money"]]
    if w.get("question"):
        out.append(w["question"]["text"])
    out += [r["text"] for r in w["reasons"]] + [r["settle"] for r in w["reasons"]]
    out += [v for _, v in w["measured"]] + w["settle"]
    if w.get("rows"):
        out += [c for row in w["rows"]["rows"] for c in row]
    return [t for t in out if t]


@pytest.mark.parametrize("key", sorted(BY, key=str))
def test_every_number_in_the_text_comes_from_the_finding(key):
    f = BY[key]
    w = sweep_text.words(f, SPEC)
    allowed = _forms(f) | DEFINITIONAL
    for text in _texts(w):
        for n in NUM.findall(text):
            assert n.replace(",", "") in allowed, (key, n, text)


def test_the_texts_read_like_the_boards():
    group = sweep_text.words(BY[("missing_records", None)], SPEC)
    assert group["title"] == "14 customers still marked active haven’t invoiced in 90+ days"
    assert group["money"] == "₹3.6 Cr"
    assert group["why_unseen"].startswith("erp_db has no churn status.")
    assert group["question"]["to"] and group["question"]["to"][0]["name"] == "Priya Menon"
    assert group["rows"]["total"] == 14 and group["rows"]["rows"][0][0] == "Shree Varada Textiles"
    cross = sweep_text.words(BY[("cross_source", None)], SPEC)
    assert cross["title"] == (
        "37 orders in sales_dw have no matching invoice in erp_db after 30 days"
    )
    assert cross["question"]["text"] == "Which system is right about these 37 orders?"
    assert len(cross["reasons"]) == 3
    nd = sweep_text.words(BY[("missing_records", "Ravira Exports")], SPEC)
    assert nd["title"] == "Ravira Exports stopped invoicing on 9 Oct"
    moved = [r for r in nd["reasons"] if r["text"].startswith("Billing moved")]
    assert moved and moved[0]["ruled_out"] and "GSTIN" in moved[0]["settle"]
    nhg = sweep_text.words(BY[("net_hiding_gross", None)], SPEC)
    assert nhg["title"] == "South fell ₹1.4 Cr; West’s rise hid it"
    pair = sweep_text.words(BY[("broken_relationship", None)], SPEC)
    assert pair["money"].endswith(" / month")
    rv = sweep_text.words(BY[("rising_variance", "Coimbatore")], SPEC)
    assert rv["money"].endswith(" at risk") and "Coimbatore" in rv["title"]


def test_the_same_finding_gives_the_same_text():
    for f in FIX["findings"]:
        assert sweep_text.words(f, SPEC) == sweep_text.words(json.loads(json.dumps(f)), SPEC)
