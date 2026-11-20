# SPDX-License-Identifier: Apache-2.0
"""0.28.5 · the stored nights the brief's "Since last week" reads (`context.json` "history"):
the last 30 detecting nights before tonight from the `finding` table, one per date (a Run again
now replaces its night), each finding's status, money and tag, and for each fingerprint what a
brief's scope reads, its title, first-seen day and money when first reported. Collected when the
run starts and sealed with its inputs, so replay reads the file and never collects again."""

from __future__ import annotations

import datetime as dt
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

NIGHTS = 30


def _note(status: str, body: dict[str, Any]) -> tuple[str | None, str | None]:
    if status == "resolved":
        r = body.get("resolved") or {}
        return r.get("reason"), r.get("date")
    if status == "covered":
        c = body.get("covered") or {}
        return c.get("by"), c.get("date")
    if status == "merged":
        return None, (body.get("merged") or {}).get("date")
    if status == "snoozed":
        return body.get("snoozed_until"), None
    return None, None


def collect(s: Session, number: int, run_date: dt.date) -> dict[str, Any] | None:
    """The nights before `run_date` (and before run `number`), or None when there are none."""
    from othor.engine.models import DETECTING_KINDS, Finding, Run

    first = run_date - dt.timedelta(days=NIGHTS)
    runs = s.execute(
        sa.select(Run.id, Run.number, Run.run_date)
        .where(
            Run.status.in_(("done", "partial")),
            Run.kind.in_(DETECTING_KINDS),
            Run.number < number,
            Run.run_date >= first,
            Run.run_date < run_date,
        )
        .order_by(Run.run_date, Run.number)
    ).all()
    latest: dict[dt.date, Any] = {}
    for rid, num, day in runs:  # a later run of the same date replaces it
        latest[day] = (rid, num)
    if not latest:
        return None
    ids = {v[0]: d for d, v in latest.items()}
    nights: dict[dt.date, list[list[Any]]] = {d: [] for d in latest}
    meta: dict[str, dict[str, Any]] = {}
    firsts: dict[str, int] = {}
    for f in s.scalars(sa.select(Finding).where(Finding.run_id.in_(list(ids)))):
        body = f.body or {}
        note, date = _note(f.status, body)
        nights[ids[f.run_id]].append([f.fingerprint, f.status, f.money, f.change, note, date])
        meta[f.fingerprint] = {
            "detector": f.detector,
            "metric": f.metric,
            "breakdown": f.breakdown,
            "breakdown_value": f.breakdown_value,
            "entities": sorted(body.get("entities") or []),
            "state": f.state,
            "title": str((body.get("text") or {}).get("title") or ""),
            "first_seen": str((body.get("first_seen") or {}).get("date") or ""),
        }
        firsts[f.fingerprint] = f.first_seen_run
    # the money each was first reported with: its row in the run that first saw it
    by_run: dict[int, set[str]] = {}
    for fp, n in firsts.items():
        by_run.setdefault(n, set()).add(fp)
    if by_run:
        got = s.execute(
            sa.select(Finding.fingerprint, Finding.money, Run.number)
            .join(Run, Run.id == Finding.run_id)
            .where(Run.number.in_(list(by_run)), Finding.fingerprint.in_(list(firsts)))
        ).all()
        for fp, money, num in got:
            if fp in by_run.get(num, set()):
                meta[fp]["first_money"] = money
    return {
        "nights": [
            {"date": d.isoformat(), "rows": sorted(nights[d], key=lambda r: r[0])}
            for d in sorted(nights)
        ],
        "meta": {k: dict(sorted(meta[k].items())) for k in sorted(meta)},
    }
