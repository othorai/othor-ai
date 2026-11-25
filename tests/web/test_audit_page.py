# SPDX-License-Identifier: Apache-2.0
"""Audit log (0.35): the side-menu item after AI usage, who did what in plain words, the filters
and "Load more" in place, Details only where there's a before and after (never a secret), the
CSV as the page, Othor AI on a person's request, another company's events never shown, and the
admins see it too."""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import uuid

import sqlalchemy as sa

from othor import tools
from othor.ai.models import ModelCall
from othor.core import actors
from othor.core.events import record_event
from othor.core.models import AppUser, Company, Event, Membership
from othor.core.tenancy import system_scope, tenant_scope
from tests.web.conftest import BASE, Env, signup

AT = dt.datetime(2026, 10, 20, 6, 0, tzinfo=dt.UTC)  # two weeks before the app's clock


def _ids(env: Env, slug: str = "indigothread") -> tuple[uuid.UUID, uuid.UUID]:
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, system_scope("test"):
        c = s.scalars(sa.select(Company).where(Company.slug == slug)).one()
        uid = s.scalars(sa.select(Membership.user_id).where(Membership.company_id == c.id)).first()
        return c.id, uid


def _add(env: Env, cid: uuid.UUID, rows: list[dict]) -> None:
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, tenant_scope(cid):
        for r in rows:
            record_event(s, **{"object_type": "company", "object_id": cid, "at": AT, **r})
        s.commit()


def _seed(env: Env) -> tuple[uuid.UUID, uuid.UUID]:
    cid, uid = _ids(env)
    _add(
        env,
        cid,
        [
            {
                "actor": "system",
                "action": "run.finished",
                "object_type": "run",
                "after": {"number": 61, "kind": "nightly", "status": "done"},
                "at": AT + dt.timedelta(minutes=1),
            },
            {
                "actor": actors.othor_ai(uid),
                "action": "question.decided_by_ai",
                "object_type": "question",
                "after": {"record": "breakdown:season"},
                "at": AT + dt.timedelta(minutes=2),
            },
            {
                "actor": str(uid),
                "action": "othor_chat.applied",
                "object_type": "chat_thread",
                "after": {"changes": 1, "version": 4, "drafted_by": "othor_ai"},
                "at": AT + dt.timedelta(minutes=3),
            },
            {
                "actor": "operator",
                "action": "company.ai_cap_changed",
                "before": {"cap": 100},
                "after": {"cap": 200, "api_token": "TOPSECRET-1", "password": "TOPSECRET-2"},
                "at": AT + dt.timedelta(minutes=4),
            },
            {
                "actor": "uk@indigothread.example",  # an old row: the email, before 0.35
                "action": "user.renamed",
                "object_type": "user",
                "before": {"name": "Uday"},
                "after": {"name": "Uday Kamath"},
                "at": AT + dt.timedelta(minutes=5),
            },
            {
                "actor": str(uid),
                "action": "source.created",
                "object_type": "source",
                "after": {"kind": "sheets"},
                "at": AT + dt.timedelta(minutes=6),
            },
        ],
    )
    return cid, uid


def _get(env: Env, path: str) -> str:
    r = env.client.get(env.host("indigothread") + path)
    assert r.status_code == 200, (path, r.status_code)
    return r.text


def test_the_menu_item_follows_ai_usage_with_a_clock(env: Env) -> None:
    signup(env)
    home = _get(env, "/home")
    side = home[home.index('<p class="group">Setup</p>') : home.index('class="account"')]
    assert (
        side.index('href="/usage"') < side.index('href="/audit"') < side.index('href="/settings"')
    )
    assert re.search(r'href="/audit">\s*<svg class="icon"[^>]*><use href="[^"]*#clock"', side)
    page = _get(env, "/audit")
    assert '<a class="nav-item is-on" href="/audit" aria-current="page">' in page
    assert "Who did what in Othor AI, and when. Newest first." in page
    assert "Drafts only" not in page


def test_who_and_what_in_plain_words(env: Env) -> None:
    signup(env)
    _seed(env)
    page = _get(env, "/audit?days=all")
    assert "Nightly run finished" in page and "sealed" not in page
    assert "Othor AI (asked by Uday)" in page
    assert "Answered a question for you · season" in page
    assert "Applied Othor AI&#39;s draft: 1 change to the Business Model (version 4)" in page
    assert "Othor support" in page and "Changed this month&#39;s AI allowance" in page
    assert 'href="/usage">AI usage</a>' in page  # the object, linked inside the sentence
    assert "Changed their name: Uday to Uday Kamath" in page  # the old email actor → the person
    assert "Added Google Sheets" in page
    assert "Signed in" in page  # signup's own events
    assert "TOPSECRET" not in page and "api_token" not in page.lower()
    # Details only on rows with a before and after
    rows = re.findall(r'<(details|div) class="audit-row">', page)
    assert "details" in rows and "div" in rows
    signed_in = page[: page.index("Signed in")].rsplit('class="audit-row"', 1)[0][-10:]
    assert "<div" in signed_in


def test_filters_swap_the_rows_in_place(env: Env) -> None:
    signup(env)
    _, uid = _seed(env)
    h = env.host("indigothread")
    r = env.client.get(f"{h}/audit/rows?who=ai&kind=&days=all")
    assert r.status_code == 200 and r.headers["HX-Push-Url"] == "/audit?who=ai&kind=&days=all"
    assert 'id="audit-body"' in r.text and "<html" not in r.text
    assert "Othor AI (asked by Uday)" in r.text and "Nightly run" not in r.text
    assert "Signed in" not in r.text
    sup = env.client.get(f"{h}/audit/rows?who=support&days=all").text
    assert "Othor support" in sup and "Uday Kamath" not in sup
    sysrows = env.client.get(f"{h}/audit/rows?who=system&days=all").text
    assert "Nightly run" in sysrows and "Othor support" not in sysrows
    me = env.client.get(f"{h}/audit/rows?who={uid}&days=all").text
    assert "Changed their name" in me and "Added Google Sheets" in me  # the email row too
    assert "Nightly run" not in me and "asked by" not in me
    src = env.client.get(f"{h}/audit/rows?kind=sources&days=all").text
    assert "Added Google Sheets" in src and "Signed in" not in src
    runs = env.client.get(f"{h}/audit/rows?kind=runs&days=all").text
    assert "Nightly run" in runs and "Added Google Sheets" not in runs
    # the seeded rows are two weeks old: not in the last 7 days
    week = env.client.get(f"{h}/audit/rows?days=7").text
    assert "Nightly run" not in week


def test_load_more_pages_by_fifty(env: Env) -> None:
    signup(env)
    cid, _ = _ids(env)
    _add(
        env,
        cid,
        [
            {"actor": "system", "action": "source.read", "at": AT + dt.timedelta(seconds=i)}
            for i in range(70)
        ],
    )
    page = _get(env, "/audit?kind=sources&days=all")
    assert page.count('class="audit-row"') == 50
    m = re.search(r'hx-get="(/audit/rows\?[^"]+after=[^"]+)"', page)
    assert m and ">Load more<" in page
    more = env.client.get(env.host("indigothread") + m.group(1).replace("&amp;", "&")).text
    assert more.count('class="audit-row"') == 20 and "Load more" not in more
    assert 'id="audit-body"' not in more


def test_the_csv_is_the_page(env: Env) -> None:
    signup(env)
    _seed(env)
    h = env.host("indigothread")
    r = env.client.get(f"{h}/audit.csv?who=&kind=&days=all")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert list(rows[0]) == ["When", "Who", "What", "Kind"]
    page = _get(env, "/audit?days=all")
    shown = re.findall(r'<span class="audit-who">([^<]*)</span>', page)
    assert [x["Who"] for x in rows] == [s.replace("&#39;", "'") for s in shown]
    assert any(x["What"] == "Changed this month's AI allowance · AI usage" for x in rows)
    assert "TOPSECRET" not in r.text


def test_model_calls_show_the_task_never_the_text(env: Env) -> None:
    signup(env)
    cid, _ = _ids(env)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, tenant_scope(cid):
        s.add(
            ModelCall(
                prompt_id="draft_metrics",
                prompt_version="v2",
                provider="fake",
                model_id="m",
                input_hash="h",
                input={"line": "ROW VALUE 9917"},
                output="MODEL OUTPUT ZEBRA",
                guard_result="passed",
                created_at=AT,
            )
        )
        s.commit()
    page = _get(env, "/audit?who=ai&days=all")
    assert "Othor AI wrote: Draft your metrics · kept" in page and "· Used" not in page
    assert "9917" not in page and "ZEBRA" not in page
    assert "Othor AI wrote" not in _get(env, "/audit?kind=sources&days=all")


def test_a_chat_tool_call_is_othor_ai_asked_by_the_person(env: Env) -> None:
    signup(env)
    cid, uid = _seed(env)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, tenant_scope(cid):
        ctx = tools.Ctx(s, svc.settings, s.get(Company, cid), AT.date())
        card = tools.call(ctx, "audit_search", {"about": "google", "days": 30}, actor=str(uid))
        assert [v for _, v in card.facts] == ["Uday Kamath: Added Google Sheets"]
        tools.call(ctx, "audit_search", {}, actor=str(uid), via="mcp", extra={"key": "k1"})
        s.commit()
    called = [e.actor for e in env.events("tool.called")]
    assert called == [f"othor_ai:{uid}", "mcp:k1"]
    page = _get(env, "/audit?who=ai&days=all")
    assert "Looked up audit search in chat" in page


def test_another_companys_events_never_show(env: Env) -> None:
    from othor.audit import view

    signup(env)
    a_cookie = next(c.value for c in env.client.cookies.jar if c.name == "othor_session")
    env.client.cookies.clear()
    signup(env, name="Other Owner", email="owner@otherco.in", company="Other Co", slug="otherco")
    other, _ = _ids(env, "otherco")
    _add(env, other, [{"actor": "system", "action": "source.read", "after": {"rows": 77}}])
    cid, _ = _ids(env)
    _add(env, cid, [{"actor": "system", "action": "source.read", "after": {"rows": 5}}])
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, tenant_scope(cid):
        rows = view.page(s, view.Filters(days="all"), AT + dt.timedelta(days=30)).rows
    assert rows and all(r.who != "Other Owner" for r in rows)
    texts = [d for r in rows for d in r.details]
    assert ("Rows read", "", "5") in texts and ("Rows read", "", "77") not in texts
    # over HTTP: Other Co's session on Indigo Thread's page is a 404; Indigo's own page shows
    # only its rows
    b_cookie = next(c.value for c in env.client.cookies.jar if c.name == "othor_session")
    env.client.cookies.clear()
    env.client.cookies.set("othor_session", b_cookie, domain=f"indigothread.{BASE}")
    r = env.client.get(env.host("indigothread") + "/audit", follow_redirects=False)
    assert r.status_code == 404
    env.client.cookies.clear()
    env.client.cookies.set("othor_session", a_cookie, domain=f"indigothread.{BASE}")
    page = _get(env, "/audit?days=all&kind=sources")
    assert "Other Owner" not in page and "Rows read" in page
    assert "<span>77</span>" not in page and "<span>5</span>" in page


def test_an_admin_sees_the_audit_log(env: Env) -> None:
    signup(env)
    cid, uid = _ids(env)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, tenant_scope(cid):
        m = s.scalars(sa.select(Membership).where(Membership.user_id == uid)).one()
        m.role = "admin"
        s.commit()
    page = _get(env, "/audit")
    assert "Audit log" in page and 'href="/audit"' in page


def test_the_owner_writes_their_id_not_their_email(env: Env) -> None:
    """0.35: the sites that wrote the email now write the user id."""
    signup(env)
    h = env.host("indigothread")
    tok = env.csrf(f"{h}/account")
    env.client.post(f"{h}/account/name", data={"csrf": tok, "name": "Uday K"})
    ev = env.events("user.renamed")
    _, uid = _ids(env)
    assert ev and ev[-1].actor == str(uid)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s:
        assert s.get(AppUser, uid).name == "Uday K"
    assert not [e for e in env.events() if "@" in e.actor and isinstance(e, Event)]


def test_othor_ais_own_drafting_is_othor_ai_asked_by_the_person(env: Env) -> None:
    """A setup draft stored with the person's bare id reads as Othor AI on their request."""
    signup(env)
    cid, uid = _ids(env)
    _add(env, cid, [{"actor": str(uid), "action": "setup.drafted", "after": {"added": 3}}])
    ai = _get(env, "/audit?who=ai&days=all")
    assert "Othor AI drafted your numbers" in ai and "Othor AI (asked by Uday)" in ai
    me = _get(env, f"/audit?who={uid}&days=all")
    assert "Othor AI drafted your numbers" not in me


def test_a_code_request_shows_under_people_only(env: Env) -> None:
    signup(env)
    cid, uid = _ids(env)
    _add(env, cid, [{"actor": str(uid), "action": "auth.code_sent"}])
    _add(env, cid, [{"actor": str(uid), "action": "auth.code_failed"}])
    everyone = _get(env, "/audit?days=all")
    assert "Signed in" in everyone and "Asked for a sign-in code" not in everyone
    assert "Entered a wrong sign-in code" in everyone  # failed codes stay visible
    people = _get(env, "/audit?kind=people&days=all")
    assert "Asked for a sign-in code" in people
