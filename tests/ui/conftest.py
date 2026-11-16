# SPDX-License-Identifier: Apache-2.0
"""A live app for browser tests: SQLite, memory mail relay, a signed-up owner of Indigo Thread."""

from __future__ import annotations

import re
import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn

from othor.core.config import Settings
from othor.mail.relay import MemoryRelay
from othor.migrations.runner import upgrade
from othor.web.app import create_app

BASE = "othor.localhost"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[int, str]]:
    tmp = tmp_path_factory.mktemp("live")
    url = f"sqlite:///{tmp / 'live.db'}"
    upgrade(url)
    relay = MemoryRelay()
    app = create_app(
        Settings(
            _env_file=None,
            db_url=url,
            data_dir=str(tmp),
            env="test",  # type: ignore[call-arg]
            base_domain=BASE,
            mail_relay="memory",
        ),
        relay=relay,
    )
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)

    def client(host: str) -> httpx.Client:
        return httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Host": f"{host}:{port}"},
            follow_redirects=False,
        )

    root, co = client(BASE), client(f"indigothread.{BASE}")
    root.get("/signup")
    tok = root.cookies["othor_csrf"]
    root.post(
        "/signup",
        data={
            "csrf": tok,
            "name": "Uday Kamath",
            "email": "uk@indigothread.example",
            "company": "Indigo Thread Co.",
        },
    )
    code = "".join(re.findall(r"\d", relay.outbox[-1].subject))
    root.post("/signup/verify", data={"csrf": tok, "code": code})
    r = root.post(
        "/signup/address",
        data={
            "csrf": tok,
            "slug": "indigothread",
            "website": "",
            "fy": "4",
            "currency": "INR",
            "tz": "Asia/Kolkata",
        },
    )
    handoff = r.headers["location"].split(f":{port}", 1)[1]
    session = co.get(handoff).cookies["othor_session"]
    yield port, session
    server.should_exit = True
    t.join(timeout=5)
