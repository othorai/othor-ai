# SPDX-License-Identifier: Apache-2.0
"""Capture board states on demand (M12): builds each world on a fresh test app, serves it, and
runs scripts/compare_screen.py on every requested board. Skipped unless OTHOR_CAPTURE is set;
run it through scripts/capture_states.py. Writes docs/ui-review/<id>/ (and, with
OTHOR_CAPTURE_MILESTONE, docs/ui-review/built/<M>/)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn

from tests.fixtures import board_states as bs
from tests.web.board_worlds import (  # noqa: F401
    WORLD_FIXTURE,
    apply,
    auth_world,
    cookies,
    night_world,
    setup_world,
)
from tests.web.conftest import BASE, Env, env, signup  # noqa: F401
from tests.web.test_board_states import night_values
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

CAPTURE = os.environ.get("OTHOR_CAPTURE", "")
MILESTONE = os.environ.get("OTHOR_CAPTURE_MILESTONE", "")
HIRES = os.environ.get("OTHOR_CAPTURE_HIRES", "")  # a folder: also save <id>.png at 2x there
ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        not CAPTURE, reason="set OTHOR_CAPTURE=<ids|all> (scripts/capture_states.py)"
    ),
]


def wanted(*worlds: str, prepped: bool = False) -> list[bs.BoardState]:
    ids = set(bs.STATES) if CAPTURE == "all" else set(CAPTURE.split(","))
    return [
        s
        for i, s in sorted(bs.STATES.items())
        if i in ids and s.world in worlds and own_world(s) == prepped
    ]


def own_world(s: bs.BoardState) -> bool:
    """A state that needs a fresh world of its own (its prep changes the world)."""
    return s.world in WORLD_FIXTURE and (bool(s.prep) or s.world != "night")


@contextmanager
def serve(app: object) -> Iterator[int]:
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = int(sk.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))  # type: ignore[arg-type]
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    try:
        yield port
    finally:
        server.should_exit = True
        t.join(timeout=5)


def session_cookie(env: Env) -> str:  # noqa: F811
    vals = [c.value for c in env.client.cookies.jar if c.name == "othor_session"]
    return f"othor_session={vals[-1]}" if vals else ""


def compare(state: bs.BoardState, url: str, cookie: str) -> None:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "compare_screen.py"),
        "--id",
        state.id,
        "--url",
        url,
    ]
    if cookie:
        cmd += ["--cookie", cookie]
    for a in state.actions:
        cmd += ["--action", a]
    if state.width != 1440:
        cmd += ["--width", str(state.width)]
    if MILESTONE:
        cmd += ["--milestone", MILESTONE]
    if HIRES:
        cmd += ["--hires", str(Path(HIRES) / f"{state.id}.png")]
    subprocess.run(cmd, check=True, cwd=ROOT)


def test_capture_root_company_fresh_components(env: Env) -> None:  # noqa: F811
    states = wanted("root", "company", "fresh", "components")
    if not states:
        pytest.skip("no board of these worlds requested")
    signup(env)
    cookie = session_cookie(env)
    with serve(env.app) as port:
        for s in states:
            host = BASE if s.world == "root" else f"indigothread.{BASE}"
            signed_in = s.world in ("fresh", "components")
            compare(s, f"http://{host}:{port}{s.path}", cookie if signed_in else "")


def test_capture_night(request: pytest.FixtureRequest) -> None:
    states = wanted("night")
    if not states:
        pytest.skip("no night board requested")
    world, _h, cid, rid, svc = request.getfixturevalue("night")
    values = night_values(svc, cid, rid)
    with serve(world.app) as port:
        for s in states:
            compare(
                s,
                f"http://indigothread.{BASE}:{port}{bs.fill(s.path, values)}",
                session_cookie(world),
            )


def test_capture_mail() -> None:
    states = wanted("mail")
    if not states:
        pytest.skip("no email board requested")
    for s in states:
        compare(s, (ROOT / "tests/ui/mail/golden" / s.path).as_uri(), "")


OWN = sorted(i for i, s in bs.STATES.items() if own_world(s))


@pytest.mark.parametrize("sid", OWN)
def test_capture_own_world(sid: str, request: pytest.FixtureRequest) -> None:
    st = bs.STATES[sid]
    if st not in wanted(st.world, prepped=True):
        pytest.skip("not requested")
    w = request.getfixturevalue(WORLD_FIXTURE[st.world])
    values = apply(st, w)
    with serve(w.env.app) as port:
        host = w.host.replace("http://", "").split("/")[0]
        compare(st, f"http://{host}:{port}{bs.fill(st.path, values)}", cookies(w.env, w.host))
