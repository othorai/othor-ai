# SPDX-License-Identifier: Apache-2.0
"""Previews of onboarding's chats in 0.26 (Chat 1.1: the one chat's parts, their own routes) for
the owner. Dev only:

    .venv/bin/python -m pytest tests/ui/preview_v026_onboarding.py -s -p no:cacheprovider \\
        -o addopts=""

Each state is put on a fresh setup world by the board fixtures; desktop (1440) and phone (390)
into ~/Desktop/othor-preview/v0.26/onboarding/."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from tests.fixtures import board_states as bs
from tests.ui.test_capture_states import serve
from tests.web.board_worlds import apply, cookies, setup_world  # noqa: F401
from tests.web.conftest import env  # noqa: F401
from tests.web.test_chat_web import need_sources, sample  # noqa: F401

OUT = Path.home() / "Desktop" / "othor-preview" / "v0.26" / "onboarding"
SHOTS = {
    "2nb": ("1-fix-where-is-it", None),
    "2nc": ("2-fix-connect", None),
    "2nd": ("3-fix-edit-with-draft", None),
    "2n": ("4-conflict", "conflict"),
    "10c": ("5-sheets-tab-fix", "fix"),
}


def _conflict(w) -> str:
    from othor.setup.models import SetupDraft

    with w.svc.sf() as s, tenant_scope(w.cid):
        key = s.scalars(
            sa.select(SetupDraft.key).where(SetupDraft.confidence == "conflict")
        ).first()
    assert key, "the setup world has the GST conflict open"
    return f"/setup/metrics?panel=choose&key={key}"


@pytest.mark.parametrize(
    "sid", [k for k in SHOTS if k in __import__("os").environ.get("ONLY", " ".join(SHOTS)).split()]
)
def test_preview_onboarding(sid: str, request: pytest.FixtureRequest) -> None:
    import subprocess
    import sys

    from playwright.sync_api import sync_playwright

    root = Path(__file__).resolve().parents[2]
    subprocess.run([sys.executable, "scripts/build_css.py"], cwd=root, check=True)
    st = bs.STATES[sid]
    w = request.getfixturevalue("setup_world")
    values = apply(st, w)
    name, extra = SHOTS[sid]
    path = bs.fill(st.path, values)
    if extra == "conflict":
        path = _conflict(w)
    elif extra == "fix":
        path += ("&" if "?" in path else "?") + "fix=1"
    OUT.mkdir(parents=True, exist_ok=True)
    host = w.host.replace("http://", "").split("/")[0]
    jar = [
        {"name": k, "value": v, "domain": host.split(":")[0], "path": "/"}
        for k, v in (p.split("=", 1) for p in cookies(w.env, w.host).split("; ") if "=" in p)
    ]
    with serve(w.env.app) as port, sync_playwright() as pw:
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        for width, tag in ((1440, ""), (390, "-phone")):
            ctx = b.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2)
            ctx.add_cookies(jar)  # type: ignore[arg-type]
            pg = ctx.new_page()
            pg.goto(f"http://{host}:{port}{path}", wait_until="networkidle")
            pg.wait_for_timeout(500)
            pg.screenshot(path=str(OUT / f"{name}{tag}.png"))
            if extra == "conflict":  # the other choice made (from a reply): Changed, then Apply
                pg.goto(f"http://{host}:{port}{path}&choice=upload", wait_until="networkidle")
                pg.wait_for_timeout(500)
                pg.screenshot(path=str(OUT / f"{name}-choice-made{tag}.png"))
            ctx.close()
        b.close()
