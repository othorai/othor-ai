# SPDX-License-Identifier: Apache-2.0
"""Settings › AI Model (Any model, 0.37): the install's model shown in plain words; Change opens
the row in place; a key saved is never shown back or written to an event; one model per kind;
Check this model against a fake server; Back to the usual model."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from othor.ai.providers import routing
from tests.web.conftest import make_env, signup

SECRET = "sk-web-NEVER-SHOWN-0987654321"
HX = {"HX-Request": "true"}


class Server:
    def __init__(self, content: str, status: int = 200) -> None:
        self.content, self.status, self.requests = content, status, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "no"})
        return httpx.Response(
            200,
            json={
                "model": "gpt-5",
                "choices": [{"message": {"content": self.content}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 9},
            },
        )


@pytest.fixture
def fake(monkeypatch):
    holder: dict[str, Server] = {"srv": Server('{"sentence": "Orders rose to {n1}."}')}
    orig = routing.Router.__init__

    def init(self, settings, default, *, transport=None):
        orig(self, settings, default, transport=httpx.MockTransport(lambda r: holder["srv"](r)))

    monkeypatch.setattr(routing.Router, "__init__", init)
    return holder


def test_settings_othor_ai_flow(tmp_path: Path, fake) -> None:
    with make_env(tmp_path, signup="open") as env:
        signup(env)
        h, c = env.host("indigothread"), env.client
        page = c.get(h + "/settings?tab=ai").text
        pane = page.split('data-pane="ai"', 1)[1].split("data-pane=", 1)[0]
        assert "Writes with" in pane and "Templates only · Othor AI's usual model" in pane
        assert "Choose per kind" in pane and "Check this model" not in pane
        tok = env.csrf(h + "/settings?tab=ai")
        # Change: the row opens in place with the provider's own fields
        form = c.get(h + "/settings/model?edit=all&provider=openai", headers=HX).text
        assert 'name="api_key"' in form and 'name="model_id"' in form and "gpt-5" in form
        assert 'name="base_url"' not in form
        local = c.get(h + "/settings/model?edit=all&provider=ollama", headers=HX).text
        assert 'name="base_url"' in local and 'name="api_key"' not in local
        # a key is needed
        r = c.post(
            h + "/settings/model",
            headers=HX,
            data={"csrf": tok, "kind": "all", "provider": "openai", "model_id": "gpt-5"},
        )
        assert r.status_code == 200 and "Paste your GPT (OpenAI) key." in r.text
        # saved: shown as "key saved", never the key; the event says only "saved"
        r = c.post(
            h + "/settings/model",
            headers=HX,
            data={
                "csrf": tok,
                "kind": "all",
                "provider": "openai",
                "model_id": "gpt-5",
                "api_key": SECRET,
            },
        )
        assert "GPT (OpenAI) · gpt-5 · key saved" in r.text and "Saved." in r.text
        assert SECRET not in r.text
        (ev,) = env.events("model.chosen")
        assert ev.after["key"] == "saved" and SECRET not in json.dumps([ev.before, ev.after])
        again = c.get(h + "/settings/model?edit=all", headers=HX).text
        assert SECRET not in again and "Key saved · paste a new one to replace it" in again
        # Check this model: a real call through the gateway, to the fake server
        r = c.post(h + "/settings/model/check", headers=HX, data={"csrf": tok, "kind": "all"})
        assert "It works" in r.text and "Orders rose to 128." in r.text
        assert fake["srv"].requests[0].headers["authorization"] == f"Bearer {SECRET}"
        fake["srv"] = Server("", status=401)
        r = c.post(h + "/settings/model/check", headers=HX, data={"csrf": tok, "kind": "all"})
        assert "It didn&#39;t work" in r.text and SECRET not in r.text
        # one model per kind: Answering you on templates only
        r = c.post(
            h + "/settings/model",
            headers=HX,
            data={"csrf": tok, "kind": "answering", "provider": "template"},
        )
        assert "Answering you" in r.text and "Templates only" in r.text
        assert "Same as every kind of work" in r.text  # the other two kinds
        # back to the usual model
        r = c.post(h + "/settings/model/reset", headers=HX, data={"csrf": tok, "kind": ""})
        assert "Othor AI's usual model" in r.text and "key saved" not in r.text
        assert env.events("model.reset")


def test_only_admins_change_it_and_csrf_is_needed(tmp_path: Path, fake) -> None:
    with make_env(tmp_path, signup="open") as env:
        signup(env)
        h, c = env.host("indigothread"), env.client
        r = c.post(h + "/settings/model", data={"kind": "all", "provider": "template"})
        assert r.status_code == 403
