# SPDX-License-Identifier: Apache-2.0
"""A local model through Ollama (24 Nov 2026): the same reply shape as Claude, the prompt's
schema sent as structured output, no key needed, images passed, a PDF falls back, and every call
still logged and guarded by the gateway. A stub transport stands in for Ollama (no network)."""

from __future__ import annotations

import base64
import datetime as dt
import json

import httpx
import pytest
import sqlalchemy as sa

from othor.ai.gateway import (
    Attachment,
    ModelGateway,
    OllamaProvider,
    ProviderUnavailable,
    model_status,
    provider_for,
)
from othor.ai.models import ModelCall
from othor.core.config import Settings
from othor.core.http import HostNotAllowed, outbound_client
from othor.core.tenancy import tenant_scope

NOW = dt.datetime(2026, 11, 24, 6, 0, tzinfo=dt.UTC)


class Ollama:
    """Records each request; answers like /api/chat with stream false."""

    def __init__(self, content: str = '{"lines": []}', status: int = 200) -> None:
        self.content = content
        self.status = status
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx.Response(
            self.status,
            json={
                "model": "qwen3:30b-a3b",
                "message": {"role": "assistant", "content": self.content},
                "done": True,
                "prompt_eval_count": 321,
                "eval_count": 45,
            },
        )


def _provider(fake: Ollama, **kw) -> OllamaProvider:
    return OllamaProvider(
        "http://ollama.local:11434/", "qwen3:30b-a3b", transport=httpx.MockTransport(fake), **kw
    )


def test_reply_shape_schema_and_options() -> None:
    fake = Ollama('{"answer": "Up"}')
    p = _provider(fake, context=16384, think="off")
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    reply = p.call("system words", [{"role": "user", "content": "hi"}], schema, max_tokens=99)
    assert reply is not None
    assert (reply.text, reply.model_id, reply.tokens_in, reply.tokens_out) == (
        '{"answer": "Up"}',
        "qwen3:30b-a3b",
        321,
        45,
    )
    req = fake.requests[0]
    assert req["url"] == "http://ollama.local:11434/api/chat"
    body = req["body"]
    assert body["format"] == schema
    assert body["stream"] is False
    assert body["think"] is False
    assert body["options"]["num_predict"] == 99
    assert body["options"]["num_ctx"] == 16384
    assert body["options"]["temperature"] == 0
    assert body["messages"][0] == {"role": "system", "content": "system words"}
    assert body["messages"][1] == {"role": "user", "content": "hi"}


def test_no_schema_no_format_and_think_left_to_the_model() -> None:
    fake = Ollama("plain words")
    reply = _provider(fake).call("s", [{"role": "user", "content": "x"}], None)
    assert reply is not None and reply.text == "plain words"
    assert "format" not in fake.requests[0]["body"]
    assert "think" not in fake.requests[0]["body"]


def test_thinking_and_fences_are_stripped() -> None:
    fake = Ollama('<think>hmm 42</think>\n```json\n{"a": 1}\n```')
    reply = _provider(fake).call("s", [{"role": "user", "content": "x"}], {"type": "object"})
    assert reply is not None and reply.text == '{"a": 1}'


def test_blocks_become_text_and_images() -> None:
    fake = Ollama()
    img = Attachment("image", "image/png", b"\x89PNG", "chart.png")
    msg = {"role": "user", "content": [img.block(), {"type": "text", "text": "read it"}]}
    _provider(fake).call("s", [msg], None)
    sent = fake.requests[0]["body"]["messages"][1]
    assert sent["content"] == "read it"
    assert sent["images"] == [base64.standard_b64encode(b"\x89PNG").decode()]


def test_a_pdf_is_unavailable() -> None:
    pdf = Attachment("pdf", "application/pdf", b"%PDF", "plan.pdf")
    with pytest.raises(ProviderUnavailable):
        _provider(Ollama()).call("s", [{"role": "user", "content": [pdf.block()]}], None)


def test_without_a_model_id_it_is_not_configured() -> None:
    p = OllamaProvider("http://localhost:11434", "")
    assert p.configured is False


def test_provider_for_and_status_need_no_key(session_factory, two_companies) -> None:
    a, _ = two_companies
    s = Settings(model_provider="ollama", model_id="gpt-oss:20b", model_think="low")
    p = provider_for(s)
    assert isinstance(p.default, OllamaProvider) and p.configured
    with session_factory() as ses, tenant_scope(a):
        assert model_status(s, ses, NOW) == (True, "ok")
        no_model = Settings(model_provider="ollama", model_id="")
        assert model_status(no_model, ses, NOW) == (False, "no_key")


def test_only_the_configured_host_is_let_through() -> None:
    client = outbound_client(httpx.MockTransport(Ollama()), also=frozenset({"ollama.local"}))
    with pytest.raises(HostNotAllowed):
        client.post("http://elsewhere.example/api/chat", json={})


def test_gateway_logs_and_parses_an_ollama_call(session_factory, two_companies) -> None:
    a, _ = two_companies
    fake = Ollama('{"lines": []}')
    gw = ModelGateway(session_factory, _provider(fake), clock=lambda: NOW)
    got = gw.complete(a, "brief_rewrite", "v1", {"lines": []})
    assert got.ok and got.output == {"lines": []}
    with session_factory() as s, tenant_scope(a):
        row = s.scalars(sa.select(ModelCall)).one()
        assert (row.provider, row.model_id, row.tokens_in, row.tokens_out) == (
            "ollama",
            "qwen3:30b-a3b",
            321,
            45,
        )
        assert row.guard_result == "passed"


def test_gateway_marks_a_non_json_reply(session_factory, two_companies) -> None:
    a, _ = two_companies
    gw = ModelGateway(session_factory, _provider(Ollama("not json")), clock=lambda: NOW)
    got = gw.complete(a, "brief_rewrite", "v1", {"lines": []})
    assert got.guard == "failed:not-json"


def test_gateway_survives_ollama_down(session_factory, two_companies) -> None:
    a, _ = two_companies
    gw = ModelGateway(session_factory, _provider(Ollama(status=500)), clock=lambda: NOW)
    got = gw.complete(a, "brief_rewrite", "v1", {"lines": []})
    assert got.guard == "error" and not got.ok
