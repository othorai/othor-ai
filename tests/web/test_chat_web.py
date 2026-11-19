# SPDX-License-Identifier: Apache-2.0
"""The fix-a-metric chats on the Metrics screen (M4b), without a model: the panel shows code's
quick replies and "Not available right now"; a quick reply gives a computed card; Apply changes
the draft. Jobs run inline, as the worker would."""

from __future__ import annotations

import re

import pytest
import sqlalchemy as sa

from othor.core.models import SetupState
from othor.core.tenancy import system_scope, tenant_scope
from othor.jobs.queue import enqueue
from othor.jobs.runner import WorkerContext, run_pending
from othor.setup import uploads
from othor.setup.models import BusinessMessage, ChatMessage, SetupDraft
from tests.fixtures.sample_company.generate import generate
from tests.fixtures.sample_company.uploads import write_uploads
from tests.integration import setup_world as w
from tests.web.conftest import Env, signup


@pytest.fixture(scope="module")
def sample():
    return generate()


@pytest.fixture
def need_sources(sample_sources_seeded):
    from othor.connectors.sql.scan import check_connection
    from tests.conftest import require_services
    from tests.integration.test_sql_connectors import my

    if check_connection(my("othor_ro")).message.startswith("Can't reach"):
        require_services("sample sources")


def _jobs(env: Env) -> None:
    svc = env.app.state.services  # type: ignore[attr-defined]
    run_pending(WorkerContext(svc.settings, svc.sf, svc.key, clock=svc.clock))


def _drafted(env: Env, sample, tmp_path, skip: tuple[str, ...] = ()) -> tuple[str, object]:
    signup(env)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, system_scope("test"):
        from othor.core.models import Company

        cid = s.scalars(sa.select(Company.id).where(Company.slug == "indigothread")).one()
    w.connect_sample(svc.sf, svc.key, cid, sample)
    files = [f for f in write_uploads(sample, tmp_path / "files") if f.name not in skip]
    with svc.sf() as s, tenant_scope(cid):
        for f in files:
            up, _ = uploads.add(
                s, svc.settings, svc.key, cid, filename=f.name, data=f.read_bytes(), actor=w.OWNER
            )
            enqueue(s, "upload", {"upload_id": str(up.id)})
        s.add(BusinessMessage(text=w.MESSAGE))
        s.scalars(sa.select(SetupState)).one().current_step = "metrics"
        s.commit()
    _jobs(env)
    with svc.sf() as s, tenant_scope(cid):
        enqueue(s, "draft", {"mode": "draft", "actor": w.OWNER})
        s.commit()
    _jobs(env)
    return env.host("indigothread"), cid


def _post(env: Env, h: str, path: str, data: dict):
    tok = env.csrf(h + "/setup/metrics")
    return env.client.post(h + path, data={"csrf": tok, **data}, follow_redirects=False)


def test_edit_chat_without_a_model(env: Env, sample, need_sources, tmp_path):
    h, cid = _drafted(env, sample, tmp_path)
    url = "/setup/metrics?panel=chat&purpose=edit&key=receivables_over_60"
    page = env.client.get(h + url).text
    # no choice buttons until Suggest edits (owner, 18 Nov 2026)
    assert "Fix with Othor AI" in page and "Change the threshold" not in page
    assert "Suggest edits" in page and "Later" not in page and "Drafts only." in page
    page = env.client.get(h + url + "&edit=1").text
    assert "Change the threshold" in page and "Not available right now" in page
    # the threshold quick reply (a small form) → a card computed by the worker
    i = re.search(
        r'name="reply" value="(\d+)">\s*<input type="hidden" name="patch" value="set_threshold"',
        page,
    )
    assert i, "threshold reply"
    r = _post(
        env,
        h,
        "/setup/metrics/receivables_over_60/chat/edit",
        {"reply": i.group(1), "patch": "set_threshold", "value": "90"},
    )
    assert r.status_code == 303
    assert "Working it out" in env.client.get(h + url).text
    _jobs(env)
    page = env.client.get(h + url).text
    assert "Threshold 60 → 90 days" in page and "Today · computed" in page
    mid = re.search(r"/chat/edit/apply/([0-9a-f-]{36})", page).group(1)
    r = _post(env, h, f"/setup/metrics/receivables_over_60/chat/edit/apply/{mid}", {})
    assert r.headers["location"] == "/setup/metrics"
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, tenant_scope(cid):
        row = s.scalars(sa.select(SetupDraft).where(SetupDraft.key == "receivables_over_60")).one()
        assert row.body["name"] == "Receivables over 90 days" and row.confidence == "high"


def test_where_chat_lists_candidates(env: Env, sample, need_sources, tmp_path):
    h, _ = _drafted(env, sample, tmp_path)
    url = "/setup/metrics?panel=chat&purpose=where&key=metres_dispatched"
    page = env.client.get(h + url).text
    assert "Use dispatch_mtrs" in page and "fact_dispatch.dispatch_mtrs" in page
    assert "We don’t track this" not in page  # with a suggestion, no other buttons


def test_apply_my_pick_drafts_and_applies_in_one_click(env: Env, sample, need_sources, tmp_path):
    """Chat 1.1 (owner, 18 Nov 2026): Where is it? opens with Othor AI's pick (code's best
    candidate) and one button, Apply: the worker computes its value and applies it;
    the other candidates aren't offered as buttons (Suggest edits takes the owner's own); no
    Later (owner, 18 Nov 2026)."""
    h, cid = _drafted(env, sample, tmp_path)
    url = "/setup/metrics?panel=chat&purpose=where&key=metres_dispatched"
    page = env.client.get(h + url).text
    assert "My suggestion" in page and ">Apply</button>" in page and "Suggest edits" in page
    assert "Later" not in page and 'id="oc-reply"' not in page  # two buttons only (owner)
    m = re.search(r'name="reply" value="(\d+)"><input type="hidden" name="apply" value="1">', page)
    assert m, "the pick's one-click form"
    r = _post(
        env, h, "/setup/metrics/metres_dispatched/chat/where", {"reply": m.group(1), "apply": "1"}
    )
    assert r.status_code == 303
    _jobs(env)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, tenant_scope(cid):
        row = s.scalars(sa.select(SetupDraft).where(SetupDraft.key == "metres_dispatched")).one()
        assert row.confidence == "high"  # applied: no second click
        msgs = list(s.scalars(sa.select(ChatMessage).where(ChatMessage.role == "ai")))
        assert any(x.applied for x in msgs)


def test_connect_the_order_tracker_from_metrics(env: Env, sample, need_sources, tmp_path):
    """2nca / 2ncb: sign in, choose the spreadsheet and tabs, check Open orders, then the
    Review shows Order book value redrafted with today's value and what else was added."""
    from othor.connectors.models import SheetTab
    from tests.fixtures.sample_company.load import write_sheets

    write_sheets(sample, tmp_path / "sample" / "sheets")
    h, cid = _drafted(env, sample, tmp_path)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, tenant_scope(cid):
        s.scalars(sa.select(BusinessMessage)).one().data_lives = ["Order tracker"]
        s.add(
            SetupDraft(
                kind="metric",
                key="order_book_value",
                body={
                    "name": "Order book value",
                    "status": "needs_source",
                    "kind": "snapshot",
                    "grain": "day",
                    "description": "Open orders not yet invoiced",
                    "needs": "Order tracker FY27 · Open orders (Google Sheets)",
                },
                origin={"by": "model"},
                state="needs_source",
                confidence="check",
                position=0,
            )
        )
        s.commit()
    page = env.client.get(h + "/setup/metrics?panel=decisions&d=order_book_value").text  # #32
    assert "Connect the Order tracker" in page
    url = "/setup/metrics?panel=connect&key=order_book_value&kind=gsheets"
    page = env.client.get(h + url).text
    assert "For Order book value · read-only" in page and "Sign in with Google" in page
    r = _post(env, h, "/sources/google/start", {"setup": "1", "for": "order_book_value"})
    back = r.headers["location"]
    assert back.startswith(url + "&sid=")
    sid = back.split("sid=")[1]
    assert "Signed in as uday@indigothread.example" in env.client.get(h + back).text
    r = _post(env, h, f"/sources/{sid}/files", {"file": "sample-order-tracker-fy27"})
    assert r.headers["location"] == back
    _jobs(env)
    page = env.client.get(h + back).text
    assert re.search(r"Check 1 header, then scan \d+ tabs?", page)
    with svc.sf() as s, tenant_scope(cid):
        tabs = {t.tab_name: str(t.id) for t in s.scalars(sa.select(SheetTab))}
    r = _post(env, h, f"/sources/{sid}/tabs", {"tab": [tabs["Open orders"], tabs["Deals"]]})
    check = r.headers["location"]
    r = _post(env, h, check + "/confirm", {})
    assert r.headers["location"] == back
    assert "Redrafting from the new tabs" in env.client.get(h + back).text
    _jobs(env)
    page = env.client.get(h + back).text
    assert "The Order tracker FY27 is connected." in page or "is connected." in page
    assert "Order book value" in page and "Today · computed" in page
    assert "Also added to your metrics" in page and "Back to Metrics" in page


def test_redraft_with_the_data_dictionary(env: Env, sample, need_sources, tmp_path):
    """2ne → 2nf: upload in place, see what the document changed, then Draft 2 on Metrics."""
    from tests.fixtures.sample_company.uploads import DICTIONARY, write_dictionary

    h, _ = _drafted(env, sample, tmp_path, skip=(DICTIONARY,))
    page = env.client.get(h + "/setup/metrics").text
    assert "Not quite right?" in page and "PDF · DOCX · XLSX · MD" in page
    (tmp_path / "doc").mkdir()
    path = write_dictionary(tmp_path / "doc" / DICTIONARY)
    tok = env.csrf(h + "/setup/metrics")
    r = env.client.post(
        h + "/setup/metrics/document",
        data={"csrf": tok},
        files={"file": (DICTIONARY, path.read_bytes(), "application/pdf")},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/setup/metrics/document"
    page = env.client.get(h + "/setup/metrics/document").text
    assert "Redrafting your metrics" in page and DICTIONARY in page
    _jobs(env)
    page = env.client.get(h + "/setup/metrics/document").text
    # M11c (owner, 8 Nov): a simple loading screen; done, it goes back to Metrics by itself
    assert "Review metrics" in page and 'data-panel-go="/setup/metrics"' in page
    page = env.client.get(h + "/setup/metrics").text
    assert "Upload another" in page and "metrics changed" in page and "See what changed" in page
    assert "Confirm 11 and continue" in page or "and continue" in page
    # what the document changed: a tag and the citation in Metrics to confirm
    panel = env.client.get(h + "/setup/metrics?panel=confirm").text
    assert "Changed" in panel and f"{DICTIONARY} · p. " in panel


def test_document_of_the_wrong_kind_is_refused(env: Env, sample, need_sources, tmp_path):
    h, _ = _drafted(env, sample, tmp_path)
    tok = env.csrf(h + "/setup/metrics")
    r = env.client.post(
        h + "/setup/metrics/document",
        data={"csrf": tok},
        files={"file": ("photo.png", b"x", "image/png")},
        follow_redirects=False,
    )
    page = env.client.get(h + r.headers["location"]).text
    assert "Upload a PDF, Word, Excel or Markdown file." in page


def test_ontology_draft_panel(env: Env, sample, need_sources, tmp_path):
    """2n0: read-only; the four sections, what needs the owner, where it was built from."""
    h, _ = _drafted(env, sample, tmp_path)
    page = env.client.get(h + "/setup/metrics").text
    assert "Business Model draft" in page
    page = env.client.get(h + "/setup/metrics?panel=ontology").text
    for text in (
        "Business Model draft",
        "Becomes Business Model v1 when you confirm metrics",
        "Draft · not live",
        "The business",
        "Goals",
        "Numbers",
        "Names and words",
        "Known patterns",
        "Your message",
        "Built from 5 files · your message · erp_db · sales_dw",
    ):
        assert text in page, text
    assert "Needs you · " in page and "Fix in Metrics →" in page
    assert '<form method="post"' not in page.split('aria-label="Business Model draft"')[1]


def test_after_setup_review_confirm_and_fix_chat(env: Env, sample, need_sources, tmp_path):
    """6h / 6ha: connect the Order tracker from Sources after setup; the review lists its
    drafts with today's value in the Metrics to confirm panel (M11c G); Confirm stages a row,
    "Add N to the Ontology" writes one version and one event per metric, Undo takes it back."""
    from othor.connectors.models import SheetTab
    from othor.core.models import Company, Event
    from othor.ontology import service as ontology
    from othor.setup import confirm
    from tests.fixtures.sample_company.load import write_sheets

    write_sheets(sample, tmp_path / "sample" / "sheets")
    h, cid = _drafted(env, sample, tmp_path)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, tenant_scope(cid):
        for r in s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "metric")):
            if r.confidence == "conflict":
                confirm.choose(s, r, "source", actor=w.OWNER)
        confirm.confirm_setup(s, s.get(Company, cid), actor=w.OWNER, today=w.NOW.date())
        s.scalars(sa.select(SetupState)).one().current_step = "done"
        s.commit()
    r = _post(env, h, "/sources/google/start", {})
    sid = r.headers["location"].split("s=")[1]
    _post(env, h, f"/sources/{sid}/files", {"file": "sample-order-tracker-fy27"})
    _jobs(env)
    with svc.sf() as s, tenant_scope(cid):
        tabs = {t.tab_name: str(t.id) for t in s.scalars(sa.select(SheetTab))}
    r = _post(env, h, f"/sources/{sid}/tabs", {"tab": [tabs["Open orders"], tabs["Deals"]]})
    r = _post(env, h, r.headers["location"] + "/confirm", {})
    assert r.headers["location"] == f"/sources?panel=manage&s={sid}"
    _jobs(env)
    page = env.client.get(f"{h}/sources?panel=review&s={sid}").text
    # M11c G: the same Metrics to confirm panel as onboarding, with today's computed values
    assert "Metrics to confirm" in page and "You can change any of these later" in page
    assert "Step 5 of" not in page and "Choose data" not in page  # no stepper
    keys = re.findall(rf'action="/sources/{sid}/confirm-metrics/([a-z0-9_]+)/stage"', page)
    assert keys, "rows to confirm"
    # Fix with AI (Edit) opens the fix chat beside the review
    url = f"/sources?panel=chat&s={sid}&key={keys[0]}&purpose=fix"
    assert url.replace("&", "&amp;") in page
    page = env.client.get(h + url + "&edit=1").text  # the reply box opens on Suggest edits
    assert "Fix with Othor AI" in page and "Drafts only." in page
    assert f'name="back" value="{url.replace("&", "&amp;")}"' in page
    with svc.sf() as s, tenant_scope(cid):
        before = ontology.current_number(s)
        n_events = s.scalar(sa.select(sa.func.count()).select_from(Event))
    # Confirm one row: staged only, the Ontology doesn't change; one event
    r = _post(env, h, f"/sources/{sid}/confirm-metrics/{keys[0]}/stage", {})
    assert r.status_code == 303
    with svc.sf() as s, tenant_scope(cid):
        assert ontology.current_number(s) == before
        assert keys[0] not in {x.key for x in ontology.active(s, {"metric"})}
        evs = list(s.scalars(sa.select(Event)))
        assert len(evs) == n_events + 1
        assert [e.after for e in evs if e.action == "source.metric_staged"] == [{"key": keys[0]}]
    # Undo on the row unstages it
    _post(env, h, f"/sources/{sid}/confirm-metrics/unstage", {"key": keys[0]})
    page = env.client.get(f"{h}/sources?panel=review&s={sid}").text
    assert f"/sources/{sid}/confirm-metrics/{keys[0]}/stage" in page
    # Confirm the N remaining stages all; the footer adds them in one version
    _post(env, h, f"/sources/{sid}/confirm-metrics/stage-all", {"key": keys})
    page = env.client.get(f"{h}/sources?panel=review&s={sid}").text
    assert f"Add {len(keys)} to the Business Model" in page
    r = _post(env, h, f"/sources/{sid}/confirm-metrics", {})
    assert r.headers["location"].startswith(f"/sources?panel=review&s={sid}&added=")
    with svc.sf() as s, tenant_scope(cid):
        assert ontology.current_number(s) == before + 1
        live = {x.key for x in ontology.active(s, {"metric"})}
        assert set(keys) <= live
        added = [e for e in s.scalars(sa.select(Event)) if e.action == "source.metric_added"]
        assert sorted(e.after["key"] for e in added) == sorted(keys)
    page = env.client.get(h + r.headers["location"]).text
    assert f"{len(keys)} metric" in page and "added to the Business Model" in page
    assert f"/sources/{sid}/confirm-metrics/undo" in page
    # The toast's Undo takes the version back and the rows return to the panel
    _post(env, h, f"/sources/{sid}/confirm-metrics/undo", {"version": str(before + 1)})
    with svc.sf() as s, tenant_scope(cid):
        assert ontology.current_number(s) == before + 2
        assert not set(keys) & {x.key for x in ontology.active(s, {"metric"})}
    page = env.client.get(f"{h}/sources?panel=review&s={sid}").text
    assert f"/sources/{sid}/confirm-metrics/{keys[0]}/stage" in page


def test_tab_fix_units_without_a_model(env: Env, tmp_path):
    """10c: Fix with Othor AI without the model: the units form re-reads Open orders as ₹ crore;
    the line to Othor AI says it's not available."""
    from othor.connectors.models import SheetTab
    from tests.fixtures.sample_company.load import write_sheets
    from tests.web.test_sources_web import _db, _host, _to_sources
    from tests.web.test_sources_web import _post as post_page

    write_sheets(generate(), tmp_path / "sample" / "sheets")
    _to_sources(env)
    h = _host(env)
    r = post_page(env, "/sources/google/start", {"setup": "1"}, page="/setup/sources/sheets")
    sid = r.headers["location"].split("sid=")[1]
    post_page(
        env,
        f"/sources/{sid}/files",
        {"file": "sample-order-tracker-fy27"},
        page="/setup/sources/sheets",
    )
    _jobs(env)
    tabs = {t.tab_name: t for t in _db(env, SheetTab)}
    oo = tabs["Open orders"]
    check = f"/setup/sources/{sid}/check/{oo.id}"
    page = env.client.get(h + check).text
    assert "Fix with Othor AI" in page and "values are in ₹ crore" in page
    page = env.client.get(h + check + "?fix=1").text
    assert "Values are in another unit" not in page and "Suggest edits" in page
    page = env.client.get(h + check + "?fix=1&edit=1").text
    assert "Values are in another unit" in page and "Not available right now" in page
    assert "Drafts only." in page
    r = post_page(
        env,
        check + "/shape",
        {"header_row": "3", "last_row": "", "unit_col": "Value (₹ L)", "unit": "crore"},
        page=check,
    )
    assert r.headers["location"] == check + "?fix=1"
    _jobs(env)
    page = env.client.get(h + check).text
    assert "Read as ₹ crore" in page
    tab = _db(env, SheetTab, SheetTab.id == oo.id)[0]
    assert tab.units["Value (₹ L)"] == "crore"


def test_sources_suggested_from_waiting_metrics(env: Env, sample, need_sources, tmp_path):
    """6c Suggested · N and 6e "Suggested from your Ontology": a source a metric waits on."""
    h, cid = _drafted(env, sample, tmp_path)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, tenant_scope(cid):
        s.scalars(sa.select(BusinessMessage)).one().data_lives = ["Order tracker"]
        s.add(
            SetupDraft(
                kind="metric",
                key="order_book_value",
                body={
                    "name": "Order book value",
                    "status": "needs_source",
                    "needs": "Order tracker FY27 · Open orders (Google Sheets)",
                },
                origin={"by": "model"},
                state="needs_source",
                confidence="check",
            )
        )
        s.scalars(sa.select(SetupState)).one().current_step = "done"
        s.commit()
    page = env.client.get(h + "/sources").text
    assert "Suggested · 1" in page and "Order book value is waiting for it" in page
    page = env.client.get(h + "/sources?tab=suggested").text
    assert "1 metric waiting" in page and 'href="/sources?panel=sheets"' in page
    page = env.client.get(h + "/sources?panel=add").text
    assert (
        "Suggested from your Business Model" in page
        and "1 metric waiting · Order book value" in page
    )
