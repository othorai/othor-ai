# SPDX-License-Identifier: Apache-2.0
"""The fix-a-metric chats (M4b: 2nb, 2nc, 2nd): every card's value is computed by SQL from a typed
patch and equals an independent check; without a model the quick replies and Apply still work."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pandas as pd
import pytest
import sqlalchemy as sa

from othor.ai.gateway import ProviderReply
from othor.ai.models import ModelCall
from othor.core.tenancy import tenant_scope
from othor.jobs.runner import run_pending
from othor.ontology.compiler.catalog import load_catalog
from othor.setup import chat
from othor.setup.models import ChatMessage, SetupDraft
from othor.setup.patches import Patch
from tests.fixtures.sample_company import measures
from tests.fixtures.sample_company.generate import generate
from tests.integration import setup_world as w

DAY = dt.date(2026, 11, 3)


@pytest.fixture(scope="module")
def sample():
    return generate()


@pytest.fixture
def need_sources(db_url, sample_sources_seeded):
    if not db_url.startswith("sqlite"):
        pytest.skip("one metadata DB is enough")
    from othor.connectors.sql.scan import check_connection
    from tests.conftest import require_services
    from tests.integration.test_sql_connectors import my

    if check_connection(my("othor_ro")).message.startswith("Can't reach"):
        require_services("sample sources")


def _ready(session_factory, two_companies, tmp_path, sample, **kw):
    cid, _ = two_companies
    ctx = w.context(session_factory, tmp_path, **kw)
    w.build(ctx, cid, sample, tmp_path)
    w.draft(ctx, cid)
    return ctx, cid


def _turn(ctx, cid, key, purpose, *, patch=None, text=""):
    with ctx.sf() as s, tenant_scope(cid):
        t = chat.thread_for(s, key, purpose)
        m = chat.add_turn(
            s, t, text=text or (patch.kind if patch else ""), patch=patch, actor=w.OWNER
        )
        from othor.jobs.queue import enqueue

        enqueue(s, "chat", {"message_id": str(m.id)})
        s.commit()
        tid = t.id
    run_pending(ctx)
    with ctx.sf() as s, tenant_scope(cid):
        return s.scalars(
            sa.select(ChatMessage)
            .where(ChatMessage.thread_id == tid)
            .order_by(ChatMessage.created_at.desc())
        ).first()


def test_units_and_threshold_cards_equal_sql(
    session_factory, two_companies, tmp_path, sample, need_sources
):
    ctx, cid = _ready(session_factory, two_companies, tmp_path, sample)
    # 2nb: metres dispatched with Coimbatore yards converted
    ai = _turn(
        ctx,
        cid,
        "metres_dispatched",
        "where",
        patch=Patch(
            "convert_units",
            {
                "column": "dispatch_mtrs",
                "when": {"column": "uom", "value": "YD"},
                "factor": "0.9144",
                "to_unit": "metres",
            },
        ),
    )
    assert ai.role == "ai" and ai.proposal["card"]["lines"] == [
        "Convert dispatch_mtrs where uom = YD × 0.9144 (to metres)"
    ]
    d = sample.dw["sales_dw"]["fact_dispatch"]
    day = d[[x == DAY for x in d["dispatch_date"]]]
    want = Decimal(str(round(float(measures._metres(day).sum()), 4)))
    assert abs(Decimal(ai.proposal["value"]["value"]) - want) < Decimal("0.01")

    # 2nd: receivables over 90 days instead of 60
    ai = _turn(ctx, cid, "receivables_over_60", "edit", patch=Patch("set_threshold", {"value": 90}))
    assert ai.proposal["card"]["lines"] == ["Threshold 60 → 90 days"]
    o = measures._open_receivables(sample, pd.Timestamp(DAY))
    age = (pd.Timestamp(DAY) - pd.to_datetime(o["due_date"])).dt.days
    want = Decimal(str(round(float(o.loc[age > 90, "open_amount"].sum()), 2)))
    assert abs(Decimal(ai.proposal["value"]["value"]) - want) <= Decimal("0.01")

    # Apply: the draft takes the definition, the conversion rule becomes a setup record
    with ctx.sf() as s, tenant_scope(cid):
        row = chat.find_row(s, "receivables_over_60")
        chat.apply_card(s, row, ai.proposal["card"], actor=w.OWNER)
        s.commit()
        assert row.body["name"] == "Receivables over 90 days" and row.confidence == "high"


def test_quick_replies_find_the_column(
    session_factory, two_companies, tmp_path, sample, need_sources
):
    ctx, cid = _ready(session_factory, two_companies, tmp_path, sample)
    with ctx.sf() as s, tenant_scope(cid):
        row = s.scalars(sa.select(SetupDraft).where(SetupDraft.key == "metres_dispatched")).one()
        replies = chat.quick_replies(row, "where", load_catalog(s), {})
    labels = [r.label for r in replies]
    assert labels[0] == "Use dispatch_mtrs" and "We don’t track this" in labels


def test_typed_text_without_a_model(session_factory, two_companies, tmp_path, sample, need_sources):
    ctx, cid = _ready(session_factory, two_companies, tmp_path, sample)
    msg = _turn(ctx, cid, "net_revenue", "edit", text="Leave out government customers")
    assert msg.role == "system" and msg.text == chat.NOT_AVAILABLE
    with ctx.sf() as s, tenant_scope(cid):
        assert s.scalars(sa.select(ModelCall)).all() == []


class PatchingClaude:
    name, configured = "anthropic", True

    def __init__(self, text):
        self.text = text

    def call(self, system, messages, schema, max_tokens=0):
        out = {"text": self.text, "patch": {"kind": "set_threshold", "args": {"value": "90"}}}
        return ProviderReply(json.dumps(out), "claude-opus-5-5", 10, 10)


def test_model_patch_is_computed_and_its_numbers_checked(
    session_factory, two_companies, tmp_path, sample, need_sources
):
    _, cid = _ready(session_factory, two_companies, tmp_path, sample)  # drafted by rules
    ctx = w.context(
        session_factory,
        tmp_path,
        provider="anthropic",
        api_key="sk-ant-test",
        gateway_provider=PatchingClaude("Here is 90 days. That would be ₹12,34,567 today."),
    )
    ai = _turn(ctx, cid, "receivables_over_60", "edit", text="Make it 90 days")
    assert ai.proposal["card"]["lines"] == ["Threshold 60 → 90 days"]
    assert ai.proposal["value"]["state"] == "ok"
    assert "12,34,567" not in ai.text  # a figure Claude wrote is dropped; the card's is computed


def test_a_model_patch_with_a_number_nobody_gave_is_dropped(
    session_factory, two_companies, tmp_path, sample, need_sources
):
    _, cid = _ready(session_factory, two_companies, tmp_path, sample)
    ctx = w.context(
        session_factory,
        tmp_path,
        provider="anthropic",
        api_key="sk-ant-test",
        gateway_provider=PatchingClaude("A longer threshold."),
    )
    ai = _turn(ctx, cid, "receivables_over_60", "edit", text="Make it longer")
    assert ai.proposal is None  # 90 is in no input: the patch is not shown or computed
