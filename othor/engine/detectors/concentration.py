# SPDX-License-Identifier: Apache-2.0
"""Concentration (docs/04 §3, detector 7): a few names carry most of the value, and more so.

For each ₹ metric with an entity breakdown (customers): the top-N entities at the latest period
(spec `top_n`, 4), their share of the total in each period of the window (26 weeks for a
snapshot, 12 months for a flow), and the trend of that share (OLS slope, one-sided t-test). It
fires when the latest share is above `share` (50%) and rising.

Money: the value above the threshold share now, (share − 50%) × total.
"""

from __future__ import annotations

import numpy as np
from scipy import stats

from othor.engine.detectors import common
from othor.engine.detectors.base import Context, Coverage, Result, Series, Spec, dec, pval, register

NAME = "concentration"
VERSION = "1"
OTHER = "Other"
STRONG_RISE = 0.10  # share points over the window


def run(ctx: Context) -> tuple[list[Result], Coverage]:
    cfg = ctx.config(NAME)
    top_n = int(cfg.get("top_n", 4))
    limit = common.config_num(cfg, "share", 0.5)
    min_periods = int(cfg.get("min_periods", 3))
    entities = [k for k, b in ctx.spec.get("breakdowns", {}).items() if b.get("entity")]
    results: list[Result] = []
    for metric in ctx.active_metrics():
        if common.unit(ctx, metric) != "inr" or ctx.metric(metric).get("type") != "base":
            continue
        snap = common.is_snapshot(ctx, metric)
        grain, window = ("week", 26) if snap else ("month", 12)
        for b in entities:
            parts = ctx.series.values_of(metric, grain, b)
            total = ctx.series.get(metric, grain)
            if not parts or total is None:
                continue
            results.append(
                _test(ctx, metric, b, grain, window, parts, total, top_n, limit, min_periods)
            )
    eligible = sum(1 for r in results if r.eligible)
    if not entities:
        return [], Coverage(NAME, "could_not_run", 0, 0, "metrics", needs="entity")
    return results, Coverage(
        NAME, "ran" if eligible else "could_not_run", eligible, len(results), "metrics"
    )


def _test(
    ctx: Context,
    metric: str,
    b: str,
    grain: str,
    window: int,
    parts: list[Series],
    total: Series,
    top_n: int,
    limit: float,
    min_periods: int,
) -> Result:
    periods = [p for p, v in zip(total.periods, total.values, strict=True) if np.isfinite(v)][
        -window:
    ]
    r = Result(
        detector=NAME,
        version=VERSION,
        series=[f"{metric}|{grain}|{b}|"],
        metric=metric,
        breakdown=b,
        breakdown_value=None,
        window=(periods[0], periods[-1]) if periods else (ctx.run_date, ctx.run_date),
        statistic=None,
        p_value=None,
        effect=None,
        strong=False,
        direction="up",
        money=None,
        money_basis="",
        family=common.family(ctx, metric),
    )
    if len(periods) < max(min_periods, 6):
        r.eligible, r.reason = False, "insufficient history"
        return r
    tot = dict(zip(total.periods, total.values, strict=True))
    by = {s.breakdown_value or "": dict(zip(s.periods, s.values, strict=True)) for s in parts}
    last = periods[-1]

    def size(k: str) -> float:
        v = float(by[k].get(last, np.nan))
        return v if np.isfinite(v) else 0.0

    ranked = sorted((k for k in by if k is not None and k != OTHER), key=lambda k: (-size(k), k))
    top = ranked[:top_n]
    share = np.array(
        [
            sum(by[k].get(p, 0.0) for k in top if np.isfinite(by[k].get(p, np.nan))) / tot[p]
            if tot[p]
            else np.nan
            for p in periods
        ]
    )
    ok = np.isfinite(share)
    t = np.arange(len(periods))[ok]
    fit = stats.linregress(t, share[ok])
    p_rise = float(stats.t.sf(fit.slope / fit.stderr, len(t) - 2)) if fit.stderr > 0 else 1.0
    now = float(share[ok][-1])
    fires = now > limit
    rise = float(share[ok][-1] - share[ok][0])
    r.statistic = dec(float(fit.slope / fit.stderr) if fit.stderr > 0 else 0.0)
    r.p_value = pval(p_rise if fires else 1.0)
    r.effect = dec(rise)
    r.strong = rise >= STRONG_RISE
    r.entities = list(top)
    r.state_hint = "assessed"
    hhi = sum(
        (by[k].get(last, 0.0) / tot[last]) ** 2 for k in by if k not in (None, OTHER) and tot[last]
    )
    r.slots = {
        "top_n": top_n,
        "share_now": dec(now),
        "share_start": dec(float(share[ok][0])),
        "start": periods[0].isoformat(),
        "period": last.isoformat(),
        "hhi": dec(hhi),
        "limit": dec(limit),
        "names": list(top),
    }
    r.params = {"top_n": top_n, "share": str(limit), "window": window, "grain": grain}
    r.evidence = [
        {"name": k, "value": dec(by[k].get(last)), "share": dec(by[k].get(last, 0.0) / tot[last])}
        for k in ranked[:10]
    ]
    if not fires:
        r.reason = f"the top {top_n} carry {round(now * 100)}% (limit {round(limit * 100)}%)"
    else:
        from decimal import Decimal

        r.money = dec((now - limit) * float(tot[last]), places=Decimal("0.01"))
        r.money_basis = f"value above {round(limit * 100)}% of {metric} in the top {top_n}"
    return r


register(Spec(NAME, VERSION, "Concentration", run, needs=("entity",)))
