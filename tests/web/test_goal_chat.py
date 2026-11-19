# SPDX-License-Identifier: Apache-2.0
"""0.28 (owner, 19 Nov 2026): a goal is added in one chat line; what the owner leaves out,
Othor AI suggests (targets computed by code, never a model's figure); one card with its choices,
Apply and Suggest edits; Apply writes one Business Model version."""

from __future__ import annotations

import html

from othor.core.tenancy import tenant_scope
from othor.ontology import service
from tests.web.test_ask import _fake
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_in_place import _post
from tests.web.test_sweep_web import night  # noqa: F401

SUBJECT = "ontology:goal"
BACK = "/ontology?panel=othor&subject=" + SUBJECT


def _read(**kw):  # type: ignore[no-untyped-def]
    base = {
        "metric": None,
        "target": None,
        "by": None,
        "direction": None,
        "aggregate": None,
        "reply": "",
        "clarify": None,
    }
    return {**base, **kw}


def _goals(svc, cid):  # type: ignore[no-untyped-def]
    with svc.sf() as s, tenant_scope(cid):
        return {r.key: dict(r.body) for r in service.active(s, ["goal"])}


def _chat(env, h):  # type: ignore[no-untyped-def]
    return html.unescape(env.client.get(h + BACK).text)


def test_the_chat_says_what_a_goal_needs_before_the_first_line(night):  # noqa: F811
    env, h, *_ = night
    page = _chat(env, h)
    assert "Add a goal" in page and "which number, the target, and by when" in page
    assert "Net revenue at least ₹60 Cr this financial year" in page  # an example to copy
    on = html.unescape(env.client.get(h + "/ontology").text)
    assert "subject=ontology:goal" in on  # Add a goal opens the chat


def test_a_line_without_a_target_gets_a_computed_one_and_apply_writes_it(night, monkeypatch):  # noqa: F811
    env, h, cid, _rid, svc = night
    _fake(
        monkeypatch,
        [
            _read(metric="net_revenue", direction="at_least"),
            _read(metric="net_revenue", direction="at_least"),  # "make it the stretch"
        ],
    )
    _post(env, h, "/othor/reply", {"subject": SUBJECT, "text": "grow net revenue", "back": BACK})
    page = _chat(env, h)
    assert "Target: where it's heading" in page and "by the end of this financial year" in page
    assert "I suggested the target and the date from your numbers." in page
    assert "Or one of these" not in page  # one suggestion, Apply and Suggest edits (owner)
    heading = page.split("Net revenue at least ", 1)[1].split(" for the year", 1)[0]
    # Suggest edits: a computed target asked for by name
    _post(env, h, "/othor/reply", {"subject": SUBJECT, "text": "make it the stretch", "back": BACK})
    page = _chat(env, h)
    assert "Target: a stretch" in page
    stretch = page.rsplit("Net revenue at least ", 1)[1].split(" for the year", 1)[0]
    assert stretch != heading
    before = set(_goals(svc, cid))
    _post(env, h, "/othor/apply", {"subject": SUBJECT, "back": BACK})
    after = _goals(svc, cid)
    (new,) = set(after) - before
    assert after[new]["metric"] == "net_revenue" and after[new]["aggregate"] == "fiscal_year_total"
    assert "Applied 1 change" in _chat(env, h)


def test_the_owners_own_figure_and_date_are_kept_and_an_invented_one_dropped(night, monkeypatch):  # noqa: F811
    env, h, *_ = night
    _fake(
        monkeypatch,
        [
            _read(metric="net_revenue", target="60 Cr", by="2027-03-31", direction="at_least"),
            _read(metric="net_revenue", target="75 Cr"),  # not in the owner's words: dropped
        ],
    )
    _post(
        env,
        h,
        "/othor/reply",
        {"subject": SUBJECT, "text": "net revenue at least 60 cr by march", "back": BACK},
    )
    page = _chat(env, h)
    assert "Net revenue at least ₹60 Cr for the year to 31 Mar 2027" in page
    assert "Here's your goal" in page
    _post(env, h, "/othor/reply", {"subject": SUBJECT, "text": "make it bigger", "back": BACK})
    page = _chat(env, h)
    assert "₹75 Cr" not in page  # Othor AI never sets a target of its own
    assert "Net revenue at least ₹60 Cr" in page  # the owner's earlier figure stays


def test_changing_a_goals_date_replaces_it(night, monkeypatch):  # noqa: F811
    env, h, cid, _rid, svc = night
    key = next(iter(_goals(svc, cid)))
    subject = f"{SUBJECT}:{key}"
    back = "/ontology?panel=othor&subject=" + subject
    assert "What should change about this goal" in html.unescape(env.client.get(h + back).text)
    _fake(monkeypatch, [_read(by="2027-02-28")])
    _post(env, h, "/othor/reply", {"subject": subject, "text": "by end of february", "back": back})
    _post(env, h, "/othor/apply", {"subject": subject, "back": back})
    after = _goals(svc, cid)
    assert key not in after
    assert any(b["by"] == "2027-02-28" for b in after.values())


def test_without_a_number_it_asks_which(night, monkeypatch):  # noqa: F811
    env, h, cid, _rid, svc = night
    before = _goals(svc, cid)
    _fake(monkeypatch, [_read(clarify=None)])
    _post(env, h, "/othor/reply", {"subject": SUBJECT, "text": "hit 60 by march", "back": BACK})
    page = _chat(env, h)
    # no number named: the briefs' headline numbers are suggested, each with a computed target
    assert "I suggested the number" in page or "Which number is the goal for?" in page
    assert _goals(svc, cid) == before


def test_a_goal_is_removed_from_its_own_chat_without_a_model(night, monkeypatch):  # noqa: F811
    env, h, cid, _rid, svc = night
    key = next(iter(_goals(svc, cid)))
    subject = f"{SUBJECT}:{key}"
    back = "/ontology?panel=othor&subject=" + subject

    def no_model(*a, **k):  # type: ignore[no-untyped-def]
        raise AssertionError("removing a goal needs no model")

    from othor.ai.gateway import ModelGateway

    monkeypatch.setattr(ModelGateway, "complete", no_model)
    _post(env, h, "/othor/reply", {"subject": subject, "text": "remove this goal", "back": back})
    page = html.unescape(env.client.get(h + back).text)
    assert "Remove · Goal" in page and "I'll remove this goal when you apply." in page
    _post(env, h, "/othor/apply", {"subject": subject, "back": back})
    assert key not in _goals(svc, cid)
