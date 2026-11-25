# SPDX-License-Identifier: Apache-2.0
"""v0.19 (M19.5): Big Picture, the owner's design (15 Nov 2026), free writing with numbers checked.
Code computes the tiles, the money at stake, the charts and the forecast cards; the model writes
the words around them; every figure it writes must be a computed value, and becomes a link to its
source; a sentence with any other number is dropped (a finding card falls back to its own text)."""

from __future__ import annotations

import json
from pathlib import Path

from othor.ai.guards import check_input
from othor.briefs import big_picture as bp
from tests.fakes_big_picture import report
from tests.unit.mail.world import issue_1nov

NAMES = {"net_revenue": "Net revenue", "dispatch_time": "Dispatch time"}
FINDINGS = [
    {
        "fp": "abc123",
        "title": "37 orders in sales_dw have no matching invoice in erp_db after 30 days",
        "line": "",
        "money": "₹1.9 Cr",
        "state": "needs_confirmation",
    }
]


def _facts() -> bp.Facts:
    return bp.facts(
        [{"name": "Leadership", "href": "/brief/b1", "body": issue_1nov()}],
        FINDINGS,
        [{"key": "net_revenue", "name": "Net revenue", "description": "Invoice value, net."}],
        NAMES,
    )


def _out(lang: str = "en") -> dict:
    return report(bp.model_input(_facts().inputs, lang))


def _fill(out: dict, lang: str = "en") -> bp.Report:
    return bp.fill(out, _facts(), lang)


def test_the_model_sees_the_computed_figures_and_its_writing_passes():
    inp = bp.model_input(_facts().inputs, "en")
    assert "−₹19 L" in inp["briefs"][0]["situation"]
    assert inp["stakes"][0] == {"fact": "b1i1", "money": "₹1.4 Cr"}
    r = _fill(_out())
    assert r.guard == "passed"
    assert bp.plain(r.body["one_line"]).startswith("October looked flat, but South lost ₹1.4 Cr")


def test_every_figure_is_a_link_to_where_it_came_from():
    lead = _fill(_out()).body["one_line"]
    linked = [s for s in lead if s.get("href")]
    assert [s["t"] for s in linked] == ["₹1.4 Cr"]
    assert linked[0]["href"].startswith("/sweep/finding/")


def test_the_tiles_stakes_and_forecasts_are_computed_not_written():
    b = _fill(_out()).body
    tiles = issue_1nov()["tiles"][: bp.KPIS]
    assert [(k["label"], k["value"]) for k in b["kpis"]] == [
        (t["label"], t["value"]) for t in tiles
    ]
    assert [(s["value"], s["unit"]) for s in b["stakes"]] == [
        ("₹1.4 Cr", ""),
        ("₹4.06 Cr", "at risk"),
        ("₹3.6 Cr", ""),
        ("₹5.84 L", "/ month"),
    ]
    goal = b["forecasts"][0]
    assert (goal["expected"], goal["low"], goal["high"], goal["target"]) == (
        "₹56.7 Cr",
        "₹54.1 Cr",
        "₹59.2 Cr",
        "₹60 Cr",
    )
    assert goal["tone"] == "negative" and "likely to miss" in goal["verdict"]


def test_one_card_per_finding_with_its_own_chart_in_the_models_order():
    b = _fill(_out()).body
    assert [c["fact"] for c in b["findings"]] == ["b1i1", "b1i2", "b1i4"]
    assert all(c["chart"] for c in b["findings"])
    assert b["findings"][0]["tag"] == "Net revenue"
    assert bp.plain(b["findings"][0]["title"]) == "South fell, and West made most of it back"


def test_a_figure_that_isnt_computed_drops_the_words_and_the_card_keeps_its_own():
    r = bp.fill(report(bp.model_input(_facts().inputs, "en"), wrong=True), _facts(), "en")
    freight = r.body["findings"][2]
    assert "35%" not in bp.plain(freight["sentence"])
    assert bp.plain(freight["sentence"]) == issue_1nov()["items"][3]["line"]  # the computed line
    assert r.guard == "dropped:1"


def test_rounded_spelled_or_converted_figures_drop_too():
    out = _out()
    for s in (
        "South fell by ₹1.40 Cr.",
        "South fell by 1.4 crore.",
        "South fell by ₹14 L.",
        "Somewhere near ₹55 Cr.",
    ):
        out["summary"] = [s]
        assert _fill(out).body["summary"] == [], s
    out["summary"] = [
        "West grew by ₹1.21 Cr.",  # computed as +₹1.21 Cr
        "The total moved by -₹19 L.",  # a hyphen-minus for −
        "Most likely between ₹54.1 Cr and ₹59.2 Cr.",  # the ends of ₹54.1–59.2 Cr
    ]
    assert len(_fill(out).body["summary"]) == 3


def test_devanagari_digits_drop():
    out = _out("hi")
    out["summary"] = ["दक्षिण में १.४ करोड़ की गिरावट।", "दक्षिण में ₹1.4 Cr की गिरावट।"]
    got = [bp.plain(x) for x in _fill(out, "hi").body["summary"]]
    assert got == ["दक्षिण में ₹1.4 Cr की गिरावट।"]


def test_actions_carry_their_findings_tag_and_link():
    a = _fill(_out()).body["look_first"]
    assert [x["tag"] for x in a][:2] == ["Net revenue", "Dispatch time"]
    assert all(x["href"].startswith("/sweep/finding/") for x in a)


def test_the_glossary_is_checked_like_the_rest():
    out = {**_out(), "words": [{"term": "P10", "meaning": "x"}, {"term": "Range", "meaning": "y"}]}
    assert [bp.plain(w["term"]) for w in _fill(out).body["words"]] == ["Range"]


def test_nothing_is_kept_when_the_lead_goes():
    assert _fill({**_out(), "one_line": "Down 3%."}).body is None
    assert _fill(None).guard == "failed:not-json"  # type: ignore[arg-type]


def test_the_same_facts_give_the_same_input_and_it_passes_the_input_guard():
    assert _facts().inputs == _facts().inputs
    check_input(bp.model_input(_facts().inputs, "hi"))


def test_the_prompt_asks_for_what_fill_reads():
    schema = json.loads(
        (Path(bp.__file__).parents[1] / "ai/prompts/big_picture/v1.output.json").read_text()
    )
    assert set(schema["required"]) == set(_out())
