# SPDX-License-Identifier: Apache-2.0
"""The Othor AI chat in the app (Chat 1.0, 0.26): Ask Othor AI in every top bar opens the chat in
place; Questions for you is one message with Apply N and Suggest edits; a reply changes the set
and applies nothing; Apply writes one Business Model version; without a model, buttons."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from othor.core.models import Company
from othor.core.tenancy import system_scope, tenant_scope
from othor.ontology import service
from othor.ontology.models import Question
from tests.web.conftest import make_env, signup

PAIR = {
    "type": "rule",
    "key": "pair_net_revenue_invoiced_value",
    "body": {
        "kind": "pair",
        "text": "Net revenue moves with invoiced value",
        "over": ["net_revenue", "invoiced_value"],
        "relation": "together",
    },
}


def _questions(env: Any) -> tuple[Any, list[Question]]:
    svc = env.app.state.services
    with svc.sf() as s, system_scope("test"):
        cid = s.scalars(sa.select(Company.id).where(Company.slug == "indigothread")).one()
    with svc.sf() as s, tenant_scope(cid):
        service.confirm(s, actor="test")  # set up: an answer is a new version, not a draft
        a = Question(
            text="Which numbers move together?",
            why="",
            origin={},
            quick_replies=[],
            fingerprint=uuid.uuid4().hex,
            kind="needs",
            rank_key="1|",
            unblocks={"detectors": ["pair_break"]},
            subject={
                "suggestions": [
                    {
                        "key": "pair:a",
                        "label": "Net revenue and invoiced value",
                        "sub": "Moved the same way in 40 of 52 weeks",
                        "change": PAIR,
                        "strong": True,
                    }
                ],
                "decide": {"pick": "pair:a"},
            },
        )
        b = Question(
            text="Is the Surat shutdown every year?",
            why="",
            origin={},
            quick_replies=["Yes, every year", "No, the dates change"],
            fingerprint=uuid.uuid4().hex,
            kind="pattern",
            rank_key="2|",
            subject={"pattern": "surat_shutdown"},
        )
        s.add_all([a, b])
        s.commit()
        return cid, [a, b]


class _Got:
    def __init__(self, output: Any) -> None:
        self.output, self.guard = output, "passed"

    @property
    def ok(self) -> bool:
        return True


@pytest.fixture
def fake_model(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    said: dict[str, Any] = {}

    class GW:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def complete(
            self, company_id: Any, prompt: str, version: str, inputs: Any, history: Any = ()
        ) -> _Got:
            said["inputs"] = inputs
            return _Got(said["out"])

    import othor.ai.gateway as g

    monkeypatch.setattr(g, "ModelGateway", GW)
    return said


def test_ask_in_every_top_bar_and_questions_as_one_message(tmp_path: Path, fake_model) -> None:  # type: ignore[no-untyped-def]
    with make_env(tmp_path, signup="open") as env:
        signup(env)
        h = env.host("indigothread")
        for page in ("/home", "/settings", "/sources"):
            html = env.client.get(h + page).text
            assert "Ask Othor AI" in html and f'href="{page}?panel=othor&amp;subject=ask"' in html
            # (#86: on a Home with its title row, Ask sits left of Big Picture instead)
        cid, (_a, b) = _questions(env)
        page = env.client.get(h + "/home?panel=othor&subject=questions").text
        assert 'aria-label="Questions for you"' in page
        assert "I have 2 questions" in page and "suggested an answer for each" in page
        assert "My suggestion" in page and "Needs you" not in page and "Apply 2 answers" in page
        assert "Suggest edits" in page and 'id="oc-reply"' not in page  # no reply box at first
        assert "Drafts only" in page
        page = env.client.get(h + "/home?panel=othor&subject=questions&edit=1").text
        assert 'id="oc-reply"' in page and "Nothing is applied until you press Apply" not in page

        tok = env.csrf(h + "/home")
        svc = env.app.state.services
        with svc.sf() as s, tenant_scope(cid):
            n = service.current_number(s)
        fake_model["out"] = {
            "edits": [
                {"id": f"q:{b.id}", "action": "pick", "option": "Yes, every year", "text": None}
            ],
            "reply": "Marked it as every year.",
            "clarify": None,
        }
        back = "/home?panel=othor&subject=questions"
        r = env.client.post(
            h + "/othor/reply",
            data={"csrf": tok, "subject": "questions", "text": "Surat is every year", "back": back},
            follow_redirects=False,
        )
        assert r.status_code == 303 and r.headers["location"] == back
        assert "rows" not in str(fake_model["inputs"])
        page = env.client.get(h + back).text
        assert (
            "Marked it as every year." in page and "Changed" in page and "Apply 2 answers" in page
        )
        with svc.sf() as s, tenant_scope(cid):
            assert service.current_number(s) == n  # a reply applies nothing
        env.client.post(
            h + "/othor/apply", data={"csrf": tok, "subject": "questions", "back": back}
        )
        with svc.sf() as s, tenant_scope(cid):
            assert service.current_number(s) == n + 1
            st = {q.text: q.status for q in s.scalars(sa.select(Question))}
        # the pattern answer refers to a pattern this new company doesn't have: not written, said
        assert st == {
            "Which numbers move together?": "answered",
            "Is the Surat shutdown every year?": "open",
        }
        page = env.client.get(h + back).text
        assert "Applied 1 answer" in page and "1 couldn" in page
        assert "on the Business Model page" in page


def test_an_outside_back_address_is_refused(tmp_path: Path) -> None:
    from othor.web.routes.othor_chat import _back

    assert _back("https://evil.example/x", "ask") == "/home?panel=othor&subject=ask"
    assert _back("//evil.example/x", "ask") == "/home?panel=othor&subject=ask"
    assert _back("/sweep?panel=othor&subject=ask", "ask") == "/sweep?panel=othor&subject=ask"


FACT = {
    "op": "put",
    "type": "business_fact",
    "key": "kochi_invoicing",
    "body_json": '{"text": "Kochi plant starts invoicing in December", "kind": "other"}',
}
ALIAS = {
    "op": "put",
    "type": "alias",
    "key": "kpt",
    "body_json": '{"term": "KPT", "means": "Kochi plant"}',
}


def test_business_model_changes_are_drafted_reshaped_and_applied_once(  # type: ignore[no-untyped-def]
    tmp_path: Path, fake_model
) -> None:
    """M26.4: Tell Othor AI in the one chat: up to 5 changes, each checked by code (a type it
    can't hold is left out and said), a reply reshapes the list, Apply writes one version."""
    with make_env(tmp_path, signup="open") as env:
        signup(env)
        h = env.host("indigothread")
        svc = env.app.state.services
        with svc.sf() as s, system_scope("test"):
            cid = s.scalars(sa.select(Company.id).where(Company.slug == "indigothread")).one()
        with svc.sf() as s, tenant_scope(cid):
            service.confirm(s, actor="test")
            s.commit()
            n = service.current_number(s)
        page = env.client.get(h + "/ontology?panel=othor&subject=ontology").text
        assert "Tell me what changed in the business" in page and 'id="oc-reply"' in page
        # the old link opens the same chat
        old = env.client.get(h + "/ontology?panel=chat&subject=page").text
        assert 'id="othor-chat"' in old and "Update your Business Model" in old
        tok = env.csrf(h + "/ontology")
        back = "/ontology?panel=othor&subject=ontology"
        fake_model["out"] = {
            "changes": [
                FACT,
                ALIAS,
                {"op": "put", "type": "source", "key": "x", "body_json": "{}"},
            ],
            "reply": "Two changes for you.",
            "clarify": None,
        }
        env.client.post(  # the page's own Tell Othor AI line posts here too
            h + "/othor/reply",
            data={
                "csrf": tok,
                "subject": "ontology",
                "line": "Kochi invoices from December; KPT is Kochi",
                "back": back,
            },
        )
        assert fake_model["inputs"]["pending"] == [] and "line" in fake_model["inputs"]
        page = env.client.get(h + back).text
        assert "Kochi plant starts invoicing in December" in page and "KPT" in page
        assert "Apply 2 changes" in page and "I left out 1" in page and "Draft" in page
        with svc.sf() as s, tenant_scope(cid):
            assert service.current_number(s) == n  # drafted, nothing written
        fake_model["out"] = {"changes": [FACT], "reply": "Dropped the alias.", "clarify": None}
        env.client.post(
            h + "/othor/reply",
            data={"csrf": tok, "subject": "ontology", "text": "drop the KPT one", "back": back},
        )
        assert [p["key"] for p in fake_model["inputs"]["pending"]] == ["kochi_invoicing", "kpt"]
        page = env.client.get(h + back).text
        assert "Apply 1 change" in page and "Dropped the alias." in page
        env.client.post(h + "/othor/apply", data={"csrf": tok, "subject": "ontology", "back": back})
        env.client.post(h + "/othor/apply", data={"csrf": tok, "subject": "ontology", "back": back})
        with svc.sf() as s, tenant_scope(cid):
            assert service.current_number(s) == n + 1  # applied once
            keys = {(r.type, r.key) for r in service.active(s, ["business_fact", "alias"])}
            assert ("business_fact", "kochi_invoicing") in keys and ("alias", "kpt") not in keys
        assert f"as Business Model v{n + 1}" in env.client.get(h + back).text
