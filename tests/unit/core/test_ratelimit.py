# SPDX-License-Identifier: Apache-2.0
"""The per-IP fixed-window limiter (M11a)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import sqlalchemy as sa

from othor.core import ratelimit as rl
from othor.core.db import make_engine, make_session_factory
from othor.migrations.runner import upgrade

SECRET = b"test-secret"
T0 = dt.datetime(2026, 11, 6, 10, 2, tzinfo=dt.UTC)


def _sf(tmp_path: Path):  # type: ignore[no-untyped-def]
    url = f"sqlite:///{tmp_path / 'rl.db'}"
    upgrade(url)
    return make_session_factory(make_engine(url))


def test_window_and_limit(tmp_path: Path) -> None:
    sf = _sf(tmp_path)
    lim = rl.Limit("t", 3, dt.timedelta(minutes=15))
    assert [rl.hit(sf, lim, "1.2.3.4", secret=SECRET, now=T0) for _ in range(4)] == [
        True,
        True,
        True,
        False,
    ]
    assert rl.hit(sf, lim, "5.6.7.8", secret=SECRET, now=T0)  # another network
    later = T0 + dt.timedelta(minutes=15)
    assert rl.hit(sf, lim, "1.2.3.4", secret=SECRET, now=later)  # the next window


def test_ip_is_never_stored(tmp_path: Path) -> None:
    sf = _sf(tmp_path)
    rl.hit(sf, rl.CODES, "203.0.113.9", secret=SECRET, now=T0)
    with sf() as s:
        rows = s.execute(sa.text("SELECT * FROM rate_limit")).all()
    assert rows and "203.0.113.9" not in str(rows)
    assert rl.window_start(T0, rl.WINDOW) == dt.datetime(2026, 11, 6, 10, 0, tzinfo=dt.UTC)


def test_prune_drops_old_windows(tmp_path: Path) -> None:
    sf = _sf(tmp_path)
    rl.hit(sf, rl.CODES, "a", secret=SECRET, now=T0 - dt.timedelta(days=2))
    rl.hit(sf, rl.CODES, "a", secret=SECRET, now=T0)
    assert rl.prune(sf, T0) == 1
