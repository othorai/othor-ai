# SPDX-License-Identifier: Apache-2.0
"""Any model (0.37): Settings › AI Model in its states, desktop 1440 and phone 390. Dev only, run
by hand; a fake OpenAI-compatible server answers Check this model (no network):

    .venv/bin/python -m pytest tests/ui/preview_v037_any_model.py -s -p no:cacheprovider \\
        -o addopts=""

Writes ~/Desktop/othor-preview/v0.37/: 01 empty (the usual model), 02 a provider chosen, 03 key
saved, 04 check OK, 05 check failed, 06 one model per kind."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest

from othor.ai.providers import routing
from tests.ui.preview_v032_chat13_bm24 import _serve
from tests.web.conftest import make_env, signup

OUT = Path.home() / "Desktop" / "othor-preview" / "v0.37"
pytestmark = pytest.mark.browser


class Fake:
    def __init__(self) -> None:
        self.status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "invalid key"})
        content = '{"sentence": "Orders rose to {n1} this week from {n2}, with {n3} late."}'
        return httpx.Response(
            200,
            json={
                "model": "gpt-5",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 180, "completion_tokens": 24},
            },
        )


def _keep(out: list[str], e: Any) -> None:
    out.append(str(e))


def test_preview_any_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from playwright.sync_api import sync_playwright

    fake = Fake()
    orig = routing.Router.__init__

    def init(self: Any, settings: Any, default: Any, *, transport: Any = None) -> None:
        orig(self, settings, default, transport=httpx.MockTransport(fake))

    monkeypatch.setattr(routing.Router, "__init__", init)
    OUT.mkdir(parents=True, exist_ok=True)
    with make_env(
        tmp_path, signup="open", model_provider="anthropic", model_api_key="sk-install"
    ) as env:
        signup(env)
        with _serve(env.app, env.client) as (app, cookies), sync_playwright() as pw:
            br = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
            for width, tag in ((1440, ""), (390, "-phone")):
                fake.status = 200
                ctx = br.new_context(
                    viewport={"width": width, "height": 900}, device_scale_factor=2
                )
                ctx.add_cookies(cookies)  # type: ignore[arg-type]
                pg = ctx.new_page()
                errors: list[str] = []
                pg.on("pageerror", partial(_keep, errors))
                loads: list[str] = []
                pg.on("load", partial(_keep, loads))

                def shot(name: str, pg: Any = pg, tag: str = tag) -> None:
                    pg.wait_for_timeout(400)
                    pg.screenshot(path=str(OUT / f"{name}{tag}.png"), full_page=True)

                pg.goto(app + "/settings?tab=ai", wait_until="networkidle")
                shot("01-empty")
                pg.click("#model-all button:has-text('Change')")
                pg.wait_for_selector("#f-provider-all")
                pg.wait_for_timeout(300)
                pg.select_option("#f-provider-all", "openai")
                pg.wait_for_selector("input[placeholder*='GPT (OpenAI)']")
                pg.fill("#f-model_id", "gpt-5")
                shot("02-provider-chosen")
                pg.fill("#f-api_key", "sk-proj-preview-key-1234")
                pg.click("#model-pane button:has-text('Save')")
                pg.wait_for_selector("text=Saved.")
                shot("03-key-saved")
                pg.click("#model-all button:has-text('Check this model')")
                pg.wait_for_selector("text=It works")
                shot("04-check-ok")
                fake.status = 401
                pg.click("#model-all button:has-text('Check this model')")
                pg.wait_for_selector("text=It didn")
                shot("05-check-failed")
                fake.status = 200
                pg.click("#model-pane button:has-text('Choose per kind')")
                pg.wait_for_selector("#model-answering")
                pg.click("#model-answering button:has-text('Change')")
                pg.wait_for_selector("#f-provider-answering")
                pg.wait_for_timeout(300)
                pg.select_option("#f-provider-answering", "anthropic")
                pg.wait_for_selector("input[placeholder*='Claude (Anthropic)']")
                pg.fill("#f-api_key", "sk-ant-preview-key-5678")
                pg.click("#model-pane button:has-text('Save')")
                pg.wait_for_selector("text=Saved.")
                pg.click("#model-understanding button:has-text('Change')")
                pg.wait_for_selector("#f-provider-understanding")
                pg.wait_for_timeout(300)
                pg.select_option("#f-provider-understanding", "ollama")
                pg.wait_for_selector("#f-base_url")
                pg.fill("#f-base_url", "https://models.example.in:11434")
                pg.fill("#f-model_id", "qwen3:30b-a3b")
                pg.click("#model-pane button:has-text('Save')")
                pg.wait_for_selector("text=Saved.")
                shot("06-per-kind")
                assert len(loads) == 1, "a step reloaded the page"
                assert not errors, errors
                # back to the usual model for the next width, as the owner would
                pg.click("#model-all button:has-text('Back to the usual model')")
                pg.wait_for_selector("#model-all button:has-text('Change')")
                ctx.close()
            br.close()
    print("previews:", OUT)
