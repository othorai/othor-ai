# SPDX-License-Identifier: Apache-2.0
"""Accept (docs/10 M4, decision 4): without a model the sample company completes setup to Confirm.

Rule drafts (Low · Needs confirmation) with values computed by SQL, five rule-based discoveries,
the GST conflict that blocks Confirm until Choose, then Confirm = Ontology version 1 and the first
spec. No model_call row is written. Run once with provider "template" and once with "anthropic"
and no key."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from othor.ai.models import ModelCall
from othor.connectors.models import RunQuery
from othor.connectors.sql.query_log import identifiers
from othor.core.models import Company, SetupState
from othor.core.tenancy import tenant_scope
from othor.engine.models import Run
from othor.jobs.runner import run_pending
from othor.ontology import service
from othor.ontology.models import OntologyVersion, Spec
from othor.setup import confirm, drafts
from othor.setup.models import SetupDraft, Upload
from tests.fixtures.sample_company.generate import generate
from tests.integration import setup_world as w


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


@pytest.mark.parametrize("provider", ["template", "anthropic"])
def test_no_model_flow_to_confirm(
    provider, session_factory, two_companies, tmp_path, sample, need_sources
):
    cid, _ = two_companies
    ctx = w.context(session_factory, tmp_path, provider=provider, api_key="")
    w.build(ctx, cid, sample, tmp_path)
    with ctx.sf() as s, tenant_scope(cid):
        ups = {u.filename: u for u in s.scalars(sa.select(Upload))}
        assert ups["MIS-Weekly-27Oct.xlsx"].status == "read"
        assert ups["Sales-dashboard-Oct.png"].status == "needs_model"
        assert ups["sales_dw data dictionary.pdf"].status == "read_as_text"
    w.draft(ctx, cid)

    with ctx.sf() as s, tenant_scope(cid):
        meta = s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "meta")).one()
        assert meta.body["by"] == "rules" and meta.body["state"] == "done"
        rows = {r.key: r for r in drafts.stored(s)}
        assert {"net_revenue", "receivables_over_60", "metres_dispatched"} <= set(rows)
        assert len(rows) <= 15
        for r in rows.values():
            assert r.origin["by"] == "rules" and r.origin["rule"].startswith("Rule:")
            assert r.state == "needs_confirmation"
            assert r.confidence in ("low", "conflict"), (r.key, r.confidence, r.note)
            assert r.value and r.value["state"] == "ok", (r.key, r.value)
        nr = rows["net_revenue"]
        assert nr.confidence == "conflict"
        opts = nr.value["conflict"]["options"]
        assert opts[0]["value"] == nr.value["value"]  # erp_db, computed by SQL
        assert opts[1]["label"] == "Including GST"
        found = drafts.stored(s, "discovery")
        assert {d.body["type"] for d in found} == {
            "overlap",
            "currency",
            "dimension",
            "pattern",
            "gap",
        }
        q = s.scalars(sa.select(RunQuery).where(RunQuery.purpose == "draft")).all()
        assert q and all(x.source_id for x in q)
        personal = {
            "phone",
            "email",
            "pan",
            "aadhaar",
            "salary",
            "bank_account",
            "address",
            "dob",
            "employee_name",
            "contact_name",
            "name",
        }
        for x in q:
            assert not (identifiers(x.sql) & personal), x.sql
        company = s.get(Company, cid)
        with pytest.raises(confirm.NotReady):
            confirm.confirm_setup(s, company, actor=w.OWNER, today=w.NOW.date())
        confirm.choose(s, nr, "source", actor=w.OWNER)
        s.commit()
    w.draft(ctx, cid, "values")

    with ctx.sf() as s, tenant_scope(cid):
        nr = s.scalars(sa.select(SetupDraft).where(SetupDraft.key == "net_revenue")).one()
        assert nr.confidence == "high" and nr.value["state"] == "ok"
        assert "conflict" not in nr.value
        v = confirm.confirm_setup(s, s.get(Company, cid), actor=w.OWNER, today=w.NOW.date())
        s.commit()
        assert v.number == 1
        assert s.scalars(sa.select(OntologyVersion)).one().number == 1
        spec = s.scalars(sa.select(Spec)).one()
        assert "net_revenue" in spec.compiled["metrics"]
        assert service.confirmed(s)
        recs = {(r.type, r.key) for r in service.active(s)}
        assert ("rule", "revenue_book_of_record") in recs
        assert ("vocabulary", "plant_codes") in recs
        assert ("known_pattern", "quarter_end_push") in recs
        assert ("business_fact", "business_message") in recs
        assert all(r.frozen for r in drafts.stored(s))
        assert s.scalars(sa.select(SetupState)).one().current_step == "first-run"
        assert s.scalars(sa.select(ModelCall)).all() == []
    # dry run #0001 (2o, moved here from M4): the run needs no model either
    run_pending(ctx)
    with ctx.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run)).one()
        assert (run.number, run.kind, run.status) == (1, "dry", "done")
        assert run.run_hash and run.summary["metrics"] > 0
        assert s.scalars(sa.select(ModelCall)).all() == []


KEY = "sk-ant-test-0123456789abcdefSECRET"


def _bodies(s):
    import json

    return {r.key: json.dumps(r.body, sort_keys=True) for r in drafts.stored(s)}


def test_model_drafts_are_computed_and_checked(
    session_factory, two_companies, tmp_path, sample, need_sources
):
    from tests.fakes_model import FakeClaude

    cid, _ = two_companies
    fake = FakeClaude()
    ctx = w.context(
        session_factory, tmp_path, provider="anthropic", api_key=KEY, gateway_provider=fake
    )
    w.build(ctx, cid, sample, tmp_path)
    w.draft(ctx, cid)
    with ctx.sf() as s, tenant_scope(cid):
        meta = s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "meta")).one()
        assert meta.body["by"] == "model"
        rows = {r.key: r for r in drafts.stored(s)}
        assert rows["metres_dispatched"].value["state"] == "ok"
        assert rows["bad_column"].confidence == "check"
        assert rows["bad_column"].note.startswith("Couldn't compute this")
        assert rows["headcount"].state == "excluded_table"
        # the model saw names and kinds only: no personal column, no row values
        sent = str(fake.sent)
        for col in ("phone", "bank_account", "pan"):
            assert f'"column": "{col}"' not in sent
        calls = s.scalars(sa.select(ModelCall)).all()
        assert [c.prompt_id for c in calls] == ["draft_metrics"] or {
            c.prompt_id for c in calls
        } <= {"draft_metrics", "read_upload"}


def test_redraft_leaves_confirmed_metrics_byte_identical(
    session_factory, two_companies, tmp_path, sample, need_sources
):
    from tests.fakes_model import FakeClaude

    cid, _ = two_companies
    ctx = w.context(session_factory, tmp_path, provider="template")
    w.build(ctx, cid, sample, tmp_path)
    w.draft(ctx, cid)  # no model: rule drafts
    with ctx.sf() as s, tenant_scope(cid):
        for key in ("receivables_over_60", "metres_dispatched"):
            row = s.scalars(sa.select(SetupDraft).where(SetupDraft.key == key)).one()
            drafts.confirm_row(s, row, actor=w.OWNER)
        s.commit()
        before = _bodies(s)
    # the model comes back
    ctx2 = w.context(
        session_factory, tmp_path, provider="anthropic", api_key=KEY, gateway_provider=FakeClaude()
    )
    w.draft(ctx2, cid, "redraft")
    with ctx2.sf() as s, tenant_scope(cid):
        after = _bodies(s)
        for key in ("receivables_over_60", "metres_dispatched"):
            assert after[key] == before[key]
        nr = s.scalars(sa.select(SetupDraft).where(SetupDraft.key == "net_revenue")).one()
        assert nr.changed and nr.prev_body is not None  # a diff to accept (2nf)
        drafts.accept_change(s, nr, actor=w.OWNER, accept=False)
        assert _bodies(s)["net_revenue"] == before["net_revenue"]


def test_the_key_never_leaks(
    session_factory, two_companies, tmp_path, sample, need_sources, caplog
):
    """A provider failure whose message carries the key: the key is in no log line, no
    model_call row, no run_query row, no job error."""
    from othor.ai.models import ModelCall
    from othor.jobs.models import Job
    from tests.fakes_model import FakeClaude

    cid, _ = two_companies
    boom = RuntimeError(f"401 invalid x-api-key {KEY}")
    ctx = w.context(
        session_factory,
        tmp_path,
        provider="anthropic",
        api_key=KEY,
        gateway_provider=FakeClaude(fail=boom),
    )
    caplog.set_level("DEBUG")
    w.build(ctx, cid, sample, tmp_path)
    w.draft(ctx, cid)
    assert KEY not in caplog.text
    with ctx.sf() as s, tenant_scope(cid):
        meta = s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "meta")).one()
        assert meta.body["by"] == "rules"  # fell back
        for c in s.scalars(sa.select(ModelCall)):
            assert KEY not in str(c.input) and KEY not in str(c.output or "")
        for q in s.scalars(sa.select(RunQuery)):
            assert KEY not in q.sql and KEY not in str(q.params)
        for j in s.scalars(sa.select(Job)):
            assert KEY not in str(j.error or "") and KEY not in str(j.payload)


def test_a_draft_that_does_not_compile_is_kept_as_needs_source(
    session_factory, two_companies, tmp_path, sample, need_sources
):
    from tests.fakes_model import FakeClaude

    cid, _ = two_companies
    ctx = w.context(
        session_factory, tmp_path, provider="anthropic", api_key=KEY, gateway_provider=FakeClaude()
    )
    w.build(ctx, cid, sample, tmp_path)
    w.draft(ctx, cid)
    with ctx.sf() as s, tenant_scope(cid):
        nr = s.scalars(sa.select(SetupDraft).where(SetupDraft.key == "net_revenue")).one()
        if nr.confidence == "conflict":
            confirm.choose(s, nr, "source", actor=w.OWNER)
        s.commit()
    w.draft(ctx, cid, "values")
    with ctx.sf() as s, tenant_scope(cid):
        confirm.confirm_setup(s, s.get(Company, cid), actor=w.OWNER, today=w.NOW.date())
        s.commit()
        bad = next(r for r in service.active(s, {"metric"}) if r.key == "bad_column")
        assert bad.body["status"] == "needs_source"
        assert s.scalars(sa.select(Spec)).one()


def test_confirm_waits_for_redrafted_definitions(
    session_factory, two_companies, tmp_path, sample, need_sources
):
    from tests.fakes_model import FakeClaude

    cid, _ = two_companies
    ctx = w.context(session_factory, tmp_path, provider="template")
    w.build(ctx, cid, sample, tmp_path)
    w.draft(ctx, cid)
    ctx2 = w.context(
        session_factory, tmp_path, provider="anthropic", api_key=KEY, gateway_provider=FakeClaude()
    )
    w.draft(ctx2, cid, "redraft")
    with ctx2.sf() as s, tenant_scope(cid):
        for r in drafts.stored(s):
            if r.confidence == "conflict":
                confirm.choose(s, r, "source", actor=w.OWNER)
        s.commit()
        assert any(r.changed for r in drafts.stored(s))
        with pytest.raises(confirm.NotReady, match="redrafted"):
            confirm.confirm_setup(s, s.get(Company, cid), actor=w.OWNER, today=w.NOW.date())


def test_redraft_sets_aside_rule_rows_and_they_come_back(
    session_factory, two_companies, tmp_path, sample, need_sources
):
    """A model redraft replaces the rule-made rows it doesn't return: Skipped, never deleted,
    at most 15 drafted plus the owner's; Bring back undoes it."""
    from othor.setup.models import SetupDraft as SD
    from tests.fakes_model import FakeClaude

    cid, _ = two_companies
    ctx = w.context(session_factory, tmp_path, provider="template")
    w.build(ctx, cid, sample, tmp_path)
    w.draft(ctx, cid)
    with ctx.sf() as s, tenant_scope(cid):
        by_rules = {r.key for r in drafts.stored(s) if (r.origin or {}).get("by") == "rules"}
        s.add(
            SD(
                kind="metric",
                key="mine",
                body={"name": "Mine", "status": "needs_source"},
                origin={"by": "owner"},
                state="needs_source",
            )
        )
        s.commit()
    ctx2 = w.context(
        session_factory, tmp_path, provider="anthropic", api_key=KEY, gateway_provider=FakeClaude()
    )
    w.draft(ctx2, cid, "redraft")
    with ctx2.sf() as s, tenant_scope(cid):
        rows = {r.key: r for r in drafts.stored(s)}
        aside = {k for k, r in rows.items() if (r.origin or {}).get("set_aside")}
        assert aside and aside <= by_rules and all(rows[k].state == "skipped" for k in aside)
        live = [r for r in rows.values() if r.state != "skipped" and not drafts.locked(r)]
        assert len(live) <= drafts.REDRAFT_CAP
        assert rows["mine"].state == "needs_source"  # the owner's row is untouched
        assert drafts.bring_back(s, actor=w.OWNER) == len(aside)
        s.commit()
        assert all(rows[k].state == "needs_confirmation" for k in aside)
