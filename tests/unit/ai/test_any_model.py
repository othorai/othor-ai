# SPDX-License-Identifier: Apache-2.0
"""Any model (0.37): an OpenAI-compatible provider with presets, a company's own model per kind
of work with its key encrypted, and "Check this model". A stub transport stands in for every
provider (no network)."""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import ClassVar

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from othor.ai import choice
from othor.ai.gateway import Attachment, ModelGateway, ProviderUnavailable, model_status
from othor.ai.gateway import provider_for as gateway_provider_for
from othor.ai.models import ModelCall, ModelSetting
from othor.ai.providers import routing
from othor.ai.providers.openai_compat import OpenAICompatibleProvider
from othor.ai.providers.presets import PRESETS
from othor.core.config import Settings
from othor.core.crypto import DecryptError, InstanceKey
from othor.core.http import HostNotAllowed, outbound_client
from othor.core.models import Event
from othor.core.tenancy import tenant_scope

NOW = dt.datetime(2026, 11, 25, 6, 0, tzinfo=dt.UTC)
SECRET = "sk-test-NEVER-SHOWN-1234567890"


class Server:
    """An OpenAI-compatible /chat/completions: records requests, answers `content`."""

    def __init__(self, *contents: str, status: int = 200) -> None:
        self.contents = list(contents) or ['{"answer": "Up"}']
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "bad key " + SECRET}})
        content = self.contents.pop(0) if len(self.contents) > 1 else self.contents[0]
        return httpx.Response(
            200,
            json={
                "model": "served-model",
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30},
            },
        )

    def body(self, i: int = 0) -> dict:
        return json.loads(self.requests[i].content)


def _p(key: str, server: Server, url: str = "", model: str = "m-1", api_key: str = SECRET):
    preset = PRESETS[key]
    return OpenAICompatibleProvider(
        preset, url or preset.base_url, api_key, model, transport=httpx.MockTransport(server)
    )


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


# --- the provider -----------------------------------------------------------------------------


def test_openai_request_shape() -> None:
    srv = Server()
    reply = _p("openai", srv).call("sys", [{"role": "user", "content": "hi"}], SCHEMA, 99)
    assert reply is not None and reply.text == '{"answer": "Up"}'
    assert (reply.model_id, reply.tokens_in, reply.tokens_out) == ("served-model", 120, 30)
    req = srv.requests[0]
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {SECRET}"
    body = srv.body()
    assert body["max_completion_tokens"] == 99 and "temperature" not in body
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["messages"][0] == {"role": "system", "content": "sys"}


@pytest.mark.parametrize(
    "key,host",
    [
        ("gemini", "generativelanguage.googleapis.com"),
        ("mistral", "api.mistral.ai"),
        ("deepseek", "api.deepseek.com"),
        ("groq", "api.groq.com"),
        ("together", "api.together.xyz"),
        ("openrouter", "openrouter.ai"),
    ],
)
def test_each_preset_calls_its_own_host(key: str, host: str) -> None:
    srv = Server()
    _p(key, srv).call("s", [{"role": "user", "content": "x"}], None)
    assert srv.requests[0].url.host == host
    assert srv.body()["max_tokens"] > 0


def test_json_object_preset_puts_the_schema_in_the_instructions_and_retries_once() -> None:
    srv = Server("Sure! here you go", '```json\n{"answer": "Up"}\n```')
    reply = _p("deepseek", srv).call("sys", [{"role": "user", "content": "x"}], SCHEMA)
    assert reply is not None and reply.text == '{"answer": "Up"}'
    assert len(srv.requests) == 2
    first = srv.body(0)
    assert first["response_format"] == {"type": "json_object"}
    assert "JSON schema" in first["messages"][0]["content"]
    assert reply.tokens_in == 240  # both calls counted


def test_azure_deployment_url_version_and_key_header() -> None:
    srv = Server()
    _p("azure", srv, url="https://acme.openai.azure.com/", model="gpt-5-prod").call(
        "s", [{"role": "user", "content": "x"}], None
    )
    req = srv.requests[0]
    assert req.url.path == "/openai/deployments/gpt-5-prod/chat/completions"
    assert req.url.params["api-version"]
    assert req.headers["api-key"] == SECRET and "authorization" not in req.headers


def test_a_local_server_needs_no_key() -> None:
    srv = Server()
    p = _p("local", srv, url="http://models.example:1234/v1", api_key="")
    assert p.configured
    p.call("s", [{"role": "user", "content": "x"}], None)
    assert "authorization" not in srv.requests[0].headers


def test_images_go_as_image_urls_and_a_pdf_falls_back() -> None:
    srv = Server()
    img = Attachment("image", "image/png", b"\x89PNG", "chart.png")
    msg = {"role": "user", "content": [img.block(), {"type": "text", "text": "read"}]}
    _p("openai", srv).call("s", [msg], None)
    parts = srv.body()["messages"][1]["content"]
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    pdf = Attachment("pdf", "application/pdf", b"%PDF", "plan.pdf")
    with pytest.raises(ProviderUnavailable):
        _p("openai", srv).call("s", [{"role": "user", "content": [pdf.block()]}], None)


def test_without_a_key_or_model_it_is_not_configured() -> None:
    assert not _p("openai", Server(), api_key="").configured
    assert not _p("openai", Server(), model="").configured


def test_the_key_is_never_in_repr() -> None:
    assert SECRET not in repr(_p("openai", Server()))


def test_a_preset_host_is_reachable_only_through_a_provider_that_uses_it() -> None:
    client = outbound_client(httpx.MockTransport(Server()))
    with pytest.raises(HostNotAllowed):
        client.post("https://api.openai.com/v1/chat/completions", json={})
    srv = Server()
    p = _p("openai", srv)
    with pytest.raises(HostNotAllowed):
        p._client.post("https://elsewhere.example/v1/chat/completions", json={})


def test_env_openai_compat_preset() -> None:
    s = Settings(
        model_provider="openai_compat",
        model_preset="gemini",
        model_id="gemini-2.5-flash",
        model_api_key=SECRET,
    )
    p = gateway_provider_for(s)
    assert isinstance(p, routing.Router) and p.name == "gemini" and p.configured


# --- the gateway: same log and guards, key never logged ---------------------------------------


def test_gateway_logs_a_compatible_call_without_the_key(
    session_factory, two_companies, caplog
) -> None:
    a, _ = two_companies
    caplog.set_level(logging.DEBUG)
    gw = ModelGateway(session_factory, _p("openai", Server(status=401)), clock=lambda: NOW)
    got = gw.complete(a, "brief_rewrite", "v3", {"title": "x"})
    assert got.guard == "error" and not got.ok
    assert SECRET not in caplog.text
    with session_factory() as s, tenant_scope(a):
        row = s.scalars(sa.select(ModelCall)).one()
        assert row.provider == "openai" and row.guard_result == "error"
        assert SECRET not in json.dumps(row.input) and SECRET not in (row.output or "")


# --- a company's own model, per kind of work --------------------------------------------------


@pytest.fixture
def ikey(tmp_path) -> tuple[Settings, InstanceKey]:
    path = tmp_path / "instance.key"
    key = InstanceKey.load_or_create(path, allow_create=True)
    return Settings(instance_key_file=path, data_dir=tmp_path), key


def _choose(sf, cid, key, **kw) -> None:
    with sf() as s, tenant_scope(cid):
        choice.save(s, key, cid, actor="owner-1", now=NOW, **kw)
        s.commit()


def test_the_key_is_stored_encrypted_bound_to_the_company(
    session_factory, two_companies, ikey
) -> None:
    a, b = two_companies
    _settings, key = ikey
    _choose(
        session_factory, a, key, kind="all", provider="openai", model_id="gpt-5", api_key=SECRET
    )
    with session_factory() as s, tenant_scope(a):
        row = s.scalars(sa.select(ModelSetting)).one()
        assert row.key_ciphertext and SECRET.encode() not in row.key_ciphertext
        assert routing.decrypt_key(key, a, "openai", row.key_ciphertext) == SECRET
        with pytest.raises(DecryptError):
            routing.decrypt_key(key, b, "openai", row.key_ciphertext)
        (ev,) = s.scalars(sa.select(Event).where(Event.action == "model.chosen"))
        assert ev.after["key"] == "saved" and SECRET not in json.dumps(ev.after)


def test_an_empty_key_keeps_the_saved_one_and_a_key_is_required(
    session_factory, two_companies, ikey
) -> None:
    a, _ = two_companies
    _, key = ikey
    with session_factory() as s, tenant_scope(a):
        with pytest.raises(choice.ChoiceError) as e:
            choice.save(
                s, key, a, kind="all", provider="openai", model_id="gpt-5", actor="o", now=NOW
            )
        assert e.value.field == "api_key"
    _choose(
        session_factory, a, key, kind="all", provider="openai", model_id="gpt-5", api_key=SECRET
    )
    _choose(session_factory, a, key, kind="all", provider="openai", model_id="gpt-5-mini")
    with session_factory() as s, tenant_scope(a):
        live = routing.rows(s)["all"]
        assert live.model_id == "gpt-5-mini" and live.key_ciphertext


def test_each_kind_goes_to_its_own_model_and_never_to_another_company(
    session_factory, two_companies, ikey
) -> None:
    a, b = two_companies
    settings, key = ikey
    _choose(
        session_factory, a, key, kind="all", provider="openai", model_id="gpt-5", api_key=SECRET
    )
    _choose(session_factory, a, key, kind="answering", provider="template")
    srv = Server('{"title": "T", "line": "L", "situation": "S", "actions": []}')
    router = routing.Router(
        settings, gateway_provider_for(settings).default, transport=httpx.MockTransport(srv)
    )
    gw = ModelGateway(session_factory, router, clock=lambda: NOW)
    # Writing your reports: the company's model for all kinds
    assert gw.complete(a, "brief_rewrite", "v3", {"title": "x"}).ok
    assert srv.requests[0].headers["authorization"] == f"Bearer {SECRET}"
    # Answering you: templates only for this company
    assert gw.complete(a, "ask_answer", "v3", {"question": "x"}).guard == "template"
    # the other company keeps the install's model (templates here)
    assert gw.complete(b, "brief_rewrite", "v3", {"title": "x"}).guard == "template"
    assert len(srv.requests) == 1
    with session_factory() as s, tenant_scope(a):
        assert model_status(settings, s, NOW, "reports") == (True, "ok")
        assert model_status(settings, s, NOW, "answering") == (False, "template")
    with session_factory() as s, tenant_scope(b):
        assert model_status(settings, s, NOW, "reports") == (False, "template")


def test_back_to_the_default(session_factory, two_companies, ikey) -> None:
    a, _ = two_companies
    settings, key = ikey
    _choose(
        session_factory, a, key, kind="all", provider="openai", model_id="gpt-5", api_key=SECRET
    )
    _choose(session_factory, a, key, kind="reports", provider="mistral", model_id="m", api_key="k1")
    with session_factory() as s, tenant_scope(a):
        assert choice.reset(s, actor="o", now=NOW, kind="reports") == 1
        assert set(routing.rows(s)) == {"all"}
        assert choice.reset(s, actor="o", now=NOW) == 1
        s.commit()
        assert routing.rows(s) == {}
        assert model_status(settings, s, NOW, "reports") == (False, "template")


def test_a_private_model_address_is_refused_where_the_host_guard_is_on(
    session_factory, two_companies, ikey, monkeypatch
) -> None:
    from othor.connectors.sql import hostguard

    a, _ = two_companies
    _, key = ikey
    monkeypatch.setattr(hostguard._policy, "on", True)
    with session_factory() as s, tenant_scope(a):
        with pytest.raises(choice.ChoiceError) as e:
            choice.save(
                s,
                key,
                a,
                kind="all",
                provider="ollama",
                model_id="qwen3",
                base_url="http://169.254.169.254:11434",
                actor="o",
                now=NOW,
            )
        assert e.value.field == "base_url"


# --- Check this model -------------------------------------------------------------------------


def _check_gateway(session_factory, settings, srv) -> ModelGateway:
    router = routing.Router(
        settings, gateway_provider_for(settings).default, transport=httpx.MockTransport(srv)
    )
    return ModelGateway(session_factory, router, clock=lambda: NOW)


def test_check_this_model_ok(session_factory, two_companies, ikey) -> None:
    a, _ = two_companies
    settings, key = ikey
    _choose(session_factory, a, key, kind="all", provider="groq", model_id="llama", api_key=SECRET)
    srv = Server('{"sentence": "Orders rose to {n1} from {n2}, with {n3} late."}')
    r = choice.check(
        _check_gateway(session_factory, settings, srv),
        session_factory,
        a,
        "reports",
        NOW,
        actor="o",
    )
    assert r.ok and r.sentence == "Orders rose to 128 from 112, with 4 late."
    # made-up figures only: nothing of the company's goes out
    sent = json.loads(srv.requests[0].content)["messages"][1]["content"]
    assert "Orders this week" in sent
    # a second check calls again (never the cache)
    choice.check(
        _check_gateway(session_factory, settings, srv),
        session_factory,
        a,
        "reports",
        NOW,
        actor="o",
    )
    assert len(srv.requests) == 2
    with session_factory() as s, tenant_scope(a):
        assert routing.rows(s)["all"].check_result == "ok"
        rows = s.scalars(sa.select(ModelCall).where(ModelCall.prompt_id == "model_check")).all()
        assert len(rows) == 2 and all(r.provider == "groq" for r in rows)


def test_check_this_model_fails_plainly(session_factory, two_companies, ikey) -> None:
    a, _ = two_companies
    settings, key = ikey
    _choose(
        session_factory, a, key, kind="all", provider="openai", model_id="gpt-5", api_key=SECRET
    )
    down = choice.check(
        _check_gateway(session_factory, settings, Server(status=401)),
        session_factory,
        a,
        "answering",
        NOW,
        actor="o",
    )
    assert not down.ok and "key" in down.text and SECRET not in down.text
    digits = Server('{"sentence": "Orders rose to 128 from 112."}')
    weak = choice.check(
        _check_gateway(session_factory, settings, digits),
        session_factory,
        a,
        "answering",
        NOW,
        actor="o",
    )
    assert not weak.ok and weak.title == "It answered, but not well enough"


def test_model_down_falls_back_to_templates(session_factory, two_companies, ikey) -> None:
    """The company's model down: the brief keeps its template words, nothing raises (rule 4)."""
    from othor.briefs import rewrite

    a, _ = two_companies
    settings, key = ikey
    _choose(
        session_factory, a, key, kind="all", provider="openai", model_id="gpt-5", api_key=SECRET
    )
    gw = _check_gateway(session_factory, settings, Server(status=503))
    body = {"title": "South fell ₹1.4 Cr.", "line": "L", "situation": "S", "quiet": False}
    got = rewrite.rewrite(gw, a, body)
    assert got["source"] == "template" and got["guard"] == "error"
    assert rewrite.shown(body, got)["title"] == "South fell ₹1.4 Cr."
    with session_factory() as s, tenant_scope(a):
        assert model_status(settings, s, NOW, "reports") == (False, "down")


# --- the company's own model is outside the monthly allowance (owner, 25 Nov 2026) -----------


class _OthorKey:
    """The install's own model (Othor AI's key)."""

    name = "anthropic"
    configured = True

    def __init__(self) -> None:
        self.calls = 0

    def call(self, system, messages, schema, max_tokens=0):
        from othor.ai.gateway import ProviderReply

        self.calls += 1
        return ProviderReply('{"answer": "x"}', "claude-opus-5-5", 50, 50)


def test_own_key_is_outside_the_cap_but_othor_key_fallback_is_not(
    session_factory, two_companies, ikey
) -> None:
    from zoneinfo import ZoneInfo

    from othor.ai import usage
    from othor.core.models import Company

    a, _ = two_companies
    settings, key = ikey
    settings = settings.model_copy(
        update={"model_provider": "anthropic", "model_api_key": SecretStr("k")}
    )
    with session_factory() as s, tenant_scope(a):
        s.execute(sa.update(Company).where(Company.id == a).values(ai_cap_tokens=100))
        s.commit()
    _choose(session_factory, a, key, kind="reports", provider="groq", model_id="m", api_key=SECRET)
    srv = Server('{"title": "T", "line": "L", "situation": "S", "actions": []}')
    othor = _OthorKey()
    router = routing.Router(settings, othor, transport=httpx.MockTransport(srv))
    gw = ModelGateway(session_factory, router, clock=lambda: NOW, monthly_tokens=100)
    # Othor AI's key uses the allowance up (100 tokens) ...
    assert gw.complete(a, "ask_answer", "v3", {"question": "q1"}).ok
    assert gw.complete(a, "ask_answer", "v3", {"question": "q2"}).guard == "capped"
    assert othor.calls == 1
    # ... the company's own key still writes, again and again
    for i in range(3):
        assert gw.complete(a, "brief_rewrite", "v3", {"title": f"t{i}"}).ok
    assert len(srv.requests) == 3
    with session_factory() as s, tenant_scope(a):
        rows = s.scalars(sa.select(ModelCall)).all()
        assert sorted((r.provider, r.own_key) for r in rows) == [
            ("anthropic", False),
            ("groq", True),
            ("groq", True),
            ("groq", True),
        ]
        tz = ZoneInfo("Asia/Kolkata")
        # AI usage lists every call; the allowance counts Othor AI's key only
        assert usage.totals(s, NOW.date().replace(day=1), tz).calls == 4
        assert usage.used_this_month(s, tz, NOW) == 100
        assert model_status(settings, s, NOW, "reports") == (True, "ok")
        assert model_status(settings, s, NOW, "answering") == (False, "capped")


# --- an own model server: checked again at connect, connected to the checked address ----------


class _Recorder:
    """httpcore's backend stand-in: records where a connection would go, opens none."""

    opened: ClassVar[list[str]] = []

    def connect_tcp(self, host, port, **kw):
        _Recorder.opened.append(host)
        raise ConnectionRefusedError("recorded")

    def sleep(self, seconds):
        pass


def _resolve(monkeypatch, answers: list[str]) -> list[str]:
    import socket

    asked: list[str] = []

    def fake(host, port, *a, **kw):
        asked.append(host)
        ip = answers[min(len(asked), len(answers)) - 1]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return asked


def _guard_on(monkeypatch) -> None:
    import httpcore

    from othor.connectors.sql import hostguard

    monkeypatch.setattr(hostguard._policy, "on", True)
    monkeypatch.setattr(httpcore, "SyncBackend", _Recorder)
    _Recorder.opened = []


def test_an_own_server_is_connected_at_the_address_that_was_checked(
    session_factory, two_companies, ikey, monkeypatch
) -> None:
    a, _ = two_companies
    settings, key = ikey
    _guard_on(monkeypatch)
    asked = _resolve(monkeypatch, ["93.184.216.34"])
    _choose(
        session_factory,
        a,
        key,
        kind="all",
        provider="ollama",
        model_id="qwen3",
        base_url="http://models.example.in:11434",
    )
    gw = ModelGateway(session_factory, gateway_provider_for(settings), clock=lambda: NOW)
    got = gw.complete(a, "brief_rewrite", "v3", {"title": "x"})
    assert got.guard == "error"  # the recorder opens nothing
    assert _Recorder.opened == ["93.184.216.34"]  # the checked address, not the name
    assert asked[-1] == "models.example.in"


def test_a_name_that_turns_private_after_the_check_is_never_reached(
    session_factory, two_companies, ikey, monkeypatch
) -> None:
    a, _ = two_companies
    settings, key = ikey
    _guard_on(monkeypatch)
    # public when saved and when the provider is built; private when the call connects
    _resolve(monkeypatch, ["93.184.216.34", "93.184.216.34", "10.0.0.5"])
    _choose(
        session_factory,
        a,
        key,
        kind="all",
        provider="local",
        model_id="m",
        base_url="http://models.example.in:1234/v1",
    )
    gw = ModelGateway(session_factory, gateway_provider_for(settings), clock=lambda: NOW)
    got = gw.complete(a, "brief_rewrite", "v3", {"title": "x"})
    assert got.guard == "error" and not got.ok
    assert _Recorder.opened == []  # no connection to anything
