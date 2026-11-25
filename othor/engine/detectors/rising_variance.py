# SPDX-License-Identifier: Apache-2.0
"""Rising variance (docs/04 §3, detector 4): the spread widens before the level moves.

For intensive measures (averages, durations, rates: not sums), by process (each plant), the
daily values since the spread started (the week of the last 8 that best splits narrow days from
wide ones, the spread since staying up; at least the last 4 weeks, spec `recent_weeks`) against
the 12 weeks before (`prior_weeks`):
F = var(recent) / var(prior), one-sided F-test, Bonferroni over the 8 candidate weeks. It fires
only while the level holds (its mean moved under 10%); a level that moved is a changepoint's.

Money: what flows through the process in the recent window, from the metric's `at_stake` ₹
metric for the same breakdown value (e.g. dispatch time → line revenue at that plant).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
from scipy import stats

from othor.engine.detectors import common
from othor.engine.detectors.base import (
    PAISA,
    Context,
    Coverage,
    Result,
    Series,
    Spec,
    dec,
    pval,
    register,
)

NAME = "rising_variance"
# 2: the start is the week that best splits narrow from wide (25 Nov 2026); version 1 (the first
# wide week) stays in the code, so a run made with it replays as it was (`Context.versions`)
VERSION = "2"
INTENSIVE_AGGS = ("avg", "stddev", "median")
INTENSIVE_UNITS = ("days", "percent", "ratio", "inr_per_metre")
STRONG_F = 4.0
CANDIDATES = 8  # weeks in which a widening may have started
LEVEL_HELD = 0.10


def intensive(ctx: Context, metric: str) -> bool:
    m = ctx.metric(metric)
    return m.get("agg") in INTENSIVE_AGGS or (
        m.get("type") == "derived" and m.get("unit") in INTENSIVE_UNITS
    )


def run(ctx: Context) -> tuple[list[Result], Coverage]:
    cfg = ctx.config(NAME)
    recent_w = int(cfg.get("recent_weeks", 4))
    prior_w = int(cfg.get("prior_weeks", 12))
    min_w = int(cfg.get("min_weeks", 16))
    results = []
    for s in common.candidates(ctx, "day"):
        if not intensive(ctx, s.metric) or ctx.metric(s.metric).get("agg") == "stddev":
            continue  # a spread metric's own level is the changepoint's
        if not s.breakdown and common.low_card_breakdowns(ctx, s.metric, "day"):
            continue  # a spread belongs to one process (a plant), not to their mix
        results.append(_test(ctx, s, recent_w, prior_w, min_w))
    eligible = sum(1 for r in results if r.eligible)
    status = (
        "ran"
        if results and eligible == len(results)
        else ("partly" if eligible else "could_not_run")
    )
    return results, Coverage(NAME, status, eligible, len(results), "series")


def _test(ctx: Context, s: Series, recent_w: int, prior_w: int, min_w: int) -> Result:
    end = ctx.run_date
    split = end - dt.timedelta(weeks=recent_w)
    base = Result(
        detector=NAME,
        version=ctx.version(NAME, VERSION),
        series=[s.id],
        metric=s.metric,
        breakdown=s.breakdown,
        breakdown_value=s.breakdown_value,
        window=(split, end - dt.timedelta(days=1)),
        statistic=None,
        p_value=None,
        effect=None,
        strong=False,
        direction="up",
        money=None,
        money_basis="",
        family=common.family(ctx, s.metric),
    )
    pairs = [(p, v) for p, v in zip(s.periods, s.values, strict=True) if np.isfinite(v)]
    if not pairs or (end - pairs[0][0]).days < min_w * 7:
        base.eligible, base.reason = False, "insufficient history"
        return base
    # when it started: among the last CANDIDATES weeks, the one that best splits the narrow days
    # from the wide ones, with the spread since well above that of the 16 weeks before them; then
    # since it started against the prior_w weeks before that (docs/04: recent vs prior), paying
    # for the choice of start (Bonferroni). A start under recent_w weeks back is still the start
    # reported (and the money since it); the test then reads the last recent_w weeks
    first_candidate = end - dt.timedelta(weeks=CANDIDATES)
    base_vals = np.array(
        [v for p, v in pairs if first_candidate - dt.timedelta(weeks=16) <= p < first_candidate]
    )
    vb = float(np.var(base_vals, ddof=1)) if len(base_vals) > 10 else 0.0
    if ctx.version(NAME, VERSION) == "1":
        tested = _start_v1(pairs, end, first_candidate, vb) if vb > 0 else split
        chosen = tested != split
        if (end - tested).days < recent_w * 7:
            tested, chosen = split, False
        started = tested
    else:
        found = _start(pairs, end, first_candidate, base_vals, vb) if vb > 0 else None
        tested = found if found is not None and (end - found).days >= recent_w * 7 else split
        chosen = found is not None and tested == found
        started = found or tested
    prior_start = tested - dt.timedelta(weeks=prior_w)
    recent = np.array([v for p, v in pairs if tested <= p < end])
    prior = np.array([v for p, v in pairs if prior_start <= p < tested])
    if len(recent) < 10 or len(prior) < 30:
        base.eligible, base.reason = False, "insufficient history"
        return base
    vr, vp = float(np.var(recent, ddof=1)), float(np.var(prior, ddof=1))
    f = vr / vp if vp > 0 else np.inf
    p = float(stats.f.sf(f, len(recent) - 1, len(prior) - 1)) if np.isfinite(f) else 0.0
    if chosen:
        p = min(1.0, p * CANDIDATES)
    # the level held: its mean moved under 10% (docs/11 P7: "the average held at 4.1 days");
    # a level that moved more is a changepoint's to report
    mp = float(np.mean(prior))
    held = abs(float(np.mean(recent)) - mp) <= LEVEL_HELD * abs(mp)
    base.statistic = dec(f)
    base.p_value = pval(p if held else 1.0)
    base.effect = dec(np.sqrt(f) if np.isfinite(f) else None)
    base.strong = f >= STRONG_F
    base.params = {
        "recent_weeks": recent_w,
        "prior_weeks": prior_w,
        "level_held": str(LEVEL_HELD),
    }

    def q(a: np.ndarray, k: float) -> float:
        return common.percentile(a, k)

    base.slots = {
        "mean_recent": dec(float(np.mean(recent))),
        "mean_prior": dec(float(np.mean(prior))),
        "sd_recent": dec(np.sqrt(vr)),
        "sd_prior": dec(np.sqrt(vp)),
        "p90_recent": dec(q(recent, 0.9)),
        "p90_prior": dec(q(prior, 0.9)),
        "p10_recent": dec(q(recent, 0.1)),
        "p10_prior": dec(q(prior, 0.1)),
        "started": started.isoformat(),
        "unit": common.unit(ctx, s.metric),
    }
    base.window = (started, end - dt.timedelta(days=1))
    if not held:
        base.reason = "the level moved too (a changepoint, not a spread)"
    money, target = common.at_stake(ctx, s, started, end)
    if money is not None:
        base.money = dec(money, places=PAISA)
        base.money_basis = f"{target} for {s.label()} since {started.isoformat()}"
    return base


def _start(
    pairs: list[tuple[dt.date, float]], end: dt.date, start: dt.date, base: np.ndarray, vb: float
) -> dt.date | None:
    """The week from `start` that best splits the narrow days before it (the baseline's and the
    candidate weeks' before it) from the wide days since it (the split with the smallest
    two-part spread, the earliest on a tie), among weeks whose spread since is over 2.5 × the
    baseline's (it stayed up); else None. Version 1 took the first such week, and a
    quiet week passed once the wide weeks after it were pooled with it, so the start was always
    the first candidate week: run date − 8 weeks (25 Nov 2026, the Coimbatore date)."""
    weeks: dict[dt.date, list[float]] = {}
    for p, v in pairs:
        if start <= p < end:
            weeks.setdefault(p - dt.timedelta(days=p.weekday()), []).append(v)
    order = sorted(weeks)
    best: tuple[float, dt.date] | None = None
    for i, w in enumerate(order):
        after = [v for x in order[i:] for v in weeks[x]]
        if len(weeks[w]) < 4 or len(after) < 10 or float(np.var(after, ddof=1)) <= 2.5 * vb:
            continue
        before = np.concatenate([base, [v for x in order[:i] for v in weeks[x]]])
        cost = len(before) * np.log(float(np.var(before))) + len(after) * np.log(
            float(np.var(after))
        )
        if best is None or cost < best[0]:
            best = (cost, w)
    return best[1] if best is not None else None


def _start_v1(
    pairs: list[tuple[dt.date, float]], end: dt.date, start: dt.date, vb: float
) -> dt.date:
    """Version 1, kept for runs made with it: the first week from `start` whose spread is over
    2.5 × the baseline's and stays up (its own and the following weeks' values together), else
    `end` − 4 weeks."""
    weeks: dict[dt.date, list[float]] = {}
    for p, v in pairs:
        if start <= p < end:
            weeks.setdefault(p - dt.timedelta(days=p.weekday()), []).append(v)
    order = sorted(weeks)
    for i, w in enumerate(order):
        after = [v for x in order[i:] for v in weeks[x]]
        if len(weeks[w]) >= 4 and len(after) >= 10 and float(np.var(after, ddof=1)) > 2.5 * vb:
            return w
    return end - dt.timedelta(weeks=4)


register(Spec(NAME, VERSION, "Rising variance", run))
