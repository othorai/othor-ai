# SPDX-License-Identifier: Apache-2.0
"""Per-IP rate limits (M11a; OPEN-QUESTIONS "per-IP rate limits"): a fixed window counted in
the metadata DB, so on-premise needs no Redis. The IP is never stored: only an HMAC of it under
the instance's secret. Codes are also limited per email (othor.core.auth); this slows one
network probing many emails or company addresses.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from othor.core.models import Base, IdMixin


class RateLimit(IdMixin, Base):
    """Not a TenantMixin table: it's counted before a company is known (the root host)."""

    __tablename__ = "rate_limit"
    __table_args__ = (sa.UniqueConstraint("bucket", "key_hash", "window_start"),)

    bucket: Mapped[str] = mapped_column(sa.String(32))
    key_hash: Mapped[str] = mapped_column(sa.String(64))
    window_start: Mapped[dt.datetime] = mapped_column()
    count: Mapped[int] = mapped_column(default=0)


@dataclass(frozen=True)
class Limit:
    bucket: str
    max: int
    window: dt.timedelta


WINDOW = dt.timedelta(minutes=15)
LOOKUP = Limit("lookup", 30, WINDOW)  # company addresses (1a) and address checks (2a)
CODES = Limit("codes", 20, WINDOW)  # sending and checking sign-in and signup codes
LIMITED_TEXT = "Too many attempts from this network. Try again in 15 minutes."


def key_hash(secret: bytes, key: str) -> str:
    return hmac.new(secret, key.encode(), hashlib.sha256).hexdigest()


def window_start(now: dt.datetime, window: dt.timedelta) -> dt.datetime:
    epoch = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)
    n = (now - epoch) // window
    return epoch + n * window


def hit(
    sf: sessionmaker[Session], limit: Limit, key: str, *, secret: bytes, now: dt.datetime
) -> bool:
    """Count one attempt; True while within the limit."""
    kh = key_hash(secret, key)
    start = window_start(now, limit.window)
    where = (
        RateLimit.bucket == limit.bucket,
        RateLimit.key_hash == kh,
        RateLimit.window_start == start,
    )
    for _ in range(2):
        with sf() as s:
            res: Any = s.execute(
                sa.update(RateLimit).where(*where).values(count=RateLimit.count + 1)
            )
            n = res.rowcount
            if n == 0:
                s.add(
                    RateLimit(
                        id=uuid.uuid4(),
                        bucket=limit.bucket,
                        key_hash=kh,
                        window_start=start,
                        count=1,
                    )
                )
            try:
                s.commit()
            except sa.exc.IntegrityError:  # another request made the row first
                s.rollback()
                continue
            count = s.scalar(sa.select(RateLimit.count).where(*where)) or 0
            return count <= limit.max
    return True


def prune(sf: sessionmaker[Session], now: dt.datetime) -> int:
    """Windows older than a day (the worker tick)."""
    with sf() as s:
        res: Any = s.execute(
            sa.delete(RateLimit).where(RateLimit.window_start < now - dt.timedelta(days=1))
        )
        n = res.rowcount
        s.commit()
        return int(n or 0)
