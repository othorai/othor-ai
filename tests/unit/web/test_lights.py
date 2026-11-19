# SPDX-License-Identifier: Apache-2.0
"""Home's lights (owner, 16 Nov 2026): one light per part, in the order the data flows, each
counted from what the run recorded."""

from __future__ import annotations

from othor.web.lights_view import lights

ORDER = [
    "connection",
    "columns",
    "fresh",
    "volume",
    "run",
    "numbers",
    "checks",
    "findings",
    "forecasts",
    "checked",
    "ai",
    "briefs",
    "next",
]


def _src(title="sales_dw", status="Updated", dot="ok", **kw):
    return {
        "name": title,
        "title": title,
        "status": status,
        "dot": dot,
        "state": "connected",
        "read_reason": None,
        "query_code": None,
        "added": 100,
        "usual": 100,
        "history": 10,
        **kw,
    }


def _facts(**kw):
    base = {
        "run": {
            "number": 12,
            "status": "done",
            "kind": "nightly",
            "ended": "02:01",
            "started": "02:00",
            "late": False,
            "slow": False,
            "took": "1 min",
        },
        "sources": [_src(), _src("erp_db"), _src("Order tracker")],
        "numbers": {"total": 60, "stale": []},
        "checks": {"ran": 11, "total": 11},
        "findings": {"new": 2, "worse": 1, "came_back": 0, "open": 5},
        "forecasts": {"total": 5, "made": 5, "unsure": []},
        "replayed": True,
        "ai": {"model": 1, "template": 0, "why": None},
        "briefs": [
            {
                "name": "Leadership",
                "made": True,
                "sent": 3,
                "recipients": 3,
                "due_passed": True,
                "bounced": 0,
            }
        ],
        "next": {"run": "tonight at 02:00", "brief": "06:00"},
    }
    return {**base, **kw}


def test_all_well_is_all_clear_in_the_order_the_data_flows():
    got = lights(_facts())
    assert [x["key"] for x in got["lights"]] == ORDER
    assert got["ok"] and got["summary"] == "All clear · finished at 02:01"
    assert got["subtitle"] == "All clear · run #0012 · finished at 02:01"
    by = {x["key"]: x for x in got["lights"]}
    assert by["findings"]["line"] == "2 new · 1 worse" and by["findings"]["state"] == "info"
    assert by["briefs"]["label"] == "Email delivery"
    assert by["briefs"]["line"] == "1 of 1 brief sent today"
    assert by["briefs"]["details"] == ["Leadership: sent to 3 people"]
    # all clear: one group, everything working (problems first when there are any)
    assert [g["title"] for g in got["groups"]] == ["Working"]
    # every light has its own icon
    assert len({x["icon"] for x in got["lights"]}) == len(ORDER)


def test_the_source_problems_each_light_their_own_part():
    got = lights(
        _facts(
            sources=[
                _src(read_reason="Wrong user or password."),
                _src("erp_db", query_code="1054"),
                _src(
                    "Order tracker", status="Not updated yesterday", dot="warn", added=10, usual=100
                ),
            ]
        )
    )
    by = {x["key"]: x for x in got["lights"]}
    assert by["connection"]["state"] == "bad"
    assert by["connection"]["details"] == ["sales_dw: Wrong user or password."]
    assert by["columns"]["state"] == "bad"
    assert by["columns"]["details"] == ["erp_db: a column the numbers use is missing"]
    assert (
        by["fresh"]["state"] == "warn" and by["fresh"]["line"] == "2 of 3 sources updated yesterday"
    )
    assert by["volume"]["state"] == "warn"
    assert by["volume"]["details"] == ["Order tracker: 90% fewer new rows than usual"]
    assert got["summary"].startswith("4 things need you")


def test_expected_quiet_is_grey_not_a_problem():
    got = lights(
        _facts(
            sources=[_src(status="Not updated yesterday", dot="muted", history=2)],
            checks={"ran": 10, "total": 11, "waiting": 1},
            forecasts={"total": 5, "made": 4, "unsure": ["Receivables over 60 days"]},
            replayed=None,
            ai={"model": 0, "template": 1, "why": "unavailable"},
        )
    )
    by = {x["key"]: x for x in got["lights"]}
    for k in ("volume", "checks", "forecasts", "checked", "ai"):
        assert by[k]["state"] == "note", k
    # a source that didn't update is worth a look, even when it often skips a day (owner,
    # 17 Nov 2026)
    assert by["fresh"]["state"] == "warn" and not got["ok"]
    assert got["groups"][0]["title"] == "Worth a look"
    assert [x["key"] for x in got["groups"][0]["lights"]] == ["fresh"]
    assert by["checks"]["href"] == "/home?panel=othor&subject=questions"
    assert by["ai"]["details"] == [
        "Othor AI was unavailable, so the brief was written from templates"
    ]


def test_the_run_and_the_briefs():
    got = lights(
        _facts(
            run={
                "number": 13,
                "status": "done",
                "kind": "rerun",
                "ended": "08:30",
                "started": "08:29",
                "late": False,
                "slow": True,
                "took": "40 min",
            },
            numbers={"total": 60, "stale": ["Net revenue", "DSO"]},
            briefs=[
                {
                    "name": "Leadership",
                    "made": True,
                    "sent": 0,
                    "recipients": 2,
                    "due_passed": True,
                    "bounced": 0,
                },
                {
                    "name": "Cash",
                    "made": True,
                    "sent": 2,
                    "recipients": 2,
                    "due_passed": True,
                    "bounced": 1,
                },
            ],
        )
    )
    by = {x["key"]: x for x in got["lights"]}
    assert (
        by["run"]["line"] == "Run #0013 (run again) finished at 08:30"
        and by["run"]["state"] == "warn"
    )
    assert by["numbers"]["state"] == "bad" and by["numbers"]["details"] == [
        "Kept at the last good day: Net revenue and DSO"
    ]
    assert by["briefs"]["state"] == "bad"
    assert by["briefs"]["details"] == ["Leadership: not sent yet", "Cash: 1 email bounced"]
    # the groups: problems, then warnings, then good to have, then working
    titles = [g["title"] for g in got["groups"]]
    assert titles == ["Needs you", "Worth a look", "Working"]
    assert [x["key"] for x in got["groups"][0]["lights"]] == ["numbers", "briefs"]


def _brief(**kw):
    base = {
        "name": "Leadership",
        "made": True,
        "sent": 0,
        "sent_today": 0,
        "recipients": 2,
        "due_passed": True,
        "late": True,
        "bounced": 0,
        "failed": 0,
        "time": "06:00",
    }
    return {**base, **kw}


def test_email_delivery_is_about_todays_emails():
    """Owner, 17 Nov 2026: whether the briefs' emails go out; a brief with no recipients, or a
    re-run's copy that isn't emailed, is not a health problem."""

    def light(*briefs):
        got = lights(_facts(briefs=list(briefs)))
        return {x["key"]: x for x in got["lights"]}["briefs"]

    # the morning's emails went out; tonight's run again made a copy that isn't emailed
    x = light(_brief(sent=0, sent_today=2))
    assert x["state"] == "ok" and x["line"] == "1 of 1 brief sent today"
    assert x["details"] == ["Leadership: sent to 2 people"]
    # no recipients: left out, nothing to warn about
    x = light(_brief(recipients=0), _brief(name="Cash", sent_today=1))
    assert x["state"] == "ok" and x["details"] == ["Cash: sent to 1 person"]
    x = light(_brief(recipients=0))
    assert x["state"] == "note" and x["line"] == "No brief emails due today"
    # before the brief's time and in the half hour after it: not a warning
    x = light(_brief(due_passed=False, late=False))
    assert x["state"] == "ok" and x["details"] == ["Leadership: goes out at 6:00 AM IST"]
    x = light(_brief(late=False))
    assert x["state"] == "ok"
    # half an hour late: worth a look; failed or bounced: needs you
    x = light(_brief())
    assert x["state"] == "warn" and x["line"] == "1 of 1 brief not sent yet"
    x = light(_brief(sent_today=1, failed=1))
    assert x["state"] == "bad" and x["details"] == ["Leadership: 1 email couldn’t be sent"]


def test_a_failed_run_shows_what_still_holds():
    got = lights(_facts(run={"number": 14, "status": "failed", "kind": "nightly", "ended": ""}))
    assert [x["key"] for x in got["lights"]] == ["connection", "run", "next"]
    assert not got["ok"] and got["lights"][1]["link"] == "Why it failed"


def test_the_words_light():
    def ai(**kw):
        return {x["key"]: x for x in lights(_facts(ai=kw))["lights"]}["ai"]

    assert ai(model=2)["line"] == "2 of 2 briefs written by Othor AI"
    assert ai(quiet=1)["line"] == "Quiet day: nothing to write up"
    w = ai(waiting=1)
    assert w["state"] == "note" and w["details"] == ["1 brief not written up yet"]
    f = ai(model=1, template=1, why="guard")
    assert f["line"] == "1 of 2 briefs written by Othor AI"
    assert f["details"] == ["Othor AI’s words didn’t match the numbers, so templates were used"]


def test_the_licence_light():
    import datetime as dt

    def lic(**kw):
        base = {
            "state": "valid",
            "customer": "Acme",
            "edition": "enterprise",
            "expires": None,
            "grace_left": None,
            "limits": {"companies": 50},
            "companies": 12,
            "reason": "",
        }
        got = lights(_facts(licence={**base, **kw}))
        return {x["key"]: x for x in got["lights"]}["licence"], got

    ok, got = lic()
    assert ok["state"] == "ok" and ok["line"] == "Enterprise · no end date · 12 of 50 companies"
    assert got["groups"][-1]["title"] == "Working"
    dated, _ = lic(expires=dt.date(2027, 12, 31))
    assert dated["line"].startswith("Enterprise · until 31 Dec 2027")
    grace, got = lic(state="grace", grace_left=11)
    assert (
        grace["state"] == "warn" and grace["line"] == "Expired · 11 days to renew" and not got["ok"]
    )
    expired, _ = lic(state="expired")
    assert expired["state"] == "bad" and expired["line"] == "Expired · no new companies"
    none, _ = lic(state="none")
    assert none["state"] == "note"
    # without enterprise features or several companies, no Licence light at all
    assert "licence" not in {x["key"] for x in lights(_facts())["lights"]}


def test_outside_services_show_only_when_a_plugin_reaches_one():
    """Advanced Intelligence Phase 0: the hosts a plug-in declares are listed on System Health."""
    got = lights(_facts())
    assert "hosts" not in [x["key"] for x in got["lights"]]
    got = lights(_facts(hosts=["api.data.gov.in", "fbil.org.in"]))
    (light,) = [x for x in got["lights"] if x["key"] == "hosts"]
    assert light["line"] == "Also reaches 2 outside services"
    assert light["details"] == ["api.data.gov.in", "fbil.org.in"]
    assert light["state"] == "info" and got["ok"]


def test_fresh_says_daily_when_it_counts_fewer_than_every_source():
    """0.27.2 (indigo2 through MCP, 19 Nov 2026): "0 of 2 sources updated" beside "All 3
    sources answered" read as a contradiction; the Fresh line now says which sources it counts."""
    got = lights(
        _facts(
            sources=[
                _src("erp_db", status="Not updated yesterday", dot="warn"),
                _src("sales_dw", status="Not updated yesterday", dot="warn"),
                _src("Sales sheet", status="No daily numbers"),
            ]
        )
    )
    by = {x["key"]: x for x in got["lights"]}
    assert by["fresh"]["line"] == "0 of 2 daily sources updated yesterday"
    assert by["connection"]["line"] == "All 3 sources answered"


def test_numbers_with_no_data_arent_called_computed():
    """0.27.2: four confirmed numbers with "No data yet" sat beside "All 19 numbers computed"."""
    got = lights(_facts(numbers={"total": 19, "stale": [], "have": 15}))
    by = {x["key"]: x for x in got["lights"]}
    assert (
        by["numbers"]["state"] == "note" and by["numbers"]["line"] == "15 of 19 numbers have data"
    )
    assert by["numbers"]["details"] == [
        "4 numbers with no data yet: the Business Model shows which"
    ]
    got = lights(_facts(numbers={"total": 19, "stale": [], "have": 19}))
    assert {x["key"]: x for x in got["lights"]}["numbers"]["line"] == "All 19 numbers computed"
