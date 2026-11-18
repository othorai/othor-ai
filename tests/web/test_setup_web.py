# SPDX-License-Identifier: Apache-2.0
"""Setup screens end to end without a model (docs/10 M4 accept, decision 4): Upload (2b/2c) →
Business (2d) → Sources → Metrics (2m → 2n) → Choose (2na) → Confirm → step 6. Jobs run inline.
No model_call row is written; the API key never reaches a page or a log."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from othor.ai.models import ModelCall
from othor.core import fmt
from othor.core.models import Company
from othor.core.tenancy import system_scope, tenant_scope
from othor.jobs.runner import WorkerContext, run_pending
from othor.ontology.models import OntologyVersion, Spec
from othor.setup.models import SetupDraft, Upload
from tests.fixtures.sample_company.generate import generate
from tests.fixtures.sample_company.uploads import write_uploads
from tests.integration import setup_world as w
from tests.web.conftest import Env, signup

KEY = "sk-ant-test-0123456789abcdefSECRET"


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


def _h(env: Env) -> str:
    return env.host("indigothread")


def _jobs(env: Env) -> int:
    svc = env.app.state.services  # type: ignore[attr-defined]
    return run_pending(WorkerContext(svc.settings, svc.sf, svc.key, clock=svc.clock))


def _post(env: Env, path: str, page: str, data: dict | None = None, **kw):
    h = _h(env)
    d = {"csrf": env.csrf(h + page), **(data or {})}
    return env.client.post(h + path, data=d, follow_redirects=False, **kw)


def _cid(env: Env):
    sf = env.app.state.services.sf  # type: ignore[attr-defined]
    with sf() as s, system_scope("test"):
        return s.scalars(sa.select(Company.id).where(Company.slug == "indigothread")).one()


def _count(env: Env, model) -> int:
    sf = env.app.state.services.sf  # type: ignore[attr-defined]
    with sf() as s, system_scope("test"):
        return int(s.scalar(sa.select(sa.func.count()).select_from(model)) or 0)


def test_no_model_setup_flow(env: Env, sample, tmp_path, need_sources):
    signup(env)
    h = _h(env)
    # 2b: empty
    r = env.client.get(f"{h}/setup/upload")
    assert r.status_code == 200 and "Upload what you already have." in r.text
    assert "Upload your files" in r.text and "Browse files" in r.text and "Board deck" in r.text
    assert (
        "Step 2 of 6" in r.text and "Next: Business" in r.text
    )  # M11c: the step bar in the column
    assert "No files? Just continue" in r.text

    # 2c: five files, read by code (the screenshot needs the model)
    files = write_uploads(sample, tmp_path / "files")
    tok = env.csrf(f"{h}/setup/upload")
    r = env.client.post(
        f"{h}/setup/upload/files",
        data={"csrf": tok},
        files=[("files", (f.name, f.read_bytes())) for f in files],
        follow_redirects=False,
    )
    assert r.status_code == 200 and "Reading…" in r.text
    _jobs(env)
    r = env.client.get(f"{h}/setup/upload")
    assert "Added · 5 · 4 read" in r.text
    assert "metrics, 4 tabs" in r.text
    assert "Can&#39;t be read without the model" in r.text
    assert "Read as text · drafted by rules" in r.text
    assert _post(env, "/setup/upload/continue", "/setup/upload").headers["location"] == (
        "/setup/business"
    )

    # 2d: no draft without the model; the owner writes the message
    r = env.client.get(f"{h}/setup/business")
    assert "Tell us about the business." in r.text
    # M11c: no side panel; the box says no draft could be made, the example sits in the box and
    # in the popup; Continue is on
    assert "Couldn’t draft from your files right now" in r.text
    assert "Drafted from your files" not in r.text and 'id="biz-draft"' not in r.text
    assert 'data-dialog-open="biz-example"' in r.text and "An example message" in r.text
    assert "Drafts only." not in r.text  # nothing drafted, so no draft note
    assert "+ erp_db" in r.text  # seen in the files, by code
    r = _post(
        env,
        "/setup/business/continue",
        "/setup/business",
        {"text": w.MESSAGE, "data_lives": "erp_db, sales_dw"},
    )
    assert r.headers["location"] == "/setup/sources"

    # Sources (M2): the sample databases, as a scan leaves them
    svc = env.app.state.services  # type: ignore[attr-defined]
    w.connect_sample(svc.sf, svc.key, _cid(env), sample)
    assert _post(env, "/setup/sources/continue", "/setup/sources").status_code == 303

    # 2m: drafting, then 2n
    r = env.client.get(f"{h}/setup/metrics")
    assert "Drafting your metrics" in r.text
    _jobs(env)
    r = env.client.get(f"{h}/setup/metrics")
    assert (
        "Drafts unavailable · suggested by rules. You can add or edit metrics yourself." in r.text
    )
    # M11c: three checks; the open conflict blocks
    assert re.search(r"We found \d+ metrics\.", r.text) and "Needs you" in r.text
    assert "1 required decision left before you continue" in r.text
    assert "Not quite right?" in r.text and "Business Model draft" in r.text
    assert re.search(r'<button class="btn primary lg" type="submit"\s+disabled', r.text)
    assumed = env.client.get(f"{h}/setup/metrics?panel=assumptions").text
    assert "What we assumed" in assumed and "Check 1 of 3" in assumed

    # every value on the page is the computed value, formatted
    sf = svc.sf
    with sf() as s, tenant_scope(_cid(env)):
        rows = {
            x.key: x for x in s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "metric"))
        }
    nr = rows["net_revenue"]
    assert nr.confidence == "conflict"
    # the Decisions panel: both computed values, nothing preselected
    dec = env.client.get(f"{h}/setup/metrics?panel=decisions").text
    for o in nr.value["conflict"]["options"]:
        assert f"Oct 2026 · {fmt.inr(Decimal(o['value']))}" in dec
    form = dec.split('action="/setup/metrics/decide"')[1].split("</form>")[0]
    assert 'name="choice:net_revenue"' in form and "checked" not in form

    # Confirm is refused while the conflict is open
    r = _post(env, "/setup/metrics/confirm", "/setup/metrics")
    assert r.headers["location"] == "/setup/metrics"
    assert _count(env, OntologyVersion) == 0

    # 2na without the model: both values, Choose works, the chat is off
    r = env.client.get(f"{h}/setup/metrics?panel=choose&key=net_revenue")
    opts = nr.value["conflict"]["options"]
    for o in opts:
        assert fmt.inr(Decimal(o["value"])) in r.text
    # Chat 1.1: Othor AI's pick (the database) by default, Apply in one click; the
    # reply box opens on Suggest edits
    assert "My suggestion" in r.text and ">Apply</button>" in r.text and "Suggest edits" in r.text
    assert "Later" not in r.text  # Apply and Suggest edits only (owner, 18 Nov 2026)
    r = env.client.get(f"{h}/setup/metrics?panel=choose&key=net_revenue&edit=1")
    assert "Not available right now" in r.text
    r = env.client.get(f"{h}/setup/metrics?panel=choose&key=net_revenue&choice=source")
    assert "erp_db wins" in r.text and "becomes High confidence" not in r.text
    r = _post(
        env,
        "/setup/metrics/decide",
        "/setup/metrics",
        {"d": "net_revenue", "choice:net_revenue": "source"},
    )
    # #32: saved, and the panel moves on to the next decision still open (or closes)
    loc = r.headers["location"]
    assert loc == "/setup/metrics" or loc.startswith("/setup/metrics?panel=decisions&d="), loc
    _jobs(env)
    dec = env.client.get(f"{h}/setup/metrics?panel=decisions&d=net_revenue").text
    assert "erp_db wins" in dec  # settled: the sentence saved to the Ontology

    # a definition the form can't show is never flattened by a save
    r = env.client.get(f"{h}/setup/metrics?panel=form&key=receivables_over_60")
    assert "more than the form can show" in r.text
    r = _post(
        env,
        "/setup/metrics/form",
        "/setup/metrics",
        {
            "key": "receivables_over_60",
            "name": "X",
            "table": "erp_db|erp_db|receivables",
            "agg": "sum",
            "column": "amount",
            "date": "due_date",
            "grain": "day",
        },
    )
    with sf() as s, tenant_scope(_cid(env)):
        rec = s.scalars(sa.select(SetupDraft).where(SetupDraft.key == "receivables_over_60")).one()
        assert rec.body == rows["receivables_over_60"].body

    # Add a metric by form (no model needed)
    r = env.client.get(f"{h}/setup/metrics?panel=form")
    assert "Add a metric" in r.text
    r = _post(
        env,
        "/setup/metrics/form",
        "/setup/metrics",
        {
            "name": "Credit notes",
            "table": "erp_db|erp_db|credit_notes",
            "agg": "sum",
            "column": "net_amount",
            "date": "cn_date",
            "grain": "month",
        },
    )
    assert r.status_code == 303, r.text
    _jobs(env)

    r = _post(env, "/setup/metrics/confirm", "/setup/metrics")
    assert r.headers["location"] == "/setup/first-run"
    r = env.client.get(f"{h}/setup/first-run")
    assert "Writing your first brief." in r.text and "#0001 · dry run" in r.text
    assert _count(env, OntologyVersion) == 1 and _count(env, Spec) == 1
    assert _count(env, ModelCall) == 0
    with sf() as s, tenant_scope(_cid(env)):
        assert s.scalars(sa.select(Upload)).all()


def test_personal_column_is_refused_by_the_form(env: Env, sample, need_sources):
    signup(env)
    svc = env.app.state.services  # type: ignore[attr-defined]
    cid = _cid(env)
    w.connect_sample(svc.sf, svc.key, cid, sample)
    with svc.sf() as s, tenant_scope(cid):
        from othor.core.models import SetupState

        s.scalars(sa.select(SetupState)).one().current_step = "metrics"
        s.add(
            SetupDraft(
                kind="meta",
                key="drafting",
                body={"state": "done", "by": "rules"},
                origin={"by": "system"},
            )
        )
        s.commit()
    r = _post(
        env,
        "/setup/metrics/form",
        "/setup/metrics",
        {
            "name": "Staff",
            "table": "sales_dw|public|employee",
            "agg": "count_distinct",
            "column": "phone",
            "grain": "month",
        },
    )
    assert r.status_code == 422
    assert "Othor AI never reads it" in r.text or "has no column" in r.text


def test_key_is_not_on_error_pages(tmp_path, monkeypatch, caplog):
    """A failure whose message carries the key: the 500 page and the logs don't show it."""
    from othor.core.config import Settings
    from othor.mail.relay import MemoryRelay
    from othor.migrations.runner import upgrade
    from othor.web import setup_view
    from othor.web.app import create_app
    from tests.web.conftest import BASE, ROOT, Clock

    url = f"sqlite:///{tmp_path / 'web.db'}"
    upgrade(url)
    settings = Settings(
        _env_file=None,
        db_url=url,
        data_dir=str(tmp_path),
        env="test",  # type: ignore[call-arg]
        base_domain=BASE,
        mail_relay="memory",
        mail_from_domain="othor.localhost",
        model_provider="anthropic",
        model_api_key=KEY,
    )
    relay, clock = MemoryRelay(), Clock()
    app = create_app(settings, relay=relay, clock=clock)
    caplog.set_level("DEBUG")
    with TestClient(app, base_url=ROOT, raise_server_exceptions=False) as client:
        env = Env(client, relay, clock, app)
        signup(env)
        sf = app.state.services.sf  # type: ignore[attr-defined]
        with sf() as s, system_scope("test"):
            from othor.core.models import SetupState

            s.scalars(sa.select(SetupState)).one().current_step = "metrics"
            cid = s.scalars(sa.select(Company.id)).one()
            s.add(
                SetupDraft(
                    company_id=cid,
                    workspace_id=cid,
                    kind="meta",
                    key="drafting",
                    body={"state": "done"},
                    origin={"by": "system"},
                )
            )
            s.commit()

        def boom(*a, **k):
            raise RuntimeError(f"401 invalid x-api-key {KEY}")

        monkeypatch.setattr(setup_view, "metrics_ctx", boom)
        r = client.get(f"{env.host('indigothread')}/setup/metrics")
        assert r.status_code == 500
        assert KEY not in r.text
    assert KEY not in caplog.text


def test_the_ceiling_on_metrics_in_setup(env: Env, sample, need_sources, monkeypatch):
    """M15.1: at the ceiling, Add a metric is refused and Confirm waits for a skip."""
    from othor.ontology import service

    signup(env)
    svc = env.app.state.services  # type: ignore[attr-defined]
    cid = _cid(env)
    w.connect_sample(svc.sf, svc.key, cid, sample)
    with svc.sf() as s, tenant_scope(cid):
        from othor.core.models import SetupState

        s.scalars(sa.select(SetupState)).one().current_step = "metrics"
        s.add(
            SetupDraft(
                kind="meta",
                key="drafting",
                body={"state": "done", "by": "rules"},
                origin={"by": "system"},
            )
        )
        s.commit()
    form = {
        "name": "Credit notes",
        "table": "erp_db|erp_db|credit_notes",
        "agg": "sum",
        "column": "net_amount",
        "date": "cn_date",
        "grain": "month",
    }
    assert _post(env, "/setup/metrics/form", "/setup/metrics", form).status_code == 303
    monkeypatch.setattr(service, "max_metrics", lambda: 1)
    r = _post(env, "/setup/metrics/form", "/setup/metrics", {**form, "name": "Credit notes 2"})
    assert r.status_code == 422
    assert "Othor AI follows up to 1 metrics. Skip one before adding another." in r.text
    monkeypatch.setattr(service, "max_metrics", lambda: 0)
    page = env.client.get(_h(env) + "/setup/metrics").text
    assert "Othor AI follows up to 0 metrics · skip 1 before you continue" in page
    r = _post(env, "/setup/metrics/confirm", "/setup/metrics")
    assert r.headers["location"] == "/setup/metrics"
    assert _count(env, OntologyVersion) == 0


def test_the_ceiling_is_said_plainly_wherever_it_is_hit(env: Env):
    """A change that adds a metric past the ceiling: a toast in place, else the Ontology's note."""
    from othor.ontology.service import MetricCeiling, ceiling_text

    def boom() -> None:
        raise MetricCeiling(ceiling_text())

    env.app.add_api_route("/_test/ceiling", boom, methods=["GET"])  # type: ignore[attr-defined]
    h = _h(env)
    r = env.client.get(h + "/_test/ceiling", headers={"HX-Request": "true"})
    assert r.status_code == 422 and "Othor AI follows up to 60 metrics." in r.text
    assert r.headers["HX-Retarget"] == "#toast-dock"
    r = env.client.get(h + "/_test/ceiling", follow_redirects=False)
    assert r.headers["location"] == "/ontology?note=ceiling"
