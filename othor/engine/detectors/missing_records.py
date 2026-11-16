# SPDX-License-Identifier: Apache-2.0
"""Missing records (docs/04 §3, detector 8): still marked active, gone silent. Needs an entity
breakdown (Ontology `breakdown.entity`: key, status and active values, identity column).

Reads the entity activity probe: per entity, the days it had events (invoices) and their value,
plus its status and identity (GSTIN). Two tests:

- **One entity, unusually quiet** (P4): an active entity with ≥ 6 events whose current gap is
  above the 95th percentile of its own intervals and over 2 × their median. Intervals are modelled
  log-normal: p = P(gap ≥ g | its own intervals). The cause can't be read from the data, so it is
  Not determinable, with its possible reasons and the checks that rule some out (a new entity
  with the same identity → "billing moved to another entity"). Money: its average daily value
  over the last 12 months × the gap.
- **Active, silent for 90+ days** (P5): the active entities with no event in 90 days or more. A
  measured count: Measured. Money: their value over the last 12 months.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal
from typing import Any

import numpy as np
from scipy import stats

from othor.engine.detectors.base import Context, Coverage, Result, Spec, dec, pval, register

NAME = "missing_records"
VERSION = "1"
QUIET_DAYS = 90
STRONG_Z = 4.0
REASONS = (
    ("competitor", "moved to a competitor", None),
    ("on_hold", "on hold", None),
    ("billing_moved", "billing moved to another entity", "identity"),
)


def run(ctx: Context) -> tuple[list[Result], Coverage]:
    probes = ctx.probes.get("entities") or {}
    if not probes:
        return [], Coverage(NAME, "could_not_run", 0, 0, "entities", needs="entity")
    cfg = ctx.config(NAME)
    out: list[Result] = []
    checked = 0
    notes: list[str] = []
    for bkey, probe in sorted(probes.items()):
        events: dict[str, list[tuple[dt.date, float]]] = defaultdict(list)
        for name, day, value in probe.get("events", []):
            events[str(name)].append((dt.date.fromisoformat(day), float(value)))
        status = {str(r[0]): r for r in probe.get("status", [])}
        active = set(probe.get("active_values") or [])
        actives = sorted(n for n, r in status.items() if not active or r[1] in active)
        as_of = ctx.run_date - dt.timedelta(days=1)
        pauses = paused(ctx, as_of)
        quiet: list[tuple[str, dt.date, float]] = []
        for name in actives:
            ev = sorted(events.get(name, []))
            if not ev:
                continue
            pause = pauses.get(name.lower())
            if pause and pause["start"] <= as_of <= pause["end"]:
                # a confirmed pause: not tested while it lasts, and not counted (decided 5 Nov)
                from othor.core import fmt

                until = fmt.day_month(pause["end"])
                notes.append(f"{name} · paused until {until} · not tested")
                out.append(_paused(ctx, bkey, probe, name, ev[-1][0], pause))
                continue
            checked += 1
            last = ev[-1][0]
            if pause and pause["end"] < as_of and last < pause["end"]:
                last = pause["end"]  # judged against its own rhythm from the end of the pause
                ev = [*ev, (last, 0.0)]
            gap = (as_of - last).days
            year = sum(v for d, v in ev if (as_of - d).days < 365)
            if gap >= QUIET_DAYS:
                quiet.append((name, last, year))
                continue
            r = _single(ctx, bkey, probe, name, ev, gap, year, status, cfg, as_of)
            if r is not None:
                out.append(r)
        out.append(_silent(ctx, bkey, probe, quiet, events, as_of))
    return out, Coverage(NAME, "ran", checked, checked, "entities", notes=tuple(notes))


def paused(ctx: Context, as_of: dt.date) -> dict[str, dict[str, Any]]:
    """Confirmed known patterns that pause one entity (Skarfab, 9 Oct – 1 Dec), by name."""
    from othor.engine.detect import windows

    out: dict[str, dict[str, Any]] = {}
    for p in ctx.spec.get("suppressions", []):
        if not p.get("entity") or not p.get("confirmed", True):
            continue
        for a, b in windows(p, range(as_of.year - 1, as_of.year + 1)):
            for name in (str(p["entity"]).lower(), str(p["entity"]).replace("_", " ").lower()):
                out[name] = {
                    "start": a,
                    "end": b,
                    "name": p.get("name", ""),
                    "answered_on": p.get("answered_on"),
                }
    return out


def _paused(
    ctx: Context, bkey: str, probe: dict[str, Any], name: str, last: dt.date, pause: dict[str, Any]
) -> Result:
    """An entity under a pause: not tested. Its fingerprint is the one a finding on it would have,
    so a finding open before the answer resolves as "Explained by your answer"."""
    r = _base(ctx, bkey, probe, [name])
    r.series = [f"{probe.get('metric', '')}|day|{bkey}|{name}"]
    r.breakdown_value = name
    r.eligible = False
    from othor.core import fmt

    answered = pause.get("answered_on")
    when = f" (answered {fmt.day_month(dt.date.fromisoformat(str(answered)))})" if answered else ""
    span = f"{fmt.day_month(pause['start'])} – {fmt.day_month(pause['end'])}"
    r.reason = f"explained by your answer · paused {span}{when}"
    r.slots = {"name": name, "last": last.isoformat(), "paused_until": pause["end"].isoformat()}
    return r


def _base(ctx: Context, bkey: str, probe: dict[str, Any], entities: list[str]) -> Result:
    metric = probe.get("metric", "")
    return Result(
        detector=NAME,
        version=VERSION,
        series=[f"{metric}|day|{bkey}|"],
        metric=metric,
        breakdown=bkey,
        breakdown_value=None,
        window=(ctx.run_date, ctx.run_date),
        statistic=None,
        p_value=None,
        effect=None,
        strong=False,
        direction="down",
        money=None,
        money_basis="",
        entities=entities,
        family=f"entity:{bkey}",
    )


def _single(
    ctx: Context,
    bkey: str,
    probe: dict[str, Any],
    name: str,
    ev: list[tuple[dt.date, float]],
    gap: int,
    year: float,
    status: dict[str, Any],
    cfg: dict[str, Any],
    as_of: dt.date,
) -> Result | None:
    min_events = int(cfg.get("min_events", 6))
    days = [d for d, _ in ev]
    gaps = np.diff([d.toordinal() for d in days]).astype(float)
    gaps = gaps[gaps > 0]
    if len(days) < min_events or len(gaps) < min_events - 1:
        return None
    med = float(np.median(gaps))
    q = float(np.quantile(gaps, float(cfg.get("quantile", 0.95))))
    # its own rhythm (docs/04): a gap is a finding only beyond its 95th-percentile interval
    # and at least twice its median interval; then its log-normal tail is the p-value
    over = gap > q and gap >= float(cfg.get("gap_over_median", 2)) * med
    lg = np.log(gaps)
    mu, sd = float(np.mean(lg)), float(np.std(lg, ddof=1)) if len(lg) > 1 else 0.0
    z = (np.log(max(gap, 1)) - mu) / sd if sd > 0 else 0.0
    r = _base(ctx, bkey, probe, [name])
    r.series = [f"{probe.get('metric', '')}|day|{bkey}|{name}"]
    r.breakdown_value = name
    r.window = (days[-1], as_of)
    r.statistic, r.effect = dec(z), dec(gap / med if med else None)
    r.p_value = pval(float(stats.norm.sf(z)) if over else 1.0)
    r.strong = z >= STRONG_Z
    r.state_hint = "not_determinable"
    daily = year / 365.0
    r.money = dec(daily * gap, places=Decimal("0.01"))
    r.money_basis = f"{name}'s average daily value over 12 months × {gap} days without an invoice"
    r.slots = {
        "name": name,
        "last": days[-1].isoformat(),
        "gap_days": gap,
        "median_days": dec(med),
        "min_days": int(gaps.min()),
        "max_days": int(gaps.max()),
        "events": len(days),
        "years": dec((days[-1] - days[0]).days / 365.25),
    }
    r.reasons = _reasons(ctx, probe, name, days[-1], status)
    r.params = {"model": "log-normal intervals", "quantile": cfg.get("quantile", "0.95")}
    if not over:
        r.reason = "within its own rhythm"
    return r


def _reasons(
    ctx: Context, probe: dict[str, Any], name: str, last: dt.date, status: dict[str, Any]
) -> list[dict[str, Any]]:
    ident = status.get(name, [None, None, None, None])[2]
    out = []
    for key, text, needs in REASONS:
        item: dict[str, Any] = {"key": key, "text": text, "ruled_out": False, "check": None}
        if needs == "identity":
            if ident:
                after = sorted(
                    n
                    for n, row in status.items()
                    if n != name
                    and row[2] == ident
                    and row[3]
                    and dt.date.fromisoformat(row[3]) > last
                )
                item["check"] = {"kind": "same_identity", "found": after}
                item["ruled_out"] = not after
            else:
                item["check"] = {"kind": "same_identity", "found": None, "needs": "identity column"}
        out.append(item)
    return out


def _silent(
    ctx: Context,
    bkey: str,
    probe: dict[str, Any],
    quiet: list[tuple[str, dt.date, float]],
    events: dict[str, list[tuple[dt.date, float]]],
    as_of: dt.date,
) -> Result:
    r = _base(ctx, bkey, probe, sorted(n for n, _, _ in quiet))
    r.state_hint = "measured"
    r.window = (as_of - dt.timedelta(days=QUIET_DAYS), as_of)
    if not quiet:
        r.p_value, r.statistic = pval(1.0), dec(0)
        r.reason = "no active entity is silent 90+ days"
        return r
    total = sum(v for _, _, v in quiet)
    r.p_value, r.statistic, r.effect = pval(0.0), dec(len(quiet)), dec(len(quiet))
    r.strong = True
    r.money = dec(total, places=Decimal("0.01"))
    r.money_basis = f"their value over the 12 months to {as_of.isoformat()}"
    rows = sorted(quiet, key=lambda x: (-x[2], x[0]))
    r.evidence = [
        {
            "name": n,
            "last": last.isoformat(),
            "silent_days": (as_of - last).days,
            "last_12m": dec(v, places=Decimal("0.01")),
            "gap_before": _gaps(events.get(n, []), last),
        }
        for n, last, v in rows[:50]
    ]
    lengthened = [e["gap_before"] for e in r.evidence if e["gap_before"]]
    r.slots = {
        "count": len(quiet),
        "quiet_days": QUIET_DAYS,
        "total_12m": dec(total, places=Decimal("0.01")),
        "gap_lengthened_weeks": dec(
            float(np.median([g["to"] - g["from"] for g in lengthened])) / 7 if lengthened else None
        ),
    }
    r.params = {"quiet_days": QUIET_DAYS}
    return r


def _gaps(ev: list[tuple[dt.date, float]], last: dt.date) -> dict[str, int] | None:
    """Reorder gap before going quiet: median interval in the year before vs the last 3."""
    days = sorted({d for d, _ in ev if d <= last})
    if len(days) < 8:
        return None
    iv = np.diff([d.toordinal() for d in days])
    return {"from": int(np.median(iv[:-3][-12:])), "to": int(np.median(iv[-3:]))}


register(Spec(NAME, VERSION, "Missing records", run, needs=("entity",)))
