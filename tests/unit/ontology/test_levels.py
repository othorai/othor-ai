# SPDX-License-Identifier: Apache-2.0
"""Business Model 2.4 · L1: the completeness check (code, counts of live records only). The
worked example: the sample's hand-built Business Model reaches level 2 and lacks only what Sweep's
level needs beyond it; take its owners away (as the demo accounts set up through onboarding had)
and it drops below level 1, with "Owners for the main areas" to do and the sentence to say."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from othor.core.tenancy import tenant_scope
from othor.ontology import levels, service
from tests.unit.ontology.conftest import OWNER


def test_the_sample_business_model_levels(world) -> None:
    with world.sf() as s, tenant_scope(world.cid):
        lv = levels.check(s)
    done = {i.key for i in lv.items if i.done}
    # level 1 and 2 items the sample has (owners, goals, rules, parts, patterns, headline …)
    assert {"sources", "rules", "goal", "owners", "breakdowns", "parts", "patterns"} <= done
    assert {"headline", "pairs", "customers", "words"} <= done
    assert "watches" not in done  # a watch is started from Sweep or the chat, not the sample
    assert lv.level >= 2 and lv.view()["title"] == f"Level {lv.level} of 3"
    assert lv.line().endswith("left to reach full potential")
    v = lv.view()
    assert [g["level"] for g in v["groups"]] == [1, 2, 3]
    assert v["segs"][: lv.level] == [True] * lv.level


def test_without_owners_it_is_below_level_one_with_the_sentence_to_say(world) -> None:
    with world.sf() as s, tenant_scope(world.cid):
        gone = [service.retire("owner", r.key) for r in service.active(s, ["owner"])]
        service.apply(s, gone, actor=OWNER, reason="As the demo accounts: no owners")
        s.commit()
        lv = levels.check(s)
    assert lv.level == 0 and lv.title() == "Level 1 of 3 to reach"
    owners = next(i for i in lv.items if i.key == "owners")
    assert not owners.done and owners.unlocks == "Each question goes to a named person"
    q = parse_qs(urlparse(owners.url()).query)
    assert q["panel"] == ["othor"] and q["subject"] == ["ontology"]
    assert q["say"][0].startswith("The Head of Sales owns Net revenue")
    # a goal opens the goal chat (Suggest a goal); a source goes to Sources
    goal = next(i for i in lv.items if i.key == "goals")
    assert "subject=ontology%3Agoal" in goal.url() or "subject=ontology:goal" in goal.url()
    assert next(i for i in lv.items if i.key == "sources").url() == "/sources"


def test_no_model_needed(world, monkeypatch) -> None:
    """Counts only: the check never reaches a model (rule 4)."""
    import othor.ai.gateway as g

    def boom(*a, **k):  # pragma: no cover - would fail the test
        raise AssertionError("no model call")

    monkeypatch.setattr(g.ModelGateway, "complete", boom)
    with world.sf() as s, tenant_scope(world.cid):
        assert levels.check(s).items
