# SPDX-License-Identifier: Apache-2.0
"""Detectors on declared metric pairs (Ontology `pair` rules): Broken relationship (5) and
Lead/lag (6), docs/04 §3. Both need a pair record; without one they write "needs pair".

Broken relationship: weekly values of the two metrics. The break is the last changepoint of
their ratio in the last 16 weeks; Spearman r over the 20 weeks before it against r after it,
Fisher z test on the drop. Money: the excess per month at the old ratio,
(ratio after − ratio before) × the second metric per month after the break.

Lead/lag: lagged Spearman over lags 1–8 weeks on 26+ weeks; the best lag's r against a seeded
permutation null of the best r over all lags (so choosing the lag is paid for). No money: an
insight for the Ontology, never a finding by itself.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
from scipy import stats

from othor.engine.detectors import changepoint
from othor.engine.detectors.base import PAISA, Context, Coverage, Result, Spec, dec, pval, register

BROKEN = "broken_relationship"
LEADLAG = "lead_lag"
VERSION = "1"
RECENT = 16
STRONG_DROP = 0.4
PERMUTATIONS = 999


def _aligned(
    ctx: Context, a: str, b: str, grain: str = "week"
) -> tuple[list[dt.date], np.ndarray, np.ndarray]:
    sa, sb = ctx.series.get(a, grain), ctx.series.get(b, grain)
    if sa is None or sb is None:
        return [], np.array([]), np.array([])
    va = {p: v for p, v in zip(sa.periods, sa.values, strict=True) if np.isfinite(v)}
    vb = {p: v for p, v in zip(sb.periods, sb.values, strict=True) if np.isfinite(v)}
    ps = sorted(set(va) & set(vb))
    return ps, np.array([va[p] for p in ps]), np.array([vb[p] for p in ps])


def _pairs(ctx: Context, relation: str) -> list[dict[str, Any]]:
    return [p for p in ctx.spec.get("pairs", []) if p.get("relation") == relation]


def _result(ctx: Context, name: str, pair: dict[str, Any]) -> Result:
    a, b = pair["a"], pair["b"]
    return Result(
        detector=name,
        version=VERSION,
        series=[f"{a}|week||", f"{b}|week||"],
        metric=a,
        breakdown="",
        breakdown_value=None,
        window=(ctx.run_date, ctx.run_date),
        statistic=None,
        p_value=None,
        effect=None,
        strong=False,
        direction="down" if name == BROKEN else "none",
        money=None,
        money_basis="",
        entities=[a, b],
        family=f"pair:{pair['key']}",
    )


def broken(ctx: Context) -> tuple[list[Result], Coverage]:
    pairs = _pairs(ctx, "together")
    if not pairs:
        return [], Coverage(BROKEN, "could_not_run", 0, 0, "pairs", needs="pair")
    min_w = int(ctx.config(BROKEN).get("min_weeks", 20))
    out = []
    for pair in pairs:
        r = _result(ctx, BROKEN, pair)
        ps, x, y = _aligned(ctx, pair["a"], pair["b"])
        if len(ps) < min_w + 6:
            r.eligible, r.reason = False, "insufficient history"
            out.append(r)
            continue
        ratio = np.where(y != 0, x / np.where(y == 0, 1, y), np.nan)
        recent = slice(max(0, len(ps) - RECENT), len(ps))
        b = changepoint.last_break(ratio[recent])
        cut = (recent.start + b) if b is not None else len(ps) - 8
        cut = min(cut, len(ps) - 6)
        before = slice(max(0, cut - min_w), cut)
        xb, yb, xa, ya = x[before], y[before], x[cut:], y[cut:]
        r1 = float(stats.spearmanr(xb, yb).statistic)
        r2 = float(stats.spearmanr(xa, ya).statistic)

        # the relationship before the break, and whether the weeks after fit it: Chow's
        # predictive-failure F-test (allows for the fit's own uncertainty)
        def rss(xx: np.ndarray, yy: np.ndarray) -> float:
            f = stats.linregress(yy, xx)
            return float(np.sum((xx - (f.intercept + f.slope * yy)) ** 2))

        n1, n2 = len(xb), len(xa)
        rss1 = rss(xb, yb)
        rss_all = rss(np.concatenate([xb, xa]), np.concatenate([yb, ya]))
        chi = ((rss_all - rss1) / n2) / (rss1 / (n1 - 2)) if rss1 > 0 else 0.0
        # the break week was chosen from the data: pay for the choice (Bonferroni over the
        # candidate weeks); a relationship that didn't hold before can't break
        chosen = RECENT if b is not None else 1
        raw = float(stats.f.sf(chi, n2, n1 - 2))
        p = min(1.0, raw * chosen) if r1 > 0.6 else 1.0
        mean_before = float(np.mean(xb[-12:] / yb[-12:]))  # mean weekly ratio (freight per metre)
        mean_after = float(np.mean(xa / ya))
        months = len(xa) * 7 / 30.4375
        per_month = (mean_after - mean_before) * float(np.sum(ya)) / months if months else 0.0
        r.window = (ps[cut], ps[-1])
        r.statistic = dec(chi)
        r.p_value = pval(p)
        r.effect = dec(r1 - r2)
        r.strong = (r1 - r2) >= STRONG_DROP and r1 >= 0.8
        r.slots = {
            "r_before": dec(r1),
            "r_after": dec(r2),
            "weeks_before": len(xb),
            "weeks_after": len(xa),
            "break": ps[cut].isoformat(),
            "ratio_before": dec(mean_before),
            "ratio_after": dec(mean_after),
            "ratio_change": dec((mean_after - mean_before) / mean_before if mean_before else None),
            "money_per_month": dec(per_month, places=PAISA) if per_month > 0 else None,
        }
        r.params = {
            "min_weeks": min_w,
            "recent_weeks": RECENT,
            "test": "χ² of residuals vs the fit before",
        }
        if per_month > 0:
            r.money = dec(per_month * 12, places=PAISA)
            r.money_basis = (
                f"12 months at the excess per month of {pair['a']} "
                f"over the old ratio to {pair['b']}"
            )
        out.append(r)
    return out, Coverage(BROKEN, "ran", sum(1 for r in out if r.eligible), len(out), "pairs")


def lead_lag(ctx: Context) -> tuple[list[Result], Coverage]:
    pairs = _pairs(ctx, "leads")
    if not pairs:
        return [], Coverage(LEADLAG, "could_not_run", 0, 0, "pairs", needs="pair")
    cfg = ctx.config(LEADLAG)
    min_w, max_lag = int(cfg.get("min_weeks", 26)), int(cfg.get("max_lag_weeks", 8))
    out = []
    for pair in pairs:
        r = _result(ctx, LEADLAG, pair)
        ps, x, y = _aligned(ctx, pair["a"], pair["b"])
        if len(ps) < min_w + max_lag:
            r.eligible, r.reason = False, "insufficient history"
            out.append(r)
            continue
        x, y = x[-(min_w * 2 + max_lag) :], y[-(min_w * 2 + max_lag) :]

        def best(xx: np.ndarray, yy: np.ndarray = y) -> tuple[int, float]:
            got = []
            for lag in range(1, max_lag + 1):
                got.append((float(stats.spearmanr(xx[:-lag], yy[lag:]).statistic), -lag))
            rr, neg = max(got)
            return -neg, rr

        lag, rbest = best(x)
        rng = ctx.rng(r.series[0] + "|" + r.series[1])
        null = [best(rng.permutation(x))[1] for _ in range(PERMUTATIONS)]
        p = (1 + sum(1 for v in null if v >= rbest)) / (PERMUTATIONS + 1)
        r.statistic, r.p_value, r.effect = dec(rbest), pval(p), dec(rbest)
        r.strong = rbest >= 0.6
        r.window = (ps[-len(x)], ps[-1])
        r.slots = {"lag_weeks": lag, "r": dec(rbest), "leader": pair["a"], "follower": pair["b"]}
        r.params = {
            "max_lag_weeks": max_lag,
            "permutations": PERMUTATIONS,
            "seed": "spec|date|pair",
        }
        r.money_basis = "an insight: no money at stake"
        out.append(r)
    return out, Coverage(LEADLAG, "ran", sum(1 for r in out if r.eligible), len(out), "pairs")


register(Spec(BROKEN, VERSION, "Broken relationship", broken, needs=("pair",)))
register(Spec(LEADLAG, VERSION, "Lead/lag", lead_lag, needs=("pair",)))
