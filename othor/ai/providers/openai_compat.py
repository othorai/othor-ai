# SPDX-License-Identifier: Apache-2.0
"""One provider for every OpenAI-compatible chat completions API (Any model, 0.37): OpenAI,
Gemini's compatible endpoint, Azure OpenAI, Mistral, DeepSeek, Groq, Together, OpenRouter and a
local server (LM Studio, vLLM, llama.cpp).

The prompt's output schema goes as `response_format` json_schema where the preset accepts it;
otherwise as json_object with the schema in the instructions, and a reply that isn't JSON is
asked for once more. The gateway still parses and guards every reply. Requests go through the one
outbound client with only this provider's host let through. The key lives in this object only:
never in a log line, an error or the call log.
"""

from __future__ import annotations

import json
import re
from typing import Any

from othor.ai.gateway import MAX_TOKENS, Message, ProviderReply, ProviderUnavailable
from othor.ai.providers.presets import Preset

_THINK = re.compile(r"^\s*<think>.*?</think>\s*", re.S)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)
AZURE_API_VERSION = "2024-10-21"


def clean(text: str) -> str:
    """A reasoning model's <think> block and a ```json fence are not the reply."""
    text = _THINK.sub("", text)
    fenced = _FENCE.match(text)
    return fenced.group(1) if fenced else text


def _is_json(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except (TypeError, ValueError):
        return False


class OpenAICompatibleProvider:
    def __init__(
        self,
        preset: Preset,
        base_url: str,
        api_key: str,
        model_id: str,
        *,
        transport: Any = None,
        timeout: float = 600.0,
    ) -> None:
        import httpx

        from othor.core.http import outbound_client

        self.name = preset.key
        self._p = preset
        self._key = api_key
        self._model = model_id
        base = base_url.rstrip("/")
        if preset.azure:
            self._url = f"{base}/openai/deployments/{model_id}/chat/completions"
        else:
            self._url = f"{base}/chat/completions"
        self.configured = bool(base and model_id and (api_key or not preset.needs_key))
        host = httpx.URL(self._url).host if base else ""
        self._client = outbound_client(
            transport, also=frozenset({host}) if host else frozenset(), timeout=timeout
        )

    def __repr__(self) -> str:  # never the key
        return f"OpenAICompatibleProvider({self.name}, {self._model})"

    def _headers(self) -> dict[str, str]:
        if not self._key:
            return {}
        if self._p.azure:
            return {"api-key": self._key}
        return {"Authorization": f"Bearer {self._key}"}

    @staticmethod
    def _message(m: Message) -> dict[str, Any]:
        content = m.get("content")
        if isinstance(content, str):
            return {"role": m["role"], "content": content}
        parts: list[dict[str, Any]] = []
        for b in content or []:
            kind = b.get("type")
            if kind == "text":
                parts.append({"type": "text", "text": str(b.get("text", ""))})
            elif kind == "image":
                src = b["source"]
                url = f"data:{src['media_type']};base64,{src['data']}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
            else:
                # a PDF: not every compatible API reads one; the caller falls back
                raise ProviderUnavailable(f"{kind} not readable by this model")
        if all(p["type"] == "text" for p in parts):
            return {"role": m["role"], "content": "\n\n".join(p["text"] for p in parts)}
        return {"role": m["role"], "content": parts}

    def _body(
        self, system: str, messages: list[Message], schema: dict[str, Any] | None, max_tokens: int
    ) -> dict[str, Any]:
        if schema is not None and self._p.json_mode != "json_schema":
            system = (
                f"{system}\n\nReply with one JSON object only, no other text, matching this JSON "
                f"schema:\n{json.dumps(schema, sort_keys=True)}"
            )
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                *(self._message(m) for m in messages),
            ],
            self._p.max_tokens_field: max_tokens,
        }
        if self._p.temperature:
            body["temperature"] = 0
        if self._p.seed:
            body["seed"] = 7
        if schema is not None:
            if self._p.json_mode == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "output", "schema": schema, "strict": False},
                }
            else:
                body["response_format"] = {"type": "json_object"}
        return body

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        params = {"api-version": AZURE_API_VERSION} if self._p.azure else None
        resp = self._client.post(self._url, json=body, headers=self._headers(), params=params)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}

    def call(
        self,
        system: str,
        messages: list[Message],
        schema: dict[str, Any] | None,
        max_tokens: int = MAX_TOKENS,
    ) -> ProviderReply | None:
        if not self.configured:
            raise ProviderUnavailable("not set up")
        body = self._body(system, messages, schema, max_tokens)
        tokens_in = tokens_out = 0
        text, model = "", self._model
        for attempt in (1, 2):
            data = self._post(body)
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            if msg.get("refusal"):
                return None
            usage = data.get("usage") or {}
            tokens_in += int(usage.get("prompt_tokens") or 0)
            tokens_out += int(usage.get("completion_tokens") or 0)
            model = str(data.get("model") or self._model)
            text = clean(str(msg.get("content") or ""))
            # JSON only (no schema enforced): one more try when the reply isn't JSON
            if schema is None or self._p.json_mode == "json_schema" or _is_json(text):
                break
            if attempt == 1:
                body = {
                    **body,
                    "messages": [
                        *body["messages"],
                        {"role": "assistant", "content": text[:2000]},
                        {
                            "role": "user",
                            "content": "That wasn't one JSON object. Reply again "
                            "with only the JSON object.",
                        },
                    ],
                }
        return ProviderReply(text, model, tokens_in, tokens_out)
