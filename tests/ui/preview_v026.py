# SPDX-License-Identifier: Apache-2.0
"""Previews of 0.26 (the one Othor AI chat, Ask, #82–#85, AI usage) for the owner. Dev only:

    .venv/bin/python -m pytest tests/ui/preview_v026.py -s -p no:cacheprovider -o addopts=""

Needs `make dev && make seed`. Every state is made by code on the sample night (a drafted change
is built by the same code the chat uses, as if Othor AI had drafted it; Ask runs without a model,
so its card is the tool's own). Desktop (1440) and phone (390) into
~/Desktop/othor-preview/v0.26/."""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_in_place import _post
from tests.web.test_othor_preview import _question
from tests.web.test_proposal_chat import _proposal
from tests.web.test_sweep_web import night  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / "v0.26"
ROOT = Path(__file__).resolve().parents[2]


def _port() -> int:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return int(so.getsockname()[1])


def _draft(svc, cid) -> None:
    """A Business Model draft as the chat holds it: two changes built and checked by code."""
    from othor.chat import changes
    from othor.chat import service as chat
    from othor.core.models import Company
    from othor.home import changes as hc
    from othor.setup.models import ChatMessage

    with svc.sf() as s, tenant_scope(cid):
        co = s.get(Company, cid)
        t = chat.thread(s, "ontology")
        said = (
            "Split open receivables by customer only, and note that Kochi starts invoicing "
            "in December"
        )
        s.add(ChatMessage(thread_id=t.id, role="user", text=said))
        raw = [
            {
                "op": "put",
                "type": "metric",
                "key": "receivables_open",
                "body_json": json.dumps({"breakdowns": ["customer"]}),
            },
            {
                "op": "put",
                "type": "business_fact",
                "key": "kochi_invoicing",
                "body_json": json.dumps(
                    {"text": "Kochi plant starts invoicing in December", "kind": "other"}
                ),
            },
        ]
        inputs = hc.model_inputs(s, said, "page", __import__("datetime").date(2026, 11, 1))
        items = []
        for n, r in enumerate(raw, start=1):
            d = hc.check(
                s,
                {"change": r, "reply": ""},
                inputs,
                said,
                __import__("datetime").date(2026, 11, 1),
            )
            assert d.change is not None, r
            items.append(changes.item(s, co, n, d.change))
        s.add(
            ChatMessage(
                thread_id=t.id,
                role="ai",
                text="Here are the 2 changes I'd make.",
                proposal={"kind": "set", "items": items},
            )
        )
        s.commit()


def test_preview_v026(night) -> None:  # noqa: F811
    import sys

    subprocess.run([sys.executable, "scripts/build_css.py"], cwd=ROOT, check=True)
    env, h, cid, rid, svc = night
    OUT.mkdir(parents=True, exist_ok=True)
    _question(svc, cid)  # a question whose pick has real rows
    pid = _proposal(svc, cid, rid)
    _draft(svc, cid)
    for q in ("show my numbers", "what did sweep find?"):  # no model: the words find the tool
        _post(env, h, "/othor/reply", {"subject": "ask", "text": q, "back": "/home"})
    from othor.connectors.models import Source

    with svc.sf() as s, tenant_scope(cid):
        sid = s.scalars(sa.select(Source.id).where(Source.name == "sales_dw")).one()
    import uvicorn

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
    shots = [
        ("01-home", "/home", True),
        ("02-questions-chat", "/home?panel=othor&subject=questions", False),
        ("03-questions-suggest-edits", "/home?panel=othor&subject=questions&edit=1", False),
        ("04-business-model-draft", "/ontology?panel=othor&subject=ontology", False),
        ("05-change-a-number", "/ontology?panel=othor&subject=ontology:metric:net_revenue", False),
        ("06-proposal-chat", f"/home?panel=othor&subject=proposal:{pid}", False),
        ("07-ask", "/home?panel=othor&subject=ask", False),
        ("08-data-sources", "/sources", True),
        ("09-source-plain-name", f"/sources?panel=manage&s={sid}", False),
        ("10-discover", "/discover", True),
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
                pg.wait_for_timeout(600)  # the lazy rows and values arrive
                pg.screenshot(path=str(OUT / f"{name}{tag}.png"), full_page=full)
            ctx.close()
        b.close()
    server.should_exit = True
    th.join(5)
    print("previews in", OUT)
