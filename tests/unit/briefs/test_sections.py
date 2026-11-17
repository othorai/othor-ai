# SPDX-License-Identifier: Apache-2.0
"""Brief sections from plug-ins (Advanced Intelligence Phase 0 §6): built from the run's
outputs, capped at the section's N items, stored beside the ranked items and never among them,
absent (the issue keeps its bytes) when no plug-in has one, and rendered in the emails."""

from __future__ import annotations

import datetime as dt

import pytest

from othor.briefs import sections, step
from tests.ui.mail.test_emails import _brief
from tests.unit.mail.world import issue_1nov, quiet_from

DAY = dt.date(2026, 11, 1)
ITEMS = [
    {
        "text": "Diesel is up 6% this week.",
        "line": "Diesel · linked to Freight cost",
        "state": "assessed",
    },
    {"text": "Cotton is down 4% this month.", "state": "made_up"},
    {"text": "The rupee fell 1% against the dollar."},
    {"text": ""},  # empty: dropped
    "not an item",
]


def _outside(spec, version, run_date, findings, watches, points, context, outputs_doc):
    seen = (context.get("run_inputs") or {}).get("demo_outside")
    if seen is None:
        return None
    return {"title": "Outside", "note": "only when a linked signal moved", "items": list(ITEMS)}


@pytest.fixture
def outside():
    sections.register_brief_section("outside", _outside, max_items=2)
    yield
    sections.SECTIONS.pop("outside", None)


def _make(monkeypatch, context):
    fixed = {"quiet": False, "items": [{"rank": 1, "fingerprint": "f1"}], "title": "T"}

    def fake_assemble(spec, v, **kw):
        return {**fixed, "items": [dict(i) for i in fixed["items"]]}

    monkeypatch.setattr(step, "assemble", fake_assemble)
    ctx = {"briefs": [{"id": "b1", "number": 1, "scope": {}}], **context}
    return step.make({}, DAY, ctx, [], [], {}, [], {})


def test_a_section_is_capped_and_kept_beside_the_ranked_items(monkeypatch, outside):
    doc = _make(monkeypatch, {"run_inputs": {"demo_outside": {"diesel": []}}})
    (issue,) = [i["issue"] for i in doc["issues"]]
    assert issue["items"] == [{"rank": 1, "fingerprint": "f1"}]  # the ranked four untouched
    (sec,) = issue["sections"]
    assert sec == {
        "key": "outside",
        "title": "Outside",
        "note": "only when a linked signal moved",
        "items": [
            {
                "text": "Diesel is up 6% this week.",
                "line": "Diesel · linked to Freight cost",
                "state": "assessed",
            },
            {"text": "Cotton is down 4% this month."},  # an unknown state is dropped
        ],
    }


def test_without_a_section_the_issue_keeps_its_bytes(monkeypatch, outside):
    doc = _make(monkeypatch, {})  # the plug-in has nothing tonight
    assert "sections" not in doc["issues"][0]["issue"]
    sections.SECTIONS.pop("outside")
    doc = _make(monkeypatch, {"run_inputs": {"demo_outside": {}}})  # no plug-in at all
    assert "sections" not in doc["issues"][0]["issue"]


def test_a_failing_section_is_left_out(monkeypatch):
    def broken(*a):
        raise RuntimeError("bad")

    sections.register_brief_section("broken", broken)
    try:
        doc = _make(monkeypatch, {})
        assert "sections" not in doc["issues"][0]["issue"]
    finally:
        sections.SECTIONS.pop("broken", None)


def test_a_taken_key_cant_be_replaced(outside):
    with pytest.raises(ValueError):
        sections.register_brief_section("outside", lambda *a: None)


SECTION = {
    "key": "outside",
    "title": "Outside · 1",
    "note": "only when a linked signal moved",
    "items": [
        {
            "text": "Diesel moved this week.",
            "line": "Diesel · linked to Freight cost",
            "state": "assessed",
        }
    ],
}


@pytest.mark.parametrize("fmt", ["simple", "full", "quiet"])
def test_the_emails_show_it(fmt):
    body = issue_1nov() if fmt != "quiet" else quiet_from(issue_1nov())
    body["sections"] = [SECTION]
    msg = _brief(body, "full" if fmt == "full" else "simple")
    for part in (msg.html, msg.text):
        assert "Outside · 1" in part and "Diesel moved this week." in part
        assert "Diesel · linked to Freight cost" in part


def test_the_emails_without_it_are_unchanged():
    a = _brief(issue_1nov())
    body = issue_1nov()
    body["sections"] = []
    b = _brief(body)
    assert a.html == b.html and a.text == b.text
