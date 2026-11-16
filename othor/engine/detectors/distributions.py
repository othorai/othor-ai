# SPDX-License-Identifier: Apache-2.0
"""Detectors on value distributions (docs/04 §3): Bimodal average (11) and Process artefact (9).

Bimodal average: an average metric whose per-event values (orders) sit in two groups. Hartigan's
dip on log values (seeded uniform null), and a two-component mixture chosen by BIC to describe
the groups. Fires when the dip is significant and the mixture prefers two groups. No money:
until a target is set, a split average has no ₹ at stake (below materiality).

Process artefact: mass piled just under an Ontology threshold (`threshold` rule: a limit on an
amount, e.g. the ₹5 L discount approval limit). Counts in equal bins just below and just above the
limit should be about equal for a smooth density; a one-sided binomial test on the share below.
No money until the owner says what an over-limit item costs (below materiality).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
from scipy import stats

from othor.engine.detectors import dip, mixture
from othor.engine.detectors.base import Context, Coverage, Result, Spec, dec, pval, register

MAX_VALUES = 500  # the dip on evenly spaced order statistics (error ≤ 1/MAX_VALUES)
BIMODAL = "bimodal_average"
ARTEFACT = "process_artefact"
VERSION = "1"


def _result(ctx: Context, name: str, key: str, metric: str) -> Result:
    return Result(
        detector=name,
        version=VERSION,
        series=[f"{metric}|event||{key}"],
        metric=metric,
        breakdown="",
        breakdown_value=None,
        window=(ctx.run_date - dt.timedelta(days=365), ctx.run_date - dt.timedelta(days=1)),
        statistic=None,
        p_value=None,
        effect=None,
        strong=False,
        direction="none",
        money=None,
        money_basis="no money at stake until a target is set",
        family=f"{name}:{key}",
    )


def thin(x: np.ndarray) -> np.ndarray:
    x = np.sort(x)
    if len(x) <= MAX_VALUES:
        return x
    idx = np.round(np.linspace(0, len(x) - 1, MAX_VALUES)).astype(int)
    out: np.ndarray = x[idx]
    return out


def bimodal(ctx: Context) -> tuple[list[Result], Coverage]:
    probes = ctx.probes.get("distributions") or []
    if not probes:
        return [], Coverage(BIMODAL, "could_not_run", 0, 0, "metrics", needs="distribution")
    min_values = int(ctx.config(BIMODAL).get("min_values", 100))
    out = []
    for pr in probes:
        r = _result(ctx, BIMODAL, pr["key"], pr["metric"])
        v = np.array([float(x) for x in pr.get("values", []) if x is not None and float(x) > 0])
        if len(v) < min_values:
            r.eligible, r.reason = False, "insufficient events"
            out.append(r)
            continue
        lv = thin(np.log(v))
        d, p = dip.p_value(lv, ctx.rng(r.series[0]))
        chosen, _ = mixture.best(lv)
        two = chosen.k == 2
        r.statistic, r.effect = dec(d), dec(d * np.sqrt(len(v)))
        r.p_value = pval(p if two else 1.0)
        r.strong = two and min(chosen.weights) >= 0.1
        r.slots = {
            "values": len(v),
            "mean": dec(float(np.mean(v))),
            "groups": [
                {"weight": dec(w), "typical": dec(float(np.exp(m)))}
                for w, m in zip(chosen.weights, chosen.means, strict=True)
            ]
            if two
            else [],
        }
        r.params = {"null": "uniform", "samples": dip.NULL_SAMPLES, "scale": "log"}
        if not two:
            r.reason = "one group"
        out.append(r)
    return out, Coverage(BIMODAL, "ran", sum(1 for r in out if r.eligible), len(out), "metrics")


def artefact(ctx: Context) -> tuple[list[Result], Coverage]:
    probes = ctx.probes.get("thresholds") or []
    if not probes:
        return [], Coverage(ARTEFACT, "could_not_run", 0, 0, "thresholds", needs="threshold")
    min_events = int(ctx.config(ARTEFACT).get("min_events", 200))
    out = []
    for pr in probes:
        r = _result(ctx, ARTEFACT, pr["key"], pr.get("metric") or pr["key"])
        below, above, events = int(pr["below"]), int(pr["above"]), int(pr["events"])
        r.series = [f"threshold|{pr['key']}"]
        if events < min_events or below + above == 0:
            r.eligible, r.reason = False, "insufficient events"
            out.append(r)
            continue
        test = stats.binomtest(below, below + above, 0.5, alternative="greater")
        ratio = below / above if above else float("inf")
        r.statistic, r.p_value = (
            dec(ratio if np.isfinite(ratio) else None),
            pval(float(test.pvalue)),
        )
        r.effect = dec(below / (below + above))
        r.strong = ratio >= 2.5
        r.direction = "up"
        r.slots = {
            "limit": pr["limit"],
            "band": pr["band"],
            "below": below,
            "above": above,
            "events": events,
            "label": pr.get("label", ""),
        }
        r.params = {"test": "binomial, one-sided", "band": pr["band"]}
        out.append(r)
    return out, Coverage(ARTEFACT, "ran", sum(1 for r in out if r.eligible), len(out), "thresholds")


register(Spec(BIMODAL, VERSION, "Bimodal average", bimodal, needs=("distribution",)))
register(Spec(ARTEFACT, VERSION, "Process artefact", artefact, needs=("threshold",)))
