# SPDX-License-Identifier: Apache-2.0
"""The worker, watched from outside it (M11b §6, after #16).

Alerts the worker sends can't report the worker itself stopping. So its scheduler tick writes a
heartbeat row every minute: the app shows "Nightly runs are paused" when it goes quiet, and
`/healthz/worker` tells monitors. Optional check-in pings (Healthchecks.io, or a customer's own
on-premise) carry no data: an empty GET to a URL from the settings.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Literal

import httpx
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from othor.core.config import Settings
from othor.core.http import outbound_client
from othor.jobs.models import WorkerHeartbeat

log = logging.getLogger("othor.worker")

STALE = dt.timedelta(minutes=10)
PING_TIMEOUT_S = 10.0
_ROW = 1


def beat(sf: sessionmaker[Session], now: dt.datetime) -> None:
    with sf() as s:
        row = s.scalars(sa.select(WorkerHeartbeat).where(WorkerHeartbeat.id == _ROW)).first()
        if row is None:
            s.add(WorkerHeartbeat(id=_ROW, at=now))
        else:
            row.at = now
        try:
            s.commit()
        except sa.exc.IntegrityError:  # a second worker wrote the first row at the same moment
            s.rollback()


def last_beat(sf: sessionmaker[Session]) -> dt.datetime | None:
    with sf() as s:
        return s.scalar(sa.select(WorkerHeartbeat.at).where(WorkerHeartbeat.id == _ROW))


def state(
    sf: sessionmaker[Session], now: dt.datetime
) -> tuple[Literal["ok", "stale", "never"], dt.datetime | None]:
    at = last_beat(sf)
    if at is None:
        return "never", None
    return ("stale" if now - at > STALE else "ok"), at


def paused_since(sf: sessionmaker[Session], now: dt.datetime) -> dt.datetime | None:
    """The last beat, when it's more than 10 minutes old. Never beaten: None (a fresh instance,
    or no worker in tests); `/healthz/worker` reports that case as "never"."""
    status, at = state(sf, now)
    return at if status == "stale" else None


def ping(url: str, *, transport: httpx.BaseTransport | None = None) -> bool:
    """An empty GET to a check-in URL; only its own host is let through. Never raises: a monitor
    being down must never fail a run or the worker."""
    if not url:
        return False
    try:
        target = httpx.URL(url)
        if target.scheme != "https" or not target.host:
            log.warning("check-in URL ignored: https only")
            return False
        with outbound_client(
            transport, also=frozenset({target.host}), timeout=PING_TIMEOUT_S
        ) as client:
            return client.get(target).is_success
    except Exception as e:  # logged by type only (the URL is a secret)
        log.warning("check-in ping failed", extra={"error": type(e).__name__})
        return False


def tick_ping(settings: Settings, *, transport: httpx.BaseTransport | None = None) -> bool:
    return ping(settings.heartbeat_url.get_secret_value(), transport=transport)


def after_run(
    settings: Settings,
    kind: str,
    status: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    """The nightly check-in: pinged when a nightly run is sealed (done, or partial with a source
    marked stale), so a night with no sealed run alerts from outside by 05:00 IST."""
    if kind != "nightly" or status not in ("done", "partial"):
        return False
    return ping(settings.nightly_ping_url.get_secret_value(), transport=transport)
