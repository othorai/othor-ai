# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
"""The README's GIF (owner, 20 Nov 2026): a full Morning Brief on Indigo Thread (the invented sample
company), scrolled top to bottom (owner: "show a full brief also instead of opening brief detail card"). Dev only, run by hand
(needs `make dev && make seed`; builds its own world in the test database, never the dev stack's):

    .venv/bin/python -m pytest tests/ui/preview_brief_tour.py -s -p no:cacheprovider -o addopts=""

Writes export/docs/images/brief-tour.gif (about 960 px wide, 12 fps, looping, under 5 MB) and a
copy in ~/Desktop/othor-preview/readme-gif/."""

from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from tests.web.conftest import BASE
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
GIF = ROOT / "export" / "docs" / "images" / "brief-tour.gif"
PREVIEW = Path.home() / "Desktop" / "othor-preview" / "readme-gif"


def _port() -> int:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return int(so.getsockname()[1])


def test_brief_tour(night) -> None:  # noqa: F811
    import sys

    subprocess.run([sys.executable, "scripts/build_css.py"], cwd=ROOT, check=True)
    env, h, cid, _rid, svc = night
    from othor.briefs.models import Brief
    from othor.core.models import Company
    from othor.core.tenancy import system_scope
    from othor.engine import orchestrator
    from othor.engine.context import EngineContext
    from tests.fixtures.sample_company.history import STEADY_RUN
    from tests.web.test_currency_floor import _set

    # the README's world in US dollars and a US time zone (its screenshots are), 1 Nov run again
    with svc.sf() as s, system_scope("preview"):
        s.scalars(sa.select(Company).where(Company.id == cid)).one().timezone = "America/New_York"
        s.commit()
    assert _set(env, h, "currency", "USD").status_code == 200
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        r = orchestrator.start(
            s, c, kind="rerun", run_date=STEADY_RUN, now=ctx.clock(), image_version="0.1.0"
        )
        s.commit()
        rid = r.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid) == "done"
    with svc.sf() as s, tenant_scope(cid):
        bid = s.scalars(sa.select(Brief.id).order_by(Brief.created_at)).first()

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
            "name": k.name,
            "value": k.value,
            "domain": k.domain or f"indigothread.{BASE}",
            "path": k.path or "/",
        }
        for k in env.client.cookies.jar
    ]
    from playwright.sync_api import sync_playwright

    tmp = Path(tempfile.mkdtemp(prefix="tour-"))
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
        # warm the page first (fonts, CSS, charts) so the recording starts on a drawn page
        warm = b.new_context(viewport={"width": 1280, "height": 800})
        warm.add_cookies(cookies)  # type: ignore[arg-type]
        wp = warm.new_page()
        wp.goto(f"{app}/brief/{bid}", wait_until="networkidle")
        warm.close()
        ctx2 = b.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(tmp),
            record_video_size={"width": 1280, "height": 800},
        )
        ctx2.add_cookies(cookies)  # type: ignore[arg-type]
        pg = ctx2.new_page()
        t0 = time.monotonic()
        pg.goto(f"{app}/brief/{bid}", wait_until="networkidle")
        # the 1 Nov night was run again for dollars: leave out its "Send the updated brief?" offer
        pg.evaluate(
            """() => document.querySelectorAll('form[action$="/keep"]').forEach(e => (e.closest('.flex') || e).remove())"""
        )
        pg.evaluate("document.fonts.ready")
        start = time.monotonic() - t0
        pg.wait_for_timeout(1800)  # the brief's top: its title and headline numbers
        # owner, 20 Nov 2026: the whole brief, top to bottom, instead of opening a finding. It moves in
        # steps (hold, then a short glide) so each part can be read and the GIF stays small.
        height = pg.evaluate("document.documentElement.scrollHeight - innerHeight")
        stops = 6
        y0 = 0.0
        for k in range(1, stops + 1):
            y1 = height * k / stops
            for i in range(1, 9):  # a 0.4 s glide, eased
                u = i / 8
                pg.evaluate(f"window.scrollTo(0, {y0 + (y1 - y0) * (u * u * (3 - 2 * u)):.0f})")
                pg.wait_for_timeout(50)
            y0 = y1
            pg.wait_for_timeout(1500)  # hold: read this part
        pg.wait_for_timeout(1200)  # the end: Ask today and the run line
        video = Path(pg.video.path())  # type: ignore[union-attr]
        ctx2.close()
        b.close()
    server.should_exit = True
    th.join(5)
    GIF.parent.mkdir(parents=True, exist_ok=True)
    pal = tmp / "palette.png"
    # a held step's near-identical frames are dropped (mpdecimate) and the GIF keeps their time
    vf = "fps=12,scale=960:-1:flags=lanczos,mpdecimate=hi=64*24:lo=64*8:frac=0.5"
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"  # a dev tool on the PATH
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.2f}",
            "-i",
            str(video),
            "-vf",
            f"{vf},palettegen=max_colors=128:stats_mode=diff",
            str(pal),
        ],
        check=True,
    )
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.2f}",
            "-i",
            str(video),
            "-i",
            str(pal),
            "-lavfi",
            f"{vf}[x];[x][1:v]paletteuse=dither=none:diff_mode=rectangle",
            "-fps_mode",
            "vfr",
            "-loop",
            "0",
            str(GIF),
        ],
        check=True,
    )
    PREVIEW.mkdir(parents=True, exist_ok=True)
    shutil.copy(GIF, PREVIEW / GIF.name)
    size = GIF.stat().st_size
    print("gif", GIF, f"{size / 1e6:.2f} MB", f"trimmed {start:.1f}s")
    assert size < 5_000_000
