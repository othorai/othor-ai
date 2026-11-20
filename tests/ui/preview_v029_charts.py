# SPDX-License-Identifier: Apache-2.0
"""0.29 checkpoint previews for the owner: the dashboard's chart kinds on /_components/9c, desktop
and phone. Dev only, run by hand:

    .venv/bin/python -m pytest tests/ui/preview_v029_charts.py -s -p no:cacheprovider -o addopts=""
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Any, cast

from othor.core.config import Settings
from othor.migrations.runner import upgrade
from othor.web.app import create_app

OUT = Path.home() / "Desktop" / "othor-preview" / "discover-2"


def test_preview(tmp_path: Path) -> None:
    import uvicorn
    from playwright.sync_api import sync_playwright

    url = f"sqlite:///{tmp_path / 'c.db'}"
    upgrade(url)
    app = create_app(Settings(_env_file=None, db_url=url, data_dir=str(tmp_path)))  # type: ignore[call-arg]
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = int(so.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(cast(Any, app), host="127.0.0.1", port=port, log_level="warning")
    )
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.05)
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for width, tag in ((1440, "desktop"), (390, "phone")):
            ctx = b.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            pg = ctx.new_page()
            pg.goto(f"http://127.0.0.1:{port}/_components/9c", wait_until="networkidle")
            cards = pg.locator(".chart-card", has_text="Dashboard · 0.29")
            for i in range(cards.count()):
                cards.nth(i).screenshot(path=str(OUT / f"m29.3-chart-{i + 1}-{tag}.png"))
            ctx.close()
        b.close()
    server.should_exit = True
    th.join(5)
    print("previews in", OUT)
