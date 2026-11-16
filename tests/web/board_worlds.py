# SPDX-License-Identifier: Apache-2.0
"""World builders for the board states (M12b, tests/fixtures/board_states.py).

A state with a `prep` gets a fresh copy of its world (`auth`, `setup` or `night`), and the named
builder here moves that world into the board's state through the app's own routes and services.
It returns extra path values (`{name}` in the state's path). The same builders serve the
render test (tests/web/test_board_states.py) and the capture (tests/ui/test_capture_states.py).

Dev and test only: nothing in othor/ imports this.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from othor.core.models import Company
from othor.core.tenancy import system_scope
from tests.web.conftest import BASE, Env, signup


@dataclass
class World:
    env: Env
    host: str
    cid: uuid.UUID
    svc: Any
    tmp: Path
    rid: uuid.UUID | None = None
    sample: Any = None
    values: dict[str, str] = field(default_factory=dict)

    def csrf(self, page: str = "/login") -> str:
        return self.env.csrf(self.host + page)

    def post(self, path: str, data: dict[str, str] | None = None, page: str = "/login") -> Any:
        tok = self.csrf(page)
        return self.env.client.post(
            self.host + path, data={"csrf": tok, **(data or {})}, follow_redirects=False
        )


Prep = Callable[[World], dict[str, str] | None]
PREPS: dict[str, Prep] = {}


def prep(fn: Prep) -> Prep:
    # PREPS is keyed by name: a second builder with the same name would silently replace the first
    if fn.__name__ in PREPS:
        raise ValueError(f"two board-state builders are named {fn.__name__}")
    PREPS[fn.__name__] = fn
    return fn


def apply(state: Any, w: World) -> dict[str, str]:
    """Run the state's prep (if any); return every path value of the world. A prep may move
    `w.host` (a signup is on the root host)."""
    if state.prep:
        w.values.update(PREPS[state.prep](w) or {})
    return w.values


def cookies(env: Env, host: str) -> str:
    """Every cookie the browser would send to `host`, most specific domain last, as `a=1; b=2`."""
    domain = host.split("//")[-1].split("/")[0].split(":")[0]
    sent: dict[str, tuple[int, str]] = {}
    for c in env.client.cookies.jar:
        d = c.domain.lstrip(".")
        if (domain == d or domain.endswith("." + d) or d.endswith(".local")) and (
            c.name not in sent or len(d) >= sent[c.name][0]
        ):
            sent[c.name] = (len(d), str(c.value))
    return "; ".join(f"{n}={v}" for n, (_, v) in sent.items())


def company_id(env: Env, slug: str = "indigothread") -> uuid.UUID:
    sf = env.app.state.services.sf  # type: ignore[attr-defined]
    with sf() as s, system_scope("test"):
        return s.scalars(sa.select(Company.id).where(Company.slug == slug)).one()


# --- base worlds --------------------------------------------------------------------------------


@pytest.fixture
def auth_world(env: Env, tmp_path: Path) -> World:
    """The company exists; its owner has signed out."""
    signup(env)
    h = env.host("indigothread")
    w = World(env, h, company_id(env), env.app.state.services, tmp_path)  # type: ignore[attr-defined]
    w.post("/logout", page="/settings")
    return w


@pytest.fixture
def setup_world(env: Env, sample, need_sources, tmp_path: Path) -> World:
    """Signed up, onboarding at its first step, the sample sources reachable."""
    signup(env)
    h = env.host("indigothread")
    return World(
        env,
        h,
        company_id(env),
        env.app.state.services,
        tmp_path,
        sample=sample,  # type: ignore[attr-defined]
    )


@pytest.fixture
def night_world(night, sample, tmp_path: Path) -> World:
    from tests.web.test_board_states import night_values

    env, h, cid, rid, svc = night
    w = World(env, h, cid, svc, tmp_path, rid=rid, sample=sample)
    w.values.update(night_values(svc, cid, rid))
    return w


WORLD_FIXTURE = {"auth": "auth_world", "setup": "setup_world", "night": "night_world"}


# --- auth ---------------------------------------------------------------------------------------


@prep
def code_sent(w: World) -> None:
    """1c: the owner asked for a code; the code screen is next."""
    w.post("/login", {"email": "uk@indigothread.example"})


@prep
def at_signup_verify(w: World) -> None:
    """1f: a new company's signup, at the verify step (on the root host)."""
    w.env.client.cookies.clear()
    root = w.host = f"http://{BASE}"
    tok = w.env.csrf(f"{root}/signup")
    w.env.client.post(
        f"{root}/signup",
        data={
            "csrf": tok,
            "name": "Meera Iyer",
            "email": "meera@tanvi-foods.example",
            "company": "Tanvi Foods",
        },
        follow_redirects=False,
    )


@prep
def at_signup_address(w: World) -> None:
    """2a: a new company's signup, code verified, at the address step."""
    at_signup_verify(w)
    root = w.host
    tok = w.env.csrf(f"{root}/signup")
    code = w.env.last_code("meera@tanvi-foods.example")
    w.env.client.post(
        f"{root}/signup/verify", data={"csrf": tok, "code": code}, follow_redirects=False
    )


# The other builders register themselves on import (one module per world, M12b).
from tests.web import board_worlds_night, board_worlds_setup  # noqa: E402, F401
