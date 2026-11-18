# SPDX-License-Identifier: Apache-2.0
"""Real rows behind a choice in the Othor AI chat (0.26, M26.3) on the sample night: Othor AI's
pick shows its rows as the chat opens, other choices on "See a few rows"; the rows are read live
from the sample databases by a query code built from the card's names, shown, and never stored
or sent to a model; a key that isn't one of the question's own cards reads nothing."""

from __future__ import annotations

import html
import re

import sqlalchemy as sa

from othor.ai.models import ModelCall
from othor.core.tenancy import tenant_scope
from othor.ontology import service
from othor.ontology.models import Question
from othor.setup.models import ChatMessage
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

T = {"source": "sales_dw", "schema": "sales_dw", "table": "fact_order"}
CUST = {"source": "erp_db", "schema": "erp_db", "table": "customers"}


def _question(svc, cid) -> str:
    with svc.sf() as s, tenant_scope(cid):
        cust = next(dict(r.body) for r in service.active(s, ["breakdown"]) if r.key == "customer")
        reconcile = {
            "type": "rule",
            "key": "reconcile_preview_test",
            "body": {
                "kind": "reconcile",
                "text": "Every order in sales_dw is in erp_db within 30 days",
                "table": T,
                "to": {"source": "erp_db", "schema": "erp_db", "table": "invoices"},
                "on": [["order_no", "order_ref"]],
                "date": "order_date",
                "days": 30,
                "label": "orders",
            },
        }
        entity = {
            "type": "breakdown",
            "key": "customer",
            "body": {**cust, "entity": {"table": CUST, "status_column": "status"}},
        }
        q = Question(
            text="Which orders should match your invoices?",
            why="Without it, a Sweep test could not run last night.",
            fingerprint="preview-test",
            kind="needs",
            subject={
                "needs": "reconcile",
                "suggestions": [
                    {
                        "key": "rec",
                        "label": "Orders by order number",
                        "sub": "",
                        "change": reconcile,
                        "strong": True,
                    },
                    {
                        "key": "ent",
                        "label": "customers in erp_db",
                        "sub": "",
                        "change": entity,
                        "strong": False,
                    },
                ],
                "decide": {"pick": "rec", "why": "", "next": "", "href": ""},
            },
            quick_replies=[],
            origin={"rule": "detector_needs", "needs": "reconcile"},
            unblocks={"needs": "reconcile", "detectors": ["reconciliation"]},
            rank_key="0|",
        )
        s.add(q)
        s.commit()
        return str(q.id)


def test_a_pick_shows_real_rows_read_live_never_stored(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    qid = _question(svc, cid)
    page = env.client.get(h + "/home?panel=othor&subject=questions").text
    chat = page.split('id="othor-chat"', 1)[1]
    urls = [html.unescape(u) for u in re.findall(r'hx-get="(/othor/preview\?[^"]+)"', chat)]
    pick = f"/othor/preview?item=q%3A{qid}&option=rec"
    other = f"/othor/preview?item=q%3A{qid}&option=ent"
    assert pick in urls and other not in urls  # the suggestion's rows; no other options shown
    assert 'hx-trigger="load"' in chat  # the pick's rows load as the chat opens
    with svc.sf() as s, tenant_scope(cid):
        calls = s.scalar(sa.select(sa.func.count()).select_from(ModelCall))
    r = env.client.get(h + pick)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    rows = re.findall(r"<tr><td>([^<]+)</td>", r.text)
    assert 1 <= len(rows) <= 6 and "order no" in r.text and "Where this comes from" in r.text
    assert "read-only, and not kept" in r.text
    counts = env.client.get(h + other).text  # still readable directly (the no-model buttons)
    assert "active" in counts
    # never stored, never sent to a model
    with svc.sf() as s, tenant_scope(cid):
        stored = " ".join(str(m.proposal) + m.text for m in s.scalars(sa.select(ChatMessage)))
        assert not any(v in stored for v in rows)
        assert s.scalar(sa.select(sa.func.count()).select_from(ModelCall)) == calls


def test_only_the_questions_own_cards_are_read(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    qid = _question(svc, cid)
    for bad in (f"item=q%3A{qid}&option=nope", "item=q%3Anot-a-uuid&option=rec", "item=x"):
        r = env.client.get(h + "/othor/preview?" + bad)
        assert r.status_code == 200 and "<table" not in r.text


def test_pair_and_finding_previews_come_from_stored_results(night):  # noqa: F811
    """0.26.2 (items 5–6): "move together" draws the two numbers from the night's stored series;
    "which system is right" shows the records the night kept with the finding. Neither reads a
    source (no query is logged) and nothing is stored."""
    from othor.connectors.models import RunQuery

    env, h, cid, _rid, svc = night
    page = env.client.get(h + "/home?panel=othor&subject=questions").text
    chat = page.split('id="othor-chat"', 1)[1]
    urls = [html.unescape(u) for u in re.findall(r'hx-get="(/othor/preview\?[^"]+)"', chat)]
    pair = [u for u in urls if "option=pair" in u]
    rows = [u for u in urls if "rows=1" in u]
    assert pair and rows, urls
    with svc.sf() as s, tenant_scope(cid):
        logged = s.scalar(sa.select(sa.func.count()).select_from(RunQuery))
    got = env.client.get(h + pair[0])
    assert got.headers["cache-control"] == "no-store" and "<svg" in got.text
    assert "last 12 weeks" in got.text
    seen = ""
    for u in rows:
        r = env.client.get(h + u).text
        seen += r
        assert "The records in question" in r or r.strip() == '<div class="oc-preview"></div>'
    assert "<td>" in seen  # at least one finding kept its records
    with svc.sf() as s, tenant_scope(cid):
        assert s.scalar(sa.select(sa.func.count()).select_from(RunQuery)) == logged  # no read
        stored = " ".join(str(m.proposal) for m in s.scalars(sa.select(ChatMessage)))
        assert "<svg" not in stored
