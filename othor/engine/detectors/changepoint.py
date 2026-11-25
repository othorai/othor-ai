# SPDX-License-Identifier: Apache-2.0
"""Changepoint (docs/04 §3, detector 2): the week the baseline moved.

PELT (ruptures, model l2) on the last 52 weekly points with a BIC-like penalty, 2·log n·σ²,
where σ is the noise of week-to-week differences (MAD). The most recent break that has held at
least 4 weeks, and is under 26 weeks old, is confirmed by Welch's two-sample test, before vs
after. Snapshot metrics use their daily points rolled to week ends, back-filled weeks included;
a change seen only on reconstructed weeks is Assessed, never Measured.

Money: for a stock (a snapshot, or a share of stocks) the step in ₹ of its ₹ part; for a flow,
the step × the weeks it has held. A number with no ₹ part puts at risk the share of its
`at_stake` ₹ since the change that the size of the step says, capped (`common.at_stake_of_change`).
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

NAME = "changepoint"
# 1: a non-₹ step has no money; 2: it puts its at_stake ₹ at risk in full (24 Nov 2026); 3: only
# the share of it the size of the step says, capped (#138, 25 Nov 2026). The earlier versions stay
# in the code: a run replays as the version it was made with (`Context.versions`)
VERSION = "3"
WINDOW = 52
MIN_SEGMENT = 4
STRONG_SIGMAS = 3.0
STRONG_EFFECT = 0.2


def _sigma(x: np.ndarray) -> float:
    d = np.diff(x)
    mad = float(np.median(np.abs(d - np.median(d)))) if len(d) else 0.0
    return 1.4826 * mad / np.sqrt(2) if mad > 0 else float(np.std(d) / np.sqrt(2) or 1e-9)


def last_break(x: np.ndarray) -> int | None:
    """Index where the last segment starts, if PELT finds one with ≥ MIN_SEGMENT points
    on both sides."""
    import ruptures as rpt

    n = len(x)
    if n < 2 * MIN_SEGMENT:
        return None
    sigma = _sigma(x)
    pen = 2 * np.log(n) * sigma**2
    bkps = rpt.Pelt(model="l2", min_size=MIN_SEGMENT, jump=1).fit(x.reshape(-1, 1)).predict(pen=pen)
    starts = [b for b in bkps[:-1] if MIN_SEGMENT <= b <= n - MIN_SEGMENT]
    return starts[-1] if starts else None


def test_series(ctx: Context, s: Series) -> Result | None:
    vals = common.masked(ctx, s)
    ok = np.isfinite(vals)
    periods = [p for p, k in zip(s.periods, ok, strict=True) if k][-WINDOW:]
    x = vals[ok][-WINDOW:]
    recon = [r for r, k in zip(s.reconstructed, ok, strict=True) if k][-WINDOW:]
    base = Result(
        detector=NAME,
        version=ctx.version(NAME, VERSION),
        series=[s.id],
        metric=s.metric,
        breakdown=s.breakdown,
        breakdown_value=s.breakdown_value,
        window=(periods[0], periods[-1]) if periods else (ctx.run_date, ctx.run_date),
        statistic=None,
        p_value=None,
        effect=None,
        strong=False,
        direction="none",
        money=None,
        money_basis="",
        family=common.family(ctx, s.metric),
    )
    days = (periods[-1] - periods[0]).days + 7 if periods else 0
    if len(x) < 8 or days < int(ctx.config(NAME).get("min_days", 28)):
        base.eligible, base.reason = False, "insufficient history"
        return base
    b = last_break(x)
    if b is None:
        base.p_value, base.statistic = pval(1.0), dec(0.0)
        return base
    before, after = x[max(0, b - WINDOW // 2) : b], x[b:]
    t = stats.ttest_ind(after, before, equal_var=False)
    delta = float(np.mean(after) - np.mean(before))
    mean_before = float(np.mean(before))
    effect = delta / abs(mean_before) if mean_before else 0.0
    change = periods[b]
    open_ = common.weeks_between(change, ctx.run_date) <= common.RECENT_WEEKS
    sigma = _sigma(x)
    base.window = (change, periods[-1])
    base.statistic = dec(float(t.statistic))
    base.p_value = pval(float(t.pvalue) if open_ else 1.0)
    base.effect = dec(effect)
    base.direction = "up" if delta > 0 else "down"
    base.strong = abs(delta) >= STRONG_SIGMAS * sigma and abs(effect) >= STRONG_EFFECT
    base.reconstructed_only = all(recon[max(0, b - 4) : b + 4])
    base.state_hint = "assessed" if base.reconstructed_only else "measured"
    held = len(after)
    base.params = {"pen": "2·log(n)·σ²", "window_weeks": WINDOW, "min_segment": MIN_SEGMENT}
    base.slots = {
        "before": dec(mean_before),
        "after": dec(float(np.mean(after))),
        "delta": dec(delta),
        "change_week": change.isoformat(),  # the text's date: the same on any history
        "weeks_held": held,
        "unit": common.unit(ctx, s.metric),
    }
    # the day itself, only where real daily points place it (evidence, not the text: P15)
    day = _refine(ctx, s, change, mean_before, float(np.mean(after)))
    if day is not None:
        base.slots["change_date"] = day.isoformat()
    if not open_:
        base.reason = "the change is older than 26 weeks"
    base.money, base.money_basis = _money(
        ctx, s, b, periods, held, (mean_before, float(np.mean(after))), base
    )
    return base


def _refine(ctx: Context, s: Series, week: dt.date, before: float, after: float) -> dt.date | None:
    """The first day the daily points crossed the midpoint, when real daily points exist."""
    d = ctx.series.get(s.metric, "day", s.breakdown, s.breakdown_value)
    if d is None:
        return None
    mid = (before + after) / 2
    up = after > before
    lo, hi = week - dt.timedelta(days=7), week + dt.timedelta(days=13)
    days = [(p, v) for p, v in zip(d.periods, d.values, strict=True) if lo <= p <= hi]
    # keep only real daily runs of points (back-filled Sundays can't place a day)
    if len(days) < 7:
        return None
    for p, v in days:
        if np.isfinite(v) and ((v >= mid) if up else (v <= mid)):
            return p
    return None


def _money(
    ctx: Context,
    s: Series,
    b: int,
    periods: list[dt.date],
    held: int,
    levels: tuple[float, float],
    res: Result,
) -> tuple[str | None, str]:
    rupee = common.inr_component(ctx, s.metric)
    if rupee is None:
        # no ₹ of its own (truck fill, stock in kg, a count): the ₹ through the metric it names in
        # `at_stake` since the change is at risk (owner, 24 Nov 2026), since v3 only the share of
        # it the size of the move says, capped (#138, 25 Nov 2026); no link, no money
        v = ctx.version(NAME, VERSION)
        if v == "1":
            return None, ""
        change = periods[b]
        got, basis, params = common.at_stake_of_change(
            ctx, s, change, periods[-1] + dt.timedelta(days=7), *levels, scaled=v != "2"
        )
        res.params = {**res.params, **params}
        if params.get("at_risk_scaling") == "from_zero":
            # the money can't be sized: still reported, and the finding says so (owner, 25 Nov)
            res.slots["money_unsized"] = True
        if got is None:
            return None, basis
        return dec(got, places=PAISA), basis
    src = s if rupee == s.metric else ctx.series.get(rupee, s.grain, s.breakdown, s.breakdown_value)
    if src is None:
        return None, ""
    vals = {p: v for p, v in zip(src.periods, src.values, strict=True) if np.isfinite(v)}
    change = periods[b]
    before = [vals[p] for p in periods[max(0, b - WINDOW // 2) : b] if p in vals]
    after = [vals[p] for p in periods[b:] if p in vals]
    if not before or not after:
        return None, ""
    step = abs(float(np.mean(after)) - float(np.mean(before)))
    if common.is_snapshot(ctx, rupee):
        return dec(step, places=PAISA), f"step in {rupee} from {change.isoformat()}"
    return (
        dec(step * held, places=PAISA),
        f"weekly step in {rupee} × {held} weeks since {change.isoformat()}",
    )


def run(ctx: Context) -> tuple[list[Result], Coverage]:
    results = []
    cands = common.candidates(ctx, "week")
    for s in cands:
        r = test_series(ctx, s)
        if r is not None:
            results.append(r)
    eligible = sum(1 for r in results if r.eligible)
    status = "ran" if eligible == len(results) else ("partly" if eligible else "could_not_run")
    return results, Coverage(NAME, status, eligible, len(results), "series")


register(Spec(NAME, VERSION, "Changepoint", run))
