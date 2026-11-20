# SPDX-License-Identifier: Apache-2.0
"""0.29 checkpoint previews for the owner: the Discover 2.0 Collections dashboard on the sample's
night, desktop (1440) and phone (390), in each state the page has. Dev only, run by hand (needs
`make dev && make seed`):

    .venv/bin/python -m pytest tests/ui/preview_v029_page.py -s -p no:cacheprovider -o addopts=""
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest

from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / "discover-2"


@pytest.fixture
def dash(sample, need_sources, tmp_path, tmp_path_factory):  # type: ignore[no-untyped-def]  # noqa: F811
    from othor_ee.tests import discover_world

    yield from discover_world.world(sample, tmp_path, tmp_path_factory)


def _post(env: Any, h: str, name: str, value: str) -> None:
    tok = env.csrf(h + "/discover/collections")
    env.client.post(
        h + "/discover/collections/view", data={"csrf": tok, "name": name, "value": value}
    )


def test_preview(dash: Any) -> None:
    import uvicorn
    from playwright.sync_api import sync_playwright

    env, h, cid, _rid, svc = dash
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = int(so.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(cast(Any, env.app), host="127.0.0.1", port=port, log_level="warning")
    )
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.05)
    OUT.mkdir(parents=True, exist_ok=True)
    base = f"http://indigothread.{BASE}:{port}/discover/"

    def shots(tag: str, path: str = "", group: str = "collections") -> None:
        cookies = [
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain or f"indigothread.{BASE}",
                "path": c.path or "/",
            }
            for c in env.client.cookies.jar
        ]
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
            for width, dev in ((1440, "desktop"), (390, "phone")):
                ctx = b.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
                ctx.add_cookies(cookies)  # type: ignore[arg-type]
                pg = ctx.new_page()
                pg.goto(base + group + path, wait_until="networkidle")
                pg.screenshot(path=str(OUT / f"m29.7-{tag}-{dev}.png"), full_page=True)
                ctx.close()
            b.close()

    shots("1-dashboard")
    _post(env, h, "compare", "1")
    _post(env, h, "window", "quarter")
    shots("2-last-3-months-compare")
    _post(env, h, "compare", "")
    _post(env, h, "window", "12m")
    _post(env, h, "by", "segment")
    shots("3-by-segment")
    _post(env, h, "by", "region")
    shots("4-ask-panel", "?panel=othor&subject=ask:discover:collections")
    # the write-up as Othor AI's version shows it (no model in a preview: its words are the
    # board's own facts, stored as a write-up would be)
    _write_up(svc, cid)
    shots("5-with-write-up")
    # a group just switched on, before its first night
    from othor_ee.tests.discover_world import switch_on_all

    switch_on_all(svc, cid, "procurement")
    shots("6-before-the-first-night", group="procurement")
    # turned off
    tok = env.csrf(h + "/discover/collections")
    env.client.post(h + "/discover/collections/off", data={"csrf": tok})
    shots("7-turned-off", "?done=off")
    server.should_exit = True
    th.join(5)
    print("previews in", OUT)


def _write_up(svc: Any, cid: Any) -> None:
    import datetime as dt

    import sqlalchemy as sa

    from othor.core.tenancy import tenant_scope
    from othor.web.home_view import latest_run
    from othor_ee.discover import view
    from othor_ee.discover.models import DiscoverGroup, DiscoverSummary

    with svc.sf() as s, tenant_scope(cid):
        g = s.scalars(sa.select(DiscoverGroup).where(DiscoverGroup.key == "collections")).one()
        b = view.board_for(s, svc.settings, g, dt.date(2026, 11, 1))
        assert b is not None and latest_run(s) is not None
        facts = b["facts"]
        lead = [f for f in facts if "alone adds" in f or "agree" in f or "differ" in f][:2]
        s.add(
            DiscoverSummary(
                group_key="collections",
                run_id=str(b["run"]["id"]),
                board_hash=b["hash"],
                body={"lead": " ".join(x.rstrip(".") + "." for x in lead), "next": []},
                guard="passed",
                written_by="system",
            )
        )
        s.commit()
