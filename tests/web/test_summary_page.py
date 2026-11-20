# SPDX-License-Identifier: Apache-2.0
"""The Summary page (v0.16, M16.3): owners only, in the page style, one line per area that
expands into its parts and other numbers; the controls, Move, Hide, Show and the lead number are
in-place posts that save the owner's layout with an event and come back to /summary."""

from __future__ import annotations

import re

import sqlalchemy as sa

from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def _post(env, h, path, data):
    tok = env.csrf(h + "/summary")
    return env.client.post(h + path, data={"csrf": tok, **data}, follow_redirects=False)


def _events(svc, cid, action):
    with svc.sf() as s, tenant_scope(cid):
        return s.scalars(sa.select(Event).where(Event.action == action)).all()


def _areas(page):
    return re.findall(r'<span class="sum-area">([^<]+)</span>', page)


def test_the_page(night):  # noqa: F811
    env, h, *_ = night
    p = env.client.get(h + "/summary").text
    assert "<h1>Summary</h1>" in p and "As of last night&#39;s run · 1 Nov" in p
    assert 'class="nav-item is-on" href="/summary" aria-current="page"' in p
    # owner, 15 Nov 2026: Summary sits below Sweep in the side menu
    nav = re.findall(r'class="nav-item[^"]*" href="(/[a-z]+)"', p)
    assert nav[:4] == ["/home", "/brief", "/sweep", "/summary"]
    assert _areas(p) == ["Sales", "Collections", "Operations", "Finance", "Customers"]
    assert 'action="/summary/view"' in p and 'name="window"' in p
    assert ">Compare</label>" in p and ">Change</label>" in p and ">Actual</label>" in p
    assert ">Period before</label>" not in p  # the period alone until Compare is on
    # Sales expands into the parts that add up to Net revenue, and the area's other numbers
    sales = p.split('<span class="sum-area">Sales</span>', 1)[1].split("</details>", 1)[0]
    for t in (
        "Net revenue",
        "How it adds up",
        "Invoiced value",
        "Credit notes",
        "Rebates",
        "Also in Sales",
        "Orders",
        "Lead number",
        "Hide",
        'href="/ontology?metric=net_revenue"',
    ):
        assert t in sales, t
    assert "<svg" in sales
    assert 'href="/sweep/finding/' in p  # the finding marker


def test_the_view_is_saved_and_reopens(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    r = _post(
        env, h, "/summary/view", {"window": "12m", "compare_on": "1", "show": "abs", "split": ""}
    )
    p = env.client.get(h + "/summary").text
    assert 'value="before" data-autosubmit checked' in p  # Compare on: the period before
    view = {"window": "12m", "compare_on": "1", "compare": "year", "show": "abs", "split": ""}
    r = _post(env, h, "/summary/view", view)
    assert r.status_code == 303 and r.headers["location"] == "/summary"
    assert _events(svc, cid, "summary.view_changed")
    p = env.client.get(h + "/summary").text
    assert (
        'value="year" data-autosubmit checked' in p and 'value="abs" data-autosubmit checked' in p
    )
    assert '<option value="12m" selected>' in p
    assert "vs Oct 2025" in p


def test_hide_show_and_the_lead_number(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    p = env.client.get(h + "/summary").text
    assert "Move up" not in p and "Move down" not in p  # parked for a later release
    _post(env, h, "/summary/hide", {"area": "Collections"})
    p = env.client.get(h + "/summary").text
    assert "Collections" not in _areas(p) and "Hidden · 1" in p and ">Show Collections<" in p
    r = _post(env, h, "/summary/show", {"area": "Collections", "all": "1"})
    assert r.headers["location"] == "/summary?all=1"
    assert "Collections" in _areas(env.client.get(h + "/summary").text)
    _post(env, h, "/summary/lead", {"area": "Sales", "metric": "orders"})
    p = env.client.get(h + "/summary").text
    assert '<span class="sum-name">Orders' in p
    for action in ("summary.hidden", "summary.shown", "summary.lead_changed"):
        assert _events(svc, cid, action), action


def test_seven_days(night):  # noqa: F811
    env, h, *_ = night
    _post(env, h, "/summary/view", {"window": "7d", "show": "pct", "split": ""})
    p = env.client.get(h + "/summary").text
    assert '<option value="7d" selected>7 days</option>' in p and ">1 Quarter</option>" in p
    assert "vs 30 Oct" in p  # a day against the day before


def test_unknown_areas_and_numbers_are_a_toast(night):  # noqa: F811
    env, h, *_ = night
    r = _post(env, h, "/summary/hide", {"area": "Mars"})
    assert r.status_code == 400 and r.headers.get("X-Othor-Toast") == "1"
    r = _post(env, h, "/summary/lead", {"area": "Sales", "metric": "dso"})
    assert r.status_code == 400


def test_show_all_in_place(night, monkeypatch):  # noqa: F811
    from othor.web import summary_view as sv

    env, h, *_ = night
    monkeypatch.setattr(sv, "SHOWN_FIRST", 3)
    p = env.client.get(h + "/summary").text
    assert len(_areas(p)) == 3 and "Show all · 2 more" in p
    assert 'hx-get="/summary?all=1" hx-target="#summary-body" hx-select="#summary-body"' in p
    p = env.client.get(h + "/summary?all=1").text
    assert len(_areas(p)) == 5 and "Show all" not in p


def test_before_the_first_run(env):
    from tests.web.conftest import signup

    signup(env)
    p = env.client.get(env.host("indigothread") + "/summary").text
    assert "<h1>Summary</h1>" in p and "Summary appears after the first nightly run" in p


def test_compare_off_takes_the_grey_line_away(night):  # noqa: F811
    env, h, *_ = night
    _post(env, h, "/summary/view", {"window": "13w", "compare_on": "1", "show": "pct", "split": ""})
    _post(env, h, "/summary/view", {"window": "13w", "show": "pct", "split": ""})
    p = env.client.get(h + "/summary").text
    assert ">Period before</label>" not in p and 'name="compare_on" value="1" data-autosubmit>' in p


def test_the_every_number_view(night):  # noqa: F811
    env, h, *_ = night
    p = env.client.get(h + "/summary").text
    assert '<option value="areas" selected>By area</option>' in p and ">Every number</option>" in p
    _post(env, h, "/summary/view", {"window": "13w", "show": "pct", "split": "", "rows": "numbers"})
    p = env.client.get(h + "/summary").text
    assert p.count('<section class="sum-group"') == 5
    assert p.count('id="num-') == 14
    assert 'id="num-orders"' in p and "Hide area" in p and "Lead number" not in p
    # a number hidden, then an area
    _post(env, h, "/summary/hide", {"metric": "orders"})
    _post(env, h, "/summary/hide", {"area": "Finance"})
    p = env.client.get(h + "/summary").text
    assert 'id="num-orders"' not in p and "Hidden · 2" in p and ">Show Orders<" in p
    assert '<section class="sum-group" aria-label="Finance">' not in p
    _post(env, h, "/summary/show", {"metric": "orders"})
    assert 'id="num-orders"' in env.client.get(h + "/summary").text
    assert _post(env, h, "/summary/hide", {"metric": "made_up"}).status_code == 400
