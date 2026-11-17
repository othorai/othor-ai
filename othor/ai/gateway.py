# SPDX-License-Identifier: Apache-2.0
"""ModelGateway: the only way to call a model (docs/06 "Gateway").

`complete(company_id, prompt_id, version, inputs, schema)` loads the versioned prompt file, checks
the input guards, answers from the cache when the same input was seen, otherwise calls the
provider, and logs every call to `model_call`. Providers:

- `template`: no model. Returns no draft; callers keep their rules/template text. The default,
  and what the nightly run would see if it could call a model (it can't: import contract).
- `anthropic`: the Claude API through the official SDK. Model from `OTHOR_MODEL_ID`.
- a fake provider in tests.

A provider failure never raises to the caller: the result says "draft unavailable".
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.ai.guards import check_input
from othor.ai.models import ModelCall
from othor.ai.usage import over_cap
from othor.core.config import Settings
from othor.core.models import Company
from othor.core.tenancy import Repo, current_company_id, tenant_scope

log = logging.getLogger("othor.ai")
PROMPTS = Path(__file__).resolve().parent / "prompts"
DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 4096
# prompts whose structured output is long (15 metrics × 20 fields): a cut-off reply isn't JSON
PROMPT_MAX_TOKENS = {"draft_metrics": 16000, "big_picture": 6000}


@dataclass(frozen=True)
class ProviderReply:
    text: str
    model_id: str
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass(frozen=True)
class Attachment:
    """A file the owner uploaded, sent to the model as a document or image block."""

    kind: str  # pdf · image
    media_type: str
    data: bytes
    name: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def block(self) -> dict[str, Any]:
        b64 = base64.standard_b64encode(self.data).decode()
        kind = "document" if self.kind == "pdf" else "image"
        return {
            "type": kind,
            "source": {"type": "base64", "media_type": self.media_type, "data": b64},
        }


Message = dict[str, Any]  # {"role": "user"|"assistant", "content": str | list[block]}


class Provider(Protocol):
    name: str
    configured: bool

    def call(
        self,
        system: str,
        messages: list[Message],
        schema: dict[str, Any] | None,
        max_tokens: int = MAX_TOKENS,
    ) -> ProviderReply | None:
        """None = the model declined (or template mode)."""


class TemplateProvider:
    name = "template"
    configured = False

    def call(
        self,
        system: str,
        messages: list[Message],
        schema: dict[str, Any] | None,
        max_tokens: int = MAX_TOKENS,
    ) -> ProviderReply | None:
        return None


class AnthropicProvider:
    """The Claude API through the official SDK. Without a key it is not configured: callers get
    'unavailable' and nothing is sent or logged. The key is held by the SDK client only."""

    name = "anthropic"

    def __init__(self, api_key: str, model_id: str, effort: str = "medium") -> None:
        import anthropic

        from othor.core.http import ALLOWED_HOSTS

        assert "api.anthropic.com" in ALLOWED_HOSTS
        self.configured = bool(api_key)
        self._client = (
            anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=600.0) if api_key else None
        )
        self._model = model_id or DEFAULT_MODEL
        self._effort = effort

    def call(
        self,
        system: str,
        messages: list[Message],
        schema: dict[str, Any] | None,
        max_tokens: int = MAX_TOKENS,
    ) -> ProviderReply | None:
        if self._client is None:
            raise ProviderUnavailable("no key")
        output_config: dict[str, Any] = {"effort": self._effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        resp = self._client.messages.create(  # type: ignore[call-overload]
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            output_config=output_config,
        )
        if resp.stop_reason == "refusal":
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return ProviderReply(
            text, resp.model, int(resp.usage.input_tokens), int(resp.usage.output_tokens)
        )


class ProviderUnavailable(RuntimeError):
    """No model to call (template, no key). Not an error to log."""


@dataclass(frozen=True)
class Result:
    output: dict[str, Any] | None  # parsed JSON when a schema was given
    text: str | None
    guard: str  # passed · template · unavailable · capped · error · refused · refused-input · …
    cached: bool = False

    @property
    def ok(self) -> bool:
        return self.guard == "passed" and (self.output is not None or self.text is not None)


def _prompt_folder(prompt_id: str) -> Path:
    """The core's prompts first, then each plug-in's (M20.1); a plug-in can't replace one."""
    from othor.core import plugins

    for root in (PROMPTS, *(d for p in plugins.loaded() for d in p.prompts)):
        if (root / prompt_id).is_dir():
            return root / prompt_id
    return PROMPTS / prompt_id


def load_prompt(prompt_id: str, version: str) -> tuple[str, dict[str, Any] | None]:
    folder = _prompt_folder(prompt_id)
    system = (folder / f"{version}.md").read_text()
    schema_file = folder / f"{version}.output.json"
    schema = json.loads(schema_file.read_text()) if schema_file.exists() else None
    return system, schema


def input_hash(prompt_id: str, version: str, inputs: Any, extra: Any = None) -> str:
    blob = json.dumps(
        [prompt_id, version, inputs, extra], sort_keys=True, ensure_ascii=False, default=str
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def provider_for(settings: Settings) -> Provider:
    if settings.model_provider == "anthropic":
        return AnthropicProvider(settings.model_api_key.get_secret_value(), settings.model_id)
    return TemplateProvider()


DOWN_FOR = 15 * 60  # seconds: a failed call marks the model unavailable this long


def model_status(settings: Settings, s: Session, now: Any) -> tuple[bool, str]:
    """(available, why): template · no_key · capped · down · ok. Inside a tenant scope."""
    if settings.model_provider == "template":
        return False, "template"
    if not settings.model_api_key.get_secret_value():
        return False, "no_key"
    company = Repo(s).get(Company, current_company_id())
    if company is not None and over_cap(
        s, company.ai_cap_tokens, settings.ai_monthly_tokens, ZoneInfo(company.timezone), now
    ):
        return False, "capped"
    last = s.scalars(
        sa.select(ModelCall)
        .where(ModelCall.guard_result != "cached")
        .order_by(ModelCall.created_at.desc())
        .limit(1)
    ).first()
    if last is not None and last.guard_result == "error":
        age = (now - last.created_at).total_seconds()
        if age < DOWN_FOR:
            return False, "down"
    return True, "ok"


class ModelGateway:
    def __init__(
        self,
        sf: sessionmaker[Session],
        provider: Provider,
        clock: Callable[[], Any] | None = None,
        *,
        monthly_tokens: int = 0,
    ) -> None:
        self.sf = sf
        self.provider = provider
        self.clock = clock
        # the install's monthly AI token cap per company (0 = none; a company may have its own)
        self.monthly_tokens = monthly_tokens

    def _now(self) -> dt.datetime:
        return self.clock() if self.clock is not None else dt.datetime.now(dt.UTC)

    def complete(
        self,
        company_id: uuid.UUID,
        prompt_id: str,
        version: str,
        inputs: dict[str, Any],
        *,
        personal_names: tuple[str, ...] = (),
        attachments: tuple[Attachment, ...] = (),
        history: tuple[Message, ...] = (),
    ) -> Result:
        """One call: the prompt file as system, the conversation so far, then the inputs (JSON)
        with any attachments. Cached by input hash (attachments by their SHA-256)."""
        system, schema = load_prompt(prompt_id, version)
        try:
            check_input(inputs, personal_names)
        except ValueError:
            return Result(None, None, "refused-input")
        files = [{"name": a.name, "sha256": a.sha256, "kind": a.kind} for a in attachments]
        h = input_hash(prompt_id, version, inputs, [files, list(history)])
        if not getattr(self.provider, "configured", False):
            return Result(
                None, None, "template" if self.provider.name == "template" else "unavailable"
            )
        with self.sf() as s, tenant_scope(company_id):
            hit = s.scalars(
                sa.select(ModelCall)
                .where(
                    ModelCall.prompt_id == prompt_id,
                    ModelCall.prompt_version == version,
                    ModelCall.input_hash == h,
                    ModelCall.guard_result == "passed",
                )
                .order_by(ModelCall.created_at.desc())
                .limit(1)
            ).first()
            if hit is not None and hit.output is not None:
                return Result(_parse(hit.output, schema), hit.output, "passed", cached=True)
            # the monthly cap (open sign-up, owner 16 Nov 2026): past it, no new call reaches the
            # provider until the 1st; callers fall back as for any unavailable draft (rule 4)
            company = Repo(s).get(Company, company_id)
            if company is not None and over_cap(
                s,
                company.ai_cap_tokens,
                self.monthly_tokens,
                ZoneInfo(company.timezone),
                self._now(),
            ):
                return Result(None, None, "capped")
        t0 = time.perf_counter()
        user_text = json.dumps(inputs, ensure_ascii=False, sort_keys=True, default=str)
        content: list[dict[str, Any]] = [a.block() for a in attachments]
        content.append({"type": "text", "text": user_text})
        messages = [*history, {"role": "user", "content": content}]
        guard, reply = "passed", None
        try:
            reply = self.provider.call(
                system, messages, schema, max_tokens=PROMPT_MAX_TOKENS.get(prompt_id, MAX_TOKENS)
            )
            if reply is None:
                guard = "refused"
        except ProviderUnavailable:
            return Result(None, None, "unavailable")
        except Exception as exc:
            # the exception's type only: messages can carry request details
            log.warning("model call failed (%s): %s", prompt_id, type(exc).__name__)
            guard = "error"
        parsed = None
        if reply is not None:
            parsed = _parse(reply.text, schema)
            if schema is not None and parsed is None:
                guard = "failed:not-json"
        with self.sf() as s, tenant_scope(company_id):
            s.add(
                ModelCall(
                    prompt_id=prompt_id,
                    prompt_version=version,
                    provider=self.provider.name,
                    model_id=reply.model_id if reply else "",
                    input_hash=h,
                    input={"inputs": inputs, "files": files, "turns": len(history)},
                    output=reply.text if reply else None,
                    tokens_in=reply.tokens_in if reply else 0,
                    tokens_out=reply.tokens_out if reply else 0,
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    guard_result=guard,
                )
            )
            s.commit()
        if guard != "passed":
            return Result(None, None, guard)
        return Result(parsed, reply.text if reply else None, "passed")


def _parse(text: str, schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if schema is None:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None
