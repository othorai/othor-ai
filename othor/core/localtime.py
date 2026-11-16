# SPDX-License-Identifier: Apache-2.0
"""A company's local time (company.timezone, IST by default): run dates, "started 11:52:04"."""

from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo


def local_now(company: Any, now: dt.datetime) -> dt.datetime:
    return now.astimezone(ZoneInfo(company.timezone or "Asia/Kolkata"))


def local_date(company: Any, now: dt.datetime) -> dt.date:
    return local_now(company, now).date()


def tz_label(company: Any, now: dt.datetime) -> str:
    """ "IST" for Asia/Kolkata; the zone's abbreviation otherwise."""
    return local_now(company, now).tzname() or (company.timezone or "")
