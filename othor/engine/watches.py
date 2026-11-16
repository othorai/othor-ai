# SPDX-License-Identifier: Apache-2.0
"""Watches (docs/04, 5d, 5e): an indicator, a threshold, an owner, checked every night.

The indicator is computed from the run's outputs, never typed in:
- `value`: the latest complete value of a series, or its mean over the last `window_days` days;
- `vs_average`: the latest complete period against the mean of the `periods` before it (as a
  change, +19% = 0.19);
- `finding_slot`: a number a detector computed tonight (the count of customers silent 90+ days).

Tonight's status is `within` or `breached`, with the date it was first breached. Evaluation is
pure (watches are snapshotted into the run's inputs); creating one writes a row and an event.
M6b adds the screens (5d Watch this, with its 90-day replay; 5e the Watching tab).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import numpy as np

from othor.engine.detectors.base import Result, SeriesIndex, dec
from othor.engine.detectors.common import percentile


def evaluate(
    watches: list[dict[str, Any]],
    series: SeriesIndex,
    results: list[Result],
    previous: dict[str, dict[str, Any]],
    run_date: dt.date,
) -> list[dict[str, Any]]:
    out = []
    for w in sorted(watches, key=lambda w: w["id"]):
        value = indicator(w["indicator"], series, results)
        thr = Decimal(w["threshold"])
        if value is None:
            status = "no_value"
        else:
            above = Decimal(value) > thr
            status = (
                "breached"
                if (above if w["direction"] == "above" else Decimal(value) < thr)
                else "within"
            )
        before = previous.get(w["id"], {})
        since = before.get("breached_since") if before.get("status") == "breached" else None
        if status == "breached" and since is None:
            since = (run_date - dt.timedelta(days=1)).isoformat()
        out.append(
            {
                "id": w["id"],
                "label": w["label"],
                "owner_name": w["owner_name"],
                "threshold": w["threshold"],
                "direction": w["direction"],
                "value": value,
                "status": status,
                "breached_since": since if status == "breached" else None,
                "since": w["since"],
            }
        )
    return out


def indicator(
    ind: dict[str, Any],
    series: SeriesIndex,
    results: list[Result],
    upto: dt.date | None = None,
) -> str | None:
    """The indicator's value tonight, or as it would have read on `upto` (the 90-day replay
    uses the stored series only; a detector's slot has no replay)."""
    kind = ind["kind"]
    if kind == "finding_slot":
        if upto is not None:
            return None
        for r in results:
            if r.detector == ind["detector"] and r.eligible and ind["slot"] in r.slots:
                v = r.slots[ind["slot"]]
                return None if v is None else str(Decimal(str(v)))
        return None
    s = series.get(
        ind["metric"], ind.get("grain", "day"), ind.get("breakdown", ""), ind.get("breakdown_value")
    )
    if s is None:
        return None
    pairs = [
        (p, v)
        for p, v in zip(s.periods, s.values, strict=True)
        if np.isfinite(v) and (upto is None or p <= upto)
    ]
    if not pairs:
        return None
    if kind in ("value", "spread"):
        n = int(ind.get("window_days") or 1)
        last = pairs[-1][0]
        vals = [v for p, v in pairs if (last - p).days < n]
        if kind == "spread":
            if len(vals) < 3:
                return None
            return dec(percentile(vals, 0.9) - percentile(vals, 0.1))
        return dec(float(np.mean(vals)))
    if kind == "vs_average":
        n = int(ind.get("periods") or 6)
        if len(pairs) < n + 1:
            return None
        base = float(np.mean([v for _, v in pairs[-n - 1 : -1]]))
        return dec(pairs[-1][1] / base - 1) if base else None
    raise ValueError(f"unknown indicator {kind!r}")


REPLAY_DAYS = 90


def replay(
    ind: dict[str, Any],
    series: SeriesIndex,
    threshold: str,
    direction: str,
    end: dt.date,
    days: int = REPLAY_DAYS,
) -> dict[str, Any]:
    """The indicator over the last `days` days as it would have read each day (5d), from the
    stored series only (no source is queried), and the first day it would have breached."""
    thr = Decimal(threshold)
    points: list[tuple[str, str]] = []
    first = None
    for i in range(days - 1, -1, -1):
        day = end - dt.timedelta(days=i)
        v = indicator(ind, series, [], upto=day)
        if v is None:
            continue
        points.append((day.isoformat(), v))
        over = Decimal(v) > thr if direction == "above" else Decimal(v) < thr
        if over and first is None:
            first = day.isoformat()
    return {"points": points, "first_breach": first, "threshold": str(thr)}


def draft_round(v: Decimal, unit: str) -> Decimal:
    """A drafted threshold at a sensible precision for its unit: days to 0.1, ₹ to two
    significant figures, counts to whole numbers, shares to 0.01."""
    if unit == "days":
        return v.quantize(Decimal("0.1"))
    if unit == "inr":
        if not v:
            return Decimal(0)
        exp_ = v.adjusted() - 1
        q = Decimal(1).scaleb(exp_)
        return v.quantize(q) if exp_ < 0 else (v / q).quantize(Decimal(1)) * q
    if unit == "count":
        return v.quantize(Decimal(1))
    return v.quantize(Decimal("0.01"))


def draft(f: dict[str, Any], series: SeriesIndex, spec: dict[str, Any]) -> dict[str, Any] | None:
    """Watch this (5d): an indicator, a threshold and its hint, drafted from a finding by rule.
    The owner can change any of it before starting."""
    sl = f.get("slots", {})
    name = spec.get("metrics", {}).get(f["metric"], {}).get("name") or f["metric"]
    det = f["detector"]
    if det == "rising_variance" and sl.get("started"):
        ind = {
            "kind": "spread",
            "metric": f["metric"],
            "grain": "day",
            "breakdown": f["breakdown"],
            "breakdown_value": f["breakdown_value"],
            "window_days": 7,
        }
        start = dt.date.fromisoformat(sl["started"])
        before = replay(ind, series, "0", "above", start - dt.timedelta(days=1), days=90)
        prior = max((Decimal(v) for _, v in before["points"]), default=None)
        now = indicator(ind, series, [])
        if prior is None or now is None:
            return None
        thr = draft_round((prior + Decimal(now)) / 2, "days")
        where = f", {f['breakdown_value']}" if f.get("breakdown_value") else ""
        return {
            "label": f"{name} spread{where} · p90 − p10 days, 7-day window",
            "indicator": ind,
            "threshold": str(thr),
            "direction": "above",
            "hint": {
                "kind": "halfway",
                "before": sl["started"],
                "value": str(draft_round(prior, "days")),
                "today": str(draft_round(Decimal(now), "days")),
            },
            "unit": "days",
        }
    if det == "missing_records" and f.get("state") == "measured":
        return {
            "label": f"Active customers silent {sl.get('quiet_days', 90)}+ days",
            "indicator": {"kind": "finding_slot", "detector": det, "slot": "count"},
            "threshold": str(sl.get("count", 0)),
            "direction": "above",
            "hint": {"kind": "today", "value": str(sl.get("count", 0))},
            "unit": "count",
        }
    if det == "changepoint" and sl.get("before"):
        return {
            "label": f"{name} · weekly",
            "indicator": {"kind": "value", "metric": f["metric"], "grain": "week"},
            "threshold": str(
                draft_round(
                    Decimal(sl["before"]),
                    spec.get("metrics", {}).get(f["metric"], {}).get("unit", "count"),
                )
            ),
            "direction": "above" if Decimal(sl["after"]) > Decimal(sl["before"]) else "below",
            "hint": {"kind": "level_before", "value": sl["before"]},
            "unit": spec.get("metrics", {}).get(f["metric"], {}).get("unit", "count"),
        }
    if det == "broken_relationship":
        a = (f.get("entities") or [f["metric"]])[0]
        an = spec.get("metrics", {}).get(a, {}).get("name") or a
        return {
            "label": f"{an} vs 6-month average",
            "indicator": {"kind": "vs_average", "metric": a, "grain": "month", "periods": 6},
            "threshold": "0.10",
            "direction": "above",
            "hint": {"kind": "ratio_change", "value": sl.get("ratio_change")},
            "unit": "percent",
        }
    if det == "cross_source" and "count" in sl:
        return {
            "label": (
                f"{sl.get('noun', 'records')} with no match after {sl.get('lag_days')} days"
            ).capitalize(),
            "indicator": {"kind": "finding_slot", "detector": det, "slot": "count"},
            "threshold": "0",
            "direction": "above",
            "hint": {"kind": "today", "value": str(sl["count"])},
            "unit": "count",
        }
    return None


def create(
    s: Any,
    *,
    label: str,
    indicator: dict[str, Any],
    threshold: str,
    direction: str,
    owner_name: str,
    since: dt.date,
    actor: str,
    from_finding: str | None = None,
) -> Any:
    """Start watching (inside the company's tenant scope): the row and its event."""
    from othor.core.events import record_event
    from othor.engine.models import Watch

    w = Watch(
        label=label,
        indicator=indicator,
        threshold=str(Decimal(threshold)),
        direction=direction,
        owner_name=owner_name,
        created_from_finding=from_finding,
        since=since,
        status="within",
    )
    s.add(w)
    s.flush()
    record_event(
        s,
        actor=actor,
        action="watch.started",
        object_type="watch",
        object_id=w.id,
        after={
            "label": label,
            "threshold": w.threshold,
            "direction": direction,
            "owner": owner_name,
        },
    )
    return w


def snapshot(s: Any) -> list[dict[str, Any]]:
    """Active watches as run inputs (sorted, plain data)."""
    import sqlalchemy as sa

    from othor.engine.models import Watch

    rows = s.scalars(
        sa.select(Watch).where(Watch.stopped_at.is_(None)).order_by(Watch.created_at, Watch.id)
    ).all()
    return [
        {
            "id": str(w.id),
            "label": w.label,
            "indicator": w.indicator,
            "threshold": w.threshold,
            "direction": w.direction,
            "owner_name": w.owner_name,
            "since": w.since.isoformat(),
            "from_finding": w.created_from_finding,
        }
        for w in rows
    ]
