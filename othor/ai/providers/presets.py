# SPDX-License-Identifier: Apache-2.0
"""The providers a company can pick in Settings › AI Model (Any model, 0.37).

Anthropic and Ollama have providers of their own; every other one speaks the OpenAI-compatible
chat completions format, so a preset is only its base URL and what it accepts. A preset's host is
let through the one outbound client only while a company (or the install) has chosen it: nothing
here widens the allow list by itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Preset:
    key: str
    label: str  # as the owner sees it
    wire: str  # anthropic · openai · ollama · none
    base_url: str = ""  # empty: the owner types it (Azure, a local server)
    needs_key: bool = True
    needs_url: bool = False
    # structured output: "json_schema" (the prompt's schema is enforced), "json_object" (JSON
    # only; the schema goes in the instructions and our parser checks it)
    json_mode: str = "json_schema"
    max_tokens_field: str = "max_tokens"
    temperature: bool = True  # sends temperature 0 (some reasoning models refuse any other)
    seed: bool = False
    azure: bool = False
    models: tuple[str, ...] = field(default_factory=tuple)  # suggestions; any id can be typed
    url_hint: str = ""


PRESETS: dict[str, Preset] = {
    p.key: p
    for p in (
        Preset("template", "Templates only", "none", needs_key=False),
        Preset(
            "anthropic",
            "Claude (Anthropic)",
            "anthropic",
            "https://api.anthropic.com",
            models=("claude-opus-5-5",),
        ),
        Preset(
            "openai",
            "GPT (OpenAI)",
            "openai",
            "https://api.openai.com/v1",
            max_tokens_field="max_completion_tokens",
            temperature=False,
            seed=True,
            models=("gpt-5", "gpt-5-mini", "gpt-4.1"),
        ),
        Preset(
            "gemini",
            "Gemini (Google)",
            "openai",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            models=("gemini-2.5-pro", "gemini-2.5-flash"),
        ),
        Preset(
            "azure",
            "Azure OpenAI",
            "openai",
            needs_url=True,
            azure=True,
            max_tokens_field="max_completion_tokens",
            temperature=False,
            url_hint="https://your-resource.openai.azure.com",
        ),
        Preset(
            "mistral",
            "Mistral",
            "openai",
            "https://api.mistral.ai/v1",
            models=("mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"),
        ),
        Preset(
            "deepseek",
            "DeepSeek",
            "openai",
            "https://api.deepseek.com/v1",
            json_mode="json_object",
            models=("deepseek-chat", "deepseek-reasoner"),
        ),
        Preset(
            "groq",
            "Groq",
            "openai",
            "https://api.groq.com/openai/v1",
            json_mode="json_object",
            seed=True,
            models=("llama-3.3-70b-versatile", "openai/gpt-oss-120b"),
        ),
        Preset(
            "together",
            "Together AI",
            "openai",
            "https://api.together.xyz/v1",
            json_mode="json_object",
            seed=True,
            models=("meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen3-235B-A22B-fp8-tput"),
        ),
        Preset(
            "openrouter",
            "OpenRouter",
            "openai",
            "https://openrouter.ai/api/v1",
            seed=True,
            models=("anthropic/claude-opus-5.5", "openai/gpt-5", "google/gemini-2.5-pro"),
        ),
        Preset(
            "ollama",
            "Ollama (your own machine)",
            "ollama",
            needs_key=False,
            needs_url=True,
            models=("qwen3:30b-a3b", "gpt-oss:20b", "llama3.3:70b"),
            url_hint="http://localhost:11434",
        ),
        Preset(
            "local",
            "Another model server (LM Studio, vLLM, llama.cpp)",
            "openai",
            needs_key=False,
            needs_url=True,
            seed=True,
            url_hint="http://localhost:1234/v1",
        ),
    )
}


def preset(key: str) -> Preset | None:
    return PRESETS.get(key)


def base_url_of(key: str, typed: str = "") -> str:
    """The URL the provider is called at: the owner's (Azure, local) or the preset's."""
    p = PRESETS.get(key)
    if p is None:
        return ""
    return (typed.strip() if p.needs_url else p.base_url).rstrip("/")
