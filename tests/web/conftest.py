# SPDX-License-Identifier: Apache-2.0
"""A whole app on SQLite with a memory mail relay and a movable clock; hosts via full URLs."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from othor.core.config import Settings
from othor.core.models import Event
from othor.core.tenancy import system_scope
from othor.mail.relay import MemoryRelay
from othor.migrations.runner import upgrade
from othor.web.app import create_app

BASE = "othor.localhost"
ROOT = f"http://{BASE}"


class Clock:
    def __init__(self) -> None:
        self.t = dt.datetime(2026, 11, 4, 9, 0, tzinfo=dt.UTC)

    def __call__(self) -> dt.datetime:
        return self.t

    def tick(self, **kw: float) -> None:
        self.t += dt.timedelta(**kw)


@dataclass
class Env:
    client: TestClient
    relay: MemoryRelay
    clock: Clock
    app: object

    def host(self, slug: str) -> str:
        return f"http://{slug}.{BASE}"

    def csrf(self, url: str) -> str:
        self.client.get(url)
        domain = url.split("//")[1].split("/")[0]
        # The test client (unlike a browser) also sends the root host's cookies to subdomains,
        # so use the most specific othor_csrf cookie the client would send to this host.
        sent = [
            c
            for c in self.client.cookies.jar
            if c.name == "othor_csrf"
            and (domain == c.domain.lstrip(".") or domain.endswith("." + c.domain.lstrip(".")))
        ]
        if not sent:
            raise AssertionError(f"no csrf cookie for {domain}")
        return str(max(sent, key=lambda c: len(c.domain)).value)

    def last_code(self, to: str | None = None) -> str:
        msgs = [m for m in self.relay.outbox if to is None or m.to == to]
        m = re.search(r"(\d{3}) (\d{3})", msgs[-1].subject)
        assert m
        return m.group(1) + m.group(2)

    def post(self, url: str, data: dict[str, str] | None = None, **kw: object) -> object:
        data = dict(data or {})
        data.setdefault(
            "csrf", self.csrf(url.rsplit("/", 1)[0] + "/login" if "/settings" not in url else url)
        )
        return self.client.post(url, data=data, follow_redirects=False, **kw)  # type: ignore[arg-type]

    def events(self, action: str | None = None) -> list[Event]:
        sf = self.app.state.services.sf  # type: ignore[attr-defined]
        with sf() as s, system_scope("test"):
            q = sa.select(Event).order_by(Event.at)
            if action:
                q = q.where(Event.action == action)
            return list(s.scalars(q))


@contextmanager
def make_env(tmp_path: Path, at: dt.datetime | None = None, **overrides: Any) -> Iterator[Env]:
    """A whole app on the SQLite file and data folder in `tmp_path` (migrated if new).
    `overrides`: more settings (M20.1: `tenancy`, `plugins`); plug-ins load before migrating,
    so their branch is applied too."""
    from othor.core import plugins

    url = f"sqlite:///{tmp_path / 'web.db'}"
    if overrides.get("plugins"):
        plugins.reset()
        plugins.load(Settings(_env_file=None, env="test", plugins=overrides["plugins"]))  # type: ignore[call-arg]
    upgrade(url)
    relay = MemoryRelay()
    clock = Clock()
    if at is not None:
        clock.t = at
    base: dict[str, Any] = {
        "_env_file": None,
        "db_url": url,
        "data_dir": str(tmp_path),
        "env": "test",
        "base_domain": BASE,
        "mail_relay": "memory",
        "mail_from_domain": "othor.localhost",
    }
    settings = Settings(**{**base, **overrides})
    app = create_app(settings, relay=relay, clock=clock)
    with TestClient(app, base_url=ROOT) as client:
        yield Env(client, relay, clock, app)


def snapshot(src: Path, dst: Path) -> None:
    """Copy a finished env's folder (database and data files) for another test: the WAL is
    folded into the database first, so the copy is complete."""
    import shutil
    import sqlite3

    con = sqlite3.connect(src / "web.db")
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    shutil.copytree(src, dst, dirs_exist_ok=True)


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    with make_env(tmp_path) as e:
        yield e


def signup(
    env: Env,
    *,
    name: str = "Uday Kamath",
    email: str = "uk@indigothread.example",
    company: str = "Indigo Thread Co.",
    slug: str = "indigothread",
) -> object:
    """1e → 1f → 2a → handoff. Returns the final response (on the company host)."""
    c = env.client
    tok = env.csrf(f"{ROOT}/signup")
    r = c.post(
        f"{ROOT}/signup",
        data={"csrf": tok, "name": name, "email": email, "company": company},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/signup/verify"
    code = env.last_code(email)
    r = c.post(f"{ROOT}/signup/verify", data={"csrf": tok, "code": code}, follow_redirects=False)
    assert r.headers["location"] == "/signup/address", r.text
    r = c.post(
        f"{ROOT}/signup/address",
        data={
            "csrf": tok,
            "slug": slug,
            "website": "https://indigothread.example",
            "fy": "4",
            "currency": "INR",
            "tz": "Asia/Kolkata",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    handoff = r.headers["location"]
    assert handoff.startswith(f"http://{slug}.{BASE}/auth/handoff?t=")
    return c.get(handoff, follow_redirects=False)
