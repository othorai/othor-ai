# SPDX-License-Identifier: Apache-2.0
"""v0.19 (M19.5): Big Picture on the sample night, with a scripted model. The button on Home, the
report in Home's side panel, made once per run and language and stored; "Not available right now"
with no model; Email it to me repeats the stored report with no model call; an event each."""

from __future__ import annotations

import sqlalchemy as sa

import othor.ai.gateway as gw
from othor.ai.gateway import TemplateProvider
from othor.briefs.models import BigPicture
from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.web.routes import big_picture as bpr
from tests.fakes_big_picture import Fake
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def _model(monkeypatch, provider) -> None:
    monkeypatch.setattr(gw, "provider_for", lambda settings: provider)
    monkeypatch.setattr(bpr, "provider_for", lambda settings: provider)


def _make(env, h, size: str = ""):
    tok = env.csrf(h + "/home")
    return env.client.post(h + "/big-picture/make", data={"csrf": tok, "size": size})


def _rows(svc, cid) -> list[BigPicture]:
    with svc.sf() as s, tenant_scope(cid):
        return list(s.scalars(sa.select(BigPicture)).all())


def _events(svc, cid, action: str) -> int:
    with svc.sf() as s, tenant_scope(cid):
        return len(s.scalars(sa.select(Event).where(Event.action == action)).all())


def test_the_button_is_on_home_and_marked_as_ai(night, monkeypatch):  # noqa: F811
    env, h, _cid, _rid, _svc = night
    _model(monkeypatch, Fake())
    page = env.client.get(h + "/home").text
    assert 'class="btn ai"' in page and "Big Picture Report" in page
    assert 'href="/home?panel=big-picture"' in page
    _model(monkeypatch, TemplateProvider())
    page = env.client.get(h + "/home").text
    assert "Big Picture · not available right now" in page and "disabled" in page


def test_written_once_per_run_and_language_then_read_from_store(night, monkeypatch):  # noqa: F811
    env, h, cid, _rid, svc = night
    fake = Fake()
    _model(monkeypatch, fake)
    panel = env.client.get(h + "/home?panel=big-picture").text
    assert 'hx-post="/big-picture/make"' in panel  # the writing state, filled in place
    r = _make(env, h)
    assert r.status_code == 200
    assert "Summary by Othor AI" in r.text and "South fell, and West made most of it back" in r.text
    assert 'id="bp-actions" hx-swap-oob="true"' in r.text and "Email it to me" in r.text
    assert "Every number is computed, not written by AI" in r.text
    assert len(fake.calls) == 1 and len(_rows(svc, cid)) == 1
    # the stored report: no second call, from the panel or from make
    assert (
        "South fell, and West made most of it back"
        in env.client.get(h + "/home?panel=big-picture").text
    )
    _make(env, h)
    assert len(fake.calls) == 1
    assert _events(svc, cid, "big_picture.made") == 1
    # Hindi is its own report
    tok = env.csrf(h + "/settings")
    env.client.post(h + "/settings", data={"csrf": tok, "field": "report_language", "value": "hi"})
    r = _make(env, h)
    assert "Othor AI का सारांश" in r.text
    assert len(fake.calls) == 2 and {x.language for x in _rows(svc, cid)} == {"en", "hi"}


def test_every_figure_shown_is_a_computed_value(night, monkeypatch):  # noqa: F811
    env, h, cid, _rid, svc = night
    _model(monkeypatch, Fake(wrong=True))
    r = _make(env, h)
    assert "35%" not in r.text  # the model's own figure, dropped
    (row,) = _rows(svc, cid)
    assert row.guard == "dropped:1"


def test_no_model_not_available_and_nothing_stored(night, monkeypatch):  # noqa: F811
    env, h, cid, _rid, svc = night
    _model(monkeypatch, TemplateProvider())
    panel = env.client.get(h + "/home?panel=big-picture").text
    assert "Not available right now" in panel and "big-picture/make" not in panel
    assert "Not available right now" in _make(env, h).text
    assert _rows(svc, cid) == []


def test_email_it_to_me_repeats_the_stored_report_without_a_model_call(night, monkeypatch):  # noqa: F811
    env, h, cid, _rid, svc = night
    fake = Fake()
    _model(monkeypatch, fake)
    _make(env, h)
    (row,) = _rows(svc, cid)
    before = len(env.relay.outbox)
    tok = env.csrf(h + "/home")
    r = env.client.post(h + f"/big-picture/{row.id}/email", data={"csrf": tok})
    assert 'id="bp-email"' in r.text and "Sent to " in r.text
    (msg,) = env.relay.outbox[before:]
    assert msg.subject.startswith("Big Picture · ")
    assert "South fell, and West made most of it back" in msg.html and "Big Picture" in msg.text
    assert len([i for i in msg.images if i.cid.startswith("chart-")]) >= 3  # cards and range bars
    assert len(fake.calls) == 1
    assert _events(svc, cid, "big_picture.emailed") == 1


def test_the_old_address_opens_the_panel_and_an_unknown_report_is_gone(night, monkeypatch):  # noqa: F811
    env, h, _cid, _rid, _svc = night
    _model(monkeypatch, Fake())
    r = env.client.get(h + "/big-picture", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/home?panel=big-picture"
    tok = env.csrf(h + "/home")
    r = env.client.post(
        h + "/big-picture/00000000-0000-0000-0000-000000000000/email", data={"csrf": tok}
    )
    assert r.status_code == 404


def test_the_extended_width_is_kept(night, monkeypatch):  # noqa: F811
    env, h, _cid, _rid, _svc = night
    _model(monkeypatch, Fake())
    panel = env.client.get(h + "/home?panel=big-picture&size=extended").text
    assert "bp-panel extended" in panel and '"size": "extended"' in panel
    r = _make(env, h, size="extended")
    assert 'aria-label="Normal width"' in r.text


def test_after_a_rerun_it_reads_that_runs_own_issue(night):  # noqa: F811
    """15 Nov 2026 (indigo2): a rerun writes another issue for the same day; Big Picture read the
    newest by date alone, an older same-day issue, so the cached words came back unchanged."""
    import uuid

    from othor.briefs.models import BriefIssue
    from othor.engine.models import Run
    from othor.web import big_picture_view as bpv

    _env, _h, cid, rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        again = Run(
            **{
                c.name: getattr(run, c.name)
                for c in Run.__table__.columns
                if c.name not in ("id", "number", "kind", "created_at")
            },
            id=uuid.uuid4(),
            number=run.number + 1,
            kind="rerun",
        )
        s.add(again)
        s.flush()
        old = s.scalars(sa.select(BriefIssue).where(BriefIssue.run_id == rid)).first()
        s.add(
            BriefIssue(
                **{
                    c.name: getattr(old, c.name)
                    for c in BriefIssue.__table__.columns
                    if c.name not in ("id", "run_id", "body", "created_at")
                },
                id=uuid.uuid4(),
                run_id=again.id,
                body={**old.body, "title": "The rerun’s own title"},
            )
        )
        s.commit()
        f = bpv.gather(s, svc.settings, again, again.run_date)
        assert f.inputs["briefs"][0]["title"] == "The rerun’s own title"
        f = bpv.gather(s, svc.settings, run, run.run_date)
        assert f.inputs["briefs"][0]["title"] != "The rerun’s own title"
