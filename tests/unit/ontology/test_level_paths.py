# SPDX-License-Identifier: Apache-2.0
"""Business Model 2.4 · L2: every checklist item has a tested chat path (docs/plans/business-model-
levels.md, "Prerequisite: the chat must apply every item"): one sentence in, the drafted change
out (checked by code), its effect and values before and after computed, one Apply.

The 20 Nov case first: "Net revenue is invoiced value minus rebates (erp_db cost_ledger account
4410)" was refused with "Try saying it another way, with the number and the date" in both
Business Model boxes because rebates wasn't a number yet. A change that needs a new number
proposes adding it (Rebates from cost_ledger 4410) and the formula, applied together.

The world is the demo accounts' Business Model (the sample without rebates); the model is a fake
returning what Claude would draft, never a real one."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest

from othor.chat import changes as cc
from othor.chat import service as chat
from othor.core.tenancy import tenant_scope
from othor.home import changes as hc
from othor.ontology import service
from tests.fixtures.sample_company.catalog import catalog_from_generator
from tests.fixtures.sample_company.ontology import T, m, op, sample_ontology
from tests.unit.ontology.conftest import OWNER, World

TODAY = dt.date(2026, 11, 2)
REBATES_LINE = "Net revenue is invoiced value minus rebates (erp_db cost_ledger account 4410)"
REBATES = {
    "name": "Rebates",
    "table": T("erp_db", "erp_db", "cost_ledger"),
    "measure": {"agg": "sum", "expr": {"col": "amount"}},
    "date": "entry_date",
    "filters": [op("=", {"col": "account_code"}, {"lit": 4410})],
    "listed": False,
}


def _demo(records: list[Any]) -> list[Any]:
    """The sample without rebates: net revenue = invoiced value − credit notes."""
    out = []
    for c in records:
        if (c.type, c.key) in (("metric", "rebates"), ("rule", "alloc_rebates")):
            continue
        if (c.type, c.key) == ("metric", "net_revenue"):
            body = dict(c.body)
            body["formula"] = op("-", m("invoiced_value", "zero"), m("credit_note_value", "zero"))
            body["breakdown_formulas"] = {
                "plant": op("-", m("line_revenue", "zero"), m("credit_note_value", "zero"))
            }
            c = service.put("metric", "net_revenue", body, c.source_ref)
        out.append(c)
    return out


@pytest.fixture
def demo(session_factory, two_companies, sample) -> World:
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        catalog_from_generator(s, sample)
        service.apply(s, _demo(sample_ontology()), actor=OWNER, reason="Demo Business Model")
        service.confirm(s, actor=OWNER)
        s.commit()
    return World(session_factory, cid)


class GW:
    """The gateway as the chat sees it: what Claude would draft for the line."""

    def __init__(self, out: dict[str, Any]) -> None:
        self.out, self.inputs = out, None

    def complete(self, company_id: Any, prompt: str, version: str, inputs: Any, **kw: Any) -> Any:
        from othor.ai.gateway import Result

        self.inputs = inputs
        return Result(self.out, json.dumps(self.out), "passed")


def _ch(type_: str, key: str, body: dict[str, Any] | None, op_: str = "put") -> dict[str, Any]:
    return {"op": op_, "type": type_, "key": key, "body_json": json.dumps(body) if body else ""}


def _draft(w: World, subject: str, line: str, changes: list[dict[str, Any]]) -> tuple[Any, GW]:
    gw = GW({"changes": changes, "reply": "", "clarify": None})
    with w.sf() as s, tenant_scope(w.cid):
        t = chat.thread(s, subject)
        msg = cc.draft(s, gw, w.company(s), t, line, actor=OWNER, today=TODAY)
        s.commit()
        assert msg is not None
        return (msg.text, list((msg.proposal or {}).get("items") or [])), gw


def _apply(w: World, subject: str) -> int:
    with w.sf() as s, tenant_scope(w.cid):
        n = service.current_number(s)
        cc.apply(s, w.company(s), chat.thread(s, subject), actor=OWNER)
        s.commit()
        assert service.current_number(s) == n + 1, "applied as one new version"
        return n + 1


def _net_revenue(w: World) -> dict[str, Any]:
    with w.sf() as s, tenant_scope(w.cid):
        return next(r.body for r in service.active(s, ["metric"]) if r.key == "net_revenue")


NET_REVENUE_CHANGES = [
    _ch("metric", "rebates", REBATES),
    _ch(
        "metric",
        "net_revenue",
        {
            "formula": op(
                "-",
                op("-", m("invoiced_value", "zero"), m("credit_note_value", "zero")),
                m("rebates", "zero"),
            ),
            "description": "Invoiced value minus credit notes and rebates",
        },
    ),
]


@pytest.mark.parametrize("subject", ["ontology:metric:net_revenue", "ontology"])
def test_rebates_a_definition_that_needs_a_new_number_is_one_apply(demo: World, subject) -> None:
    """Both boxes: Change Net revenue, and the page's Update your Business Model."""
    (text, items), gw = _draft(demo, subject, REBATES_LINE, NET_REVENUE_CHANGES)
    assert text != hc.COULDNT and "Try saying it another way" not in text
    # the tables the line names reach the model by name (columns, never rows: rule 3)
    tables = {f"{t['source']}.{t['table']}": t for t in gw.inputs["tables"]}
    assert "account_code" in tables["erp_db.cost_ledger"]["columns"]
    assert [i["change"]["key"] for i in items] == ["rebates", "net_revenue"]
    assert all(i["status"] == "pick" for i in items), [i["effect"] for i in items]
    add, change = items
    assert add["after"].startswith("Rebates") and add["text"] == "Add · Numbers"
    assert change["text"] == "Change · Numbers" and "Net revenue" in " ".join(change["lines"])
    # the formula needs the new number: it's applied with it
    assert change["needs"] == ["rebates"]
    v = _apply(demo, subject)
    body = _net_revenue(demo)
    assert "rebates" in json.dumps(body["formula"])
    with demo.sf() as s, tenant_scope(demo.cid):
        keys = {r.key for r in service.active(s, ["metric"])}
        assert "rebates" in keys and service.current_number(s) == v


def test_rebates_values_before_and_after_are_computed(demo: World) -> None:
    """Today's value before and after reads both specs (the preview), with the new number in."""
    (_, items), _ = _draft(demo, "ontology:metric:net_revenue", REBATES_LINE, NET_REVENUE_CHANGES)
    seen: dict[str, Any] = {}

    def preview(cur: Any, new: Any, keys: list[str], today: dt.date) -> list:
        seen.update(cur=cur, new=new, keys=keys)
        return [("Net revenue", "₹4.10 Cr", "₹4.02 Cr")]

    with demo.sf() as s, tenant_scope(demo.cid):
        e = hc.effect(
            s,
            demo.company(s),
            items[1]["change"],
            preview=preview,
            today=TODAY,
            also=cc.needed(items, items[1]),
        )
    assert e.error is None and "net_revenue" in seen["keys"] and "rebates" in seen["new"]["metrics"]
    assert "rebates" not in seen["cur"]["metrics"]
    assert hc.value_lines(e) == ["Net revenue today: ₹4.10 Cr → ₹4.02 Cr"]


def test_a_new_number_on_a_column_that_isnt_there_is_refused(demo: World) -> None:
    bad = {**REBATES, "filters": [op("=", {"col": "no_such_column"}, {"lit": 4410})]}
    (text, items), _ = _draft(demo, "ontology", REBATES_LINE, [_ch("metric", "rebates", bad)])
    assert items == [] and text == hc.COULDNT


def test_a_number_the_owner_didnt_say_is_refused(demo: World) -> None:
    bad = {**REBATES, "filters": [op("=", {"col": "account_code"}, {"lit": 4420})]}
    (_text, items), _ = _draft(demo, "ontology", REBATES_LINE, [_ch("metric", "rebates", bad)])
    assert items == []


# --- one path per checklist item ------------------------------------------------------------


def _one(w: World, line: str, change: dict[str, Any], subject: str = "ontology") -> dict[str, Any]:
    (text, items), _ = _draft(w, subject, line, [change])
    assert len(items) == 1, text
    assert items[0]["status"] == "pick", items[0]["effect"]
    return items[0]


def test_owners_from_one_sentence(demo: World) -> None:
    it = _one(
        demo,
        "The Head of Sales owns Net revenue and Orders",
        _ch(
            "owner",
            "head_of_sales",
            {"name": "Head of Sales", "role": "Head of Sales", "areas": ["net_revenue", "orders"]},
        ),
    )
    assert it["text"] == "Add · Names and words"
    _apply(demo, "ontology")


def test_rules_from_one_sentence(demo: World) -> None:
    it = _one(
        demo,
        "Leave goodwill credit notes out of Net revenue",
        _ch(
            "rule",
            "goodwill_credit_notes",
            {
                "kind": "filter",
                "table": T("erp_db", "erp_db", "credit_notes"),
                "expr": op("<>", {"col": "reason"}, {"lit": "Goodwill"}),
                "text": "Goodwill credit notes are left out",
            },
        ),
    )
    assert "Net revenue" in " ".join(it["lines"])  # the numbers it changes, computed


def test_breakdowns_from_one_sentence(demo: World) -> None:
    with demo.sf() as s, tenant_scope(demo.cid):
        m_ = next(r for r in service.active(s, ["metric"]) if r.key == "receivables_open")
    it = _one(
        demo,
        "Break open receivables down by customer",
        _ch(
            "metric",
            "receivables_open",
            {"breakdowns": [*m_.body["breakdowns"], "customer"]}
            if "customer" not in m_.body.get("breakdowns", [])
            else {"breakdowns": ["region"]},
        ),
    )
    assert it["lines"]


def test_numbers_built_from_parts_from_one_sentence(demo: World) -> None:
    it = _one(
        demo,
        "Add a number: invoiced value minus credit notes, called Sales after returns",
        _ch(
            "metric",
            "sales_after_returns",
            {
                "name": "Sales after returns",
                "formula": op("-", m("invoiced_value", "zero"), m("credit_note_value", "zero")),
            },
        ),
        subject="ontology:add",
    )
    assert it["after"].startswith("Sales after returns")


def test_known_patterns_from_one_sentence(demo: World) -> None:
    it = _one(
        demo,
        "Every year sales dip from 20 Dec to 2 Jan",
        _ch(
            "known_pattern",
            "year_end_dip",
            {
                "name": "Year-end dip",
                "window": {"kind": "annual", "start": "12-20", "end": "01-02"},
                "metrics": ["net_revenue"],
            },
        ),
    )
    assert any("Sweep won't flag" in x for x in it["lines"])


def test_headline_numbers_from_one_sentence(demo: World) -> None:
    it = _one(
        demo,
        "Show Orders at the top of the brief",
        _ch("metric", "orders", {"headline": True}),
    )
    assert it["text"] == "Change · Numbers"


def test_goals_from_one_sentence(demo: World) -> None:
    it = _one(
        demo,
        "Orders at least 900 by 31 March 2027",
        _ch(
            "goal",
            "orders_fy27",
            {
                "name": "Orders FY27",
                "metric": "orders",
                "target": "900",
                "direction": "at_least",
                "by": "2027-03-31",
                "aggregate": "value",
            },
        ),
    )
    assert it["text"] == "Add · The business"


def test_pairs_from_one_sentence(demo: World) -> None:
    _one(
        demo,
        "Freight per metre moves with metres dispatched",
        _ch(
            "rule",
            "pair_freight_metres",
            {
                "kind": "pair",
                "text": "Freight moves with metres",
                "over": ["freight_per_metre", "metres_dispatched"],
                "relation": "together",
            },
        ),
    )


def test_cross_source_checks_from_one_sentence(demo: World) -> None:
    _one(
        demo,
        "Invoiced value should match line revenue every month",
        _ch(
            "rule",
            "check_invoices_lines",
            {
                "kind": "reconcile",
                "grain": "month",
                "text": "Invoices match their lines",
                "over": ["invoiced_value", "line_revenue"],
            },
        ),
    )


def test_named_customers_from_one_sentence(demo: World) -> None:
    _one(
        demo,
        "Our key customer is Lumora Textiles",
        _ch("customer", "lumora", {"name": "Lumora Textiles"}),
    )


def test_words_and_facts_from_one_sentence(demo: World) -> None:
    _one(
        demo,
        "When we say dispatch, we mean goods leaving the plant",
        _ch("vocabulary", "dispatch", {"term": "dispatch", "text": "Goods leaving the plant"}),
    )


def test_watches_from_one_sentence(demo: World) -> None:
    from othor.engine import watches

    it = _one(
        demo,
        "Watch net revenue: tell me when it falls below ₹4 Cr",
        _ch(
            "watch",
            "net_revenue_below",
            {
                "metric": "net_revenue",
                "threshold": "40000000",
                "direction": "below",
                "label": "Net revenue",
            },
        ),
    )
    assert it["text"] == "Add · Watches" and "₹4" in it["after"]
    with demo.sf() as s, tenant_scope(demo.cid):
        n = service.current_number(s)
        cc.apply(s, demo.company(s), chat.thread(s, "ontology"), actor=OWNER)
        s.commit()
        got = watches.snapshot(s)
        assert service.current_number(s) == n  # a watch isn't a Business Model version
    assert [(w["label"], w["threshold"]) for w in got][-1] == ("Net revenue", "40000000")
    # a threshold the owner didn't say is refused
    (_text, items), _ = _draft(
        demo,
        "ontology",
        "Watch net revenue: tell me when it falls below ₹4 Cr",
        [
            _ch(
                "watch",
                "x",
                {
                    "metric": "net_revenue",
                    "threshold": "45000000",
                    "direction": "below",
                    "label": "Net revenue",
                },
            )
        ],
    )
    assert items == []


# --- L3: ready drafts from the files and sources -----------------------------------------------


def _files(w: World) -> None:
    """What setup read: the owner's business message and a board review's items (no names)."""
    from othor.setup import business
    from othor.setup.models import Upload, UploadRef

    with w.sf() as s, tenant_scope(w.cid):
        business.save(
            s,
            text="Sales is run by our Head of Sales; finance and collections by the Head of "
            "Finance.",
            data_lives=[],
            actor=OWNER,
        )
        u = Upload(
            filename="Board-review-Q2-FY27.pdf",
            kind="pdf",
            content_type="application/pdf",
            size=1,
            sha256="0" * 64,
            storage_path="x",
            status="read",
            uploaded_by=OWNER,
        )
        s.add(u)
        s.flush()
        s.add(
            UploadRef(
                upload_id=u.id,
                locator="slide 4",
                kind="target",
                text="Orders: 900 by 31 March 2027",
            )
        )
        s.commit()


def test_drafts_from_files_are_checked_and_applied_in_one_click(demo: World) -> None:
    from othor.chat import level_drafts
    from othor.ontology import levels

    with demo.sf() as s, tenant_scope(demo.cid):
        gone = [service.retire("owner", r.key) for r in service.active(s, ["owner"])]
        gone += [service.retire("goal", r.key) for r in service.active(s, ["goal"])]
        service.apply(s, gone, actor=OWNER, reason="As the demo accounts")
        s.commit()
    _files(demo)
    owners = _ch(
        "owner",
        "head_of_sales",
        {"name": "Head of Sales", "role": "Head of Sales", "areas": ["net_revenue", "orders"]},
    )
    owners2 = _ch(
        "owner",
        "head_of_finance",
        {
            "name": "Head of Finance",
            "role": "Head of Finance",
            "areas": ["receivables_open", "collections"],
        },
    )
    goal = _ch(
        "goal",
        "orders_fy27",
        {
            "name": "Orders FY27",
            "metric": "orders",
            "target": "900",
            "direction": "at_least",
            "by": "2027-03-31",
            "aggregate": "value",
        },
    )
    invented = _ch(
        "goal",
        "dso_target",
        {
            "name": "DSO",
            "metric": "dso",
            "target": "41",
            "direction": "at_most",
            "by": "2027-03-31",
            "aggregate": "value",
        },
    )
    gw = GW(
        {
            "drafts": [
                {
                    "item": "owners",
                    "text": "Add the Head of Sales and the Head of Finance as owners",
                    "why": "Named in your business message",
                    "changes": [owners, owners2],
                },
                {
                    "item": "goal",
                    "text": "Add the orders goal from your board review",
                    "why": "Board review, slide 4",
                    "changes": [goal],
                },
                {
                    "item": "goals",
                    "text": "Keep DSO under 41 days",
                    "why": "",
                    "changes": [invented],
                },
                {"item": "rules", "text": "Not missing", "why": "", "changes": [owners]},
            ]
        }
    )
    with demo.sf() as s, tenant_scope(demo.cid):
        n = level_drafts.find(s, gw, demo.company(s), actor=OWNER, today=TODAY)
        s.commit()
        found = level_drafts.drafts(s)
    # names and computed results only; the board review's items with their place
    inp = gw.inputs
    assert {m["key"] for m in inp["missing"]} >= {"owners", "goal"}
    assert inp["files"] == [
        {
            "kind": "target",
            "text": "Orders: 900 by 31 March 2027",
            "file": "Board-review-Q2-FY27.pdf",
            "locator": "slide 4",
        }
    ]
    assert "rows" not in str(inp)
    # the invented 41 is refused (rule 1); an item that isn't missing is ignored
    assert n == 2 and set(found) == {"owners", "goal"}
    assert found["owners"][0]["n"] == 2 and found["goal"][0]["why"] == "Board review, slide 4"
    with demo.sf() as s, tenant_scope(demo.cid):
        before = service.current_number(s)
        cc.apply(s, demo.company(s), chat.thread(s, level_drafts.subject("owners")), actor=OWNER)
        s.commit()
        assert service.current_number(s) == before + 1  # one Apply, one version
        assert {r.key for r in service.active(s, ["owner"])} == {"head_of_sales", "head_of_finance"}
        assert "owners" not in level_drafts.drafts(s)
        assert next(i for i in levels.check(s).items if i.key == "owners").done


def test_no_model_no_drafts(demo: World) -> None:
    from othor.ai.gateway import Result
    from othor.chat import level_drafts

    class Off:
        def complete(self, *a: Any, **k: Any) -> Result:
            return Result(None, None, "template")

    with demo.sf() as s, tenant_scope(demo.cid):
        gone = [service.retire("owner", r.key) for r in service.active(s, ["owner"])]
        service.apply(s, gone, actor=OWNER, reason="No owners")
        s.commit()
        assert level_drafts.find(s, Off(), demo.company(s), actor=OWNER, today=TODAY) == 0
        assert level_drafts.drafts(s) == {}
