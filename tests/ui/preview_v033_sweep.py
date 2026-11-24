# SPDX-License-Identifier: Apache-2.0
"""0.33 Sweep previews (plan docs/plans/what-to-do.md § E–G; owner's designs of 22 Nov 2026).
Dev only, run by hand (needs `make dev && make seed`; the sample is read only):

    .venv/bin/python -m pytest tests/ui/preview_v033_sweep.py -s -p no:cacheprovider -o addopts=""

On the sample's 1 Nov night: the finding panel on each of its five tabs; Suppressed with two
snoozes, two mutes (one from a brief, one from What to do) and a proposed pattern; Watching; the
Watch this panel for a spread (days) and a step in money (₹). Desktop and phone, to
~/Desktop/othor-preview/v0.33/sweep/."""

from __future__ import annotations

import datetime as dt
import socket
import threading
import time
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from othor.engine import holds
from othor.engine.models import Finding, Proposal
from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import _fp, night  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / "v0.33" / "sweep"


def _holds(svc: Any, cid: Any, rid: Any) -> None:
    """Two snoozes and two mutes, from different places, and one proposed pattern."""
    with svc.sf() as s, tenant_scope(cid):
        rows = s.scalars(sa.select(Finding).where(Finding.run_id == rid)).all()
        by = {(r.detector, r.breakdown_value): r for r in rows}
        picks = [
            ("snooze", ("missing_records", "Ravira Exports"), "what-to-do"),
            ("snooze", ("changepoint", None), "sweep"),
            ("mute", ("concentration", None), "what-to-do"),
            ("mute", ("cross_source", None), "sweep"),
        ]
        today = dt.date(2026, 11, 1)
        for action, key, origin in picks:
            r = by.get(key)
            if r is None:
                continue
            f = {**r.body, "fingerprint": r.fingerprint}
            if action == "snooze":
                holds.snooze(s, f, actor="preview", today=today, origin=origin)
            else:
                holds.mute(s, f, actor="preview", origin=origin)
        s.add(
            Proposal(
                company_id=cid,
                workspace_id=cid,
                kind="suppression",
                status="open",
                fingerprint="suppression:preview",
                text=(
                    "Orders: the same dip around 20 Oct – 3 Nov in 2024, 2025. "
                    "Mark it as a known pattern?"
                ),
                evidence={},
            )
        )
        s.commit()


def test_preview(night: Any) -> None:  # noqa: F811
    env, _h, cid, rid, svc = night
    OUT.mkdir(parents=True, exist_ok=True)
    nhg = _fp(svc, cid, rid, "net_hiding_gross")
    spread = _fp(svc, cid, rid, "rising_variance", "Coimbatore")
    step = _fp(svc, cid, rid, "changepoint")
    _holds(svc, cid, rid)
    import uvicorn

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
    cookies = [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain or f"indigothread.{BASE}",
            "path": c.path or "/",
        }
        for c in env.client.cookies.jar
    ]
    base = f"http://indigothread.{BASE}:{port}"
    pages = [
        *(
            (
                f"finding-panel-{t}",
                f"/sweep/finding/{nhg}" + ("" if t == "finding" else f"?tab={t}"),
            )
            for t in ("finding", "test", "metric", "sql", "run")
        ),
        ("suppressed", "/sweep?tab=suppressed"),
        ("watching", "/sweep?tab=watching"),
        ("watch-panel-days", f"/sweep/watch/{spread}"),
        ("watch-panel-money", f"/sweep/watch/{step}"),
    ]
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        for width, tag in ((1440, "desktop"), (390, "phone")):
            ctx = b.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(cookies)  # type: ignore[arg-type]
            pg = ctx.new_page()
            errors: list[str] = []
            pg.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            for name, path in pages:
                pg.goto(base + path, wait_until="networkidle")
                full = not name.startswith(("finding-panel", "watch-panel"))
                pg.screenshot(path=str(OUT / f"{name}-{tag}.png"), full_page=full)
            assert not errors, errors
            ctx.close()
        b.close()
    server.should_exit = True
    th.join(5)
    print("previews in", OUT)
