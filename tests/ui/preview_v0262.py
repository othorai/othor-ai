# SPDX-License-Identifier: Apache-2.0
"""Previews of 0.26.2 for the owner. Dev only:

    .venv/bin/python -m pytest tests/ui/preview_v0262.py -s -p no:cacheprovider -o addopts=""

Needs `make dev && make seed`. On the sample night: Ask answering with each of the four new
tools (no model: the words pick the tool), the questions chat with the move-together chart and
the records in question, one question on its own, and the Applied message with Intelligence
before → after. Desktop (1440) and phone (390) into ~/Desktop/othor-preview/v0.26.2/."""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_in_place import _post
from tests.web.test_sweep_web import night  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / "v0.26.2"
ROOT = Path(__file__).resolve().parents[2]


def test_preview_v0262(night) -> None:  # noqa: F811
    import threading

    import uvicorn

    from tests.ui.preview_v026 import _port

    subprocess.run([sys.executable, "scripts/build_css.py"], cwd=ROOT, check=True)
    env, h, _cid, _rid, _svc = night
    OUT.mkdir(parents=True, exist_ok=True)
    for q in (
        "what does today's brief say?",
        "how healthy is everything?",
        "how much intelligence do we have?",
        "what is coming up next week?",
    ):
        _post(env, h, "/othor/reply", {"subject": "ask", "text": q, "back": "/home"})
    chat = env.client.get(h + "/home?panel=othor&subject=questions").text
    more = re.search(r'href="(/home\?panel=othor&amp;subject=questions&amp;item=q:[^"]+)"', chat)
    pair_item = re.search(r"item=(q%3A[0-9a-f-]+)&amp;option=pair", chat)
    shots: list[tuple[str, str, bool]] = [
        ("01-ask-four-tools", "/home?panel=othor&subject=ask", False),
        ("02-questions-with-previews", "/home?panel=othor&subject=questions", False),
    ]
    if more:
        shots.append(("03-one-question", more.group(1).replace("&amp;", "&"), False))
    if pair_item:
        qid = pair_item.group(1).replace("%3A", ":")
        shots.append(("04-pair-question", f"/home?panel=othor&subject=questions&item={qid}", False))
    port = _port()
    server = uvicorn.Server(
        uvicorn.Config(cast(Any, env.app), host="127.0.0.1", port=port, log_level="warning")
    )
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.05)
    app = f"http://indigothread.{BASE}:{port}"
    cookies = [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain or f"indigothread.{BASE}",
            "path": c.path or "/",
        }
        for c in env.client.cookies.jar
    ]
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        for width, tag in ((1440, ""), (390, "-phone")):
            ctx = b.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            for name, path, full in shots:
                pg.goto(app + path, wait_until="networkidle")
                pg.wait_for_timeout(700)
                pg.screenshot(path=str(OUT / f"{name}{tag}.png"), full_page=full)
            ctx.close()
        b.close()
    # the Applied message: Othor AI's suggestions applied, with Intelligence before → after
    _post(env, h, "/othor/apply", {"subject": "questions", "back": "/home"})
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        for width, tag in ((1440, ""), (390, "-phone")):
            ctx = b.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            pg.goto(app + "/home?panel=othor&subject=questions", wait_until="networkidle")
            pg.wait_for_timeout(500)
            pg.screenshot(path=str(OUT / f"05-applied-intelligence{tag}.png"))
            ctx.close()
        b.close()
    server.should_exit = True
    th.join(5)
    print("previews in", OUT)
