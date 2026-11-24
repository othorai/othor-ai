# SPDX-License-Identifier: Apache-2.0
# ruff: noqa  (the GTM draft's code, kept as written but for formatting; its numbers are the post's)
"""The simulation behind the long read "The Most Dangerous Equation" (22 Nov 2026). Copied from
the GTM draft (othor-gtm, content/drafts/blog-assets/src-most-dangerous-equation/sim.py), only
reformatted (same output, byte for byte); tests/ui/preview_blog_long_read_22nov.py draws the
post's figures from it.

ILLUSTRATION, NOT DATA. Sixty invented branches; no real company.

Each branch issues n invoices a month (n spread evenly on a log scale from 15 to 1,500).
Each has a true chance that an invoice is paid late: drawn around 20% with a spread (sd) of
1 point, except one large branch (the planted one, "Branch P") whose true rate is 27%.
Months one and two are independent draws from the same true rates.

Pure Python (random.Random with a fixed seed; binomial draws as sums of Bernoulli trials) so the
numbers don't depend on a numpy version. Run: .venv/bin/python tests/ui/blog_sim_dangerous.py
→ prints the summary, writes tests/ui/blog_sim_dangerous.json.
"""

import json, math, random
from pathlib import Path

SEED = 20261122
N_BRANCH = 60
P0, TAU = 0.20, 0.01
PLANTED_P = 0.27
Z95, Z998 = 1.96, 3.09


def simulate(seed=SEED):
    rng = random.Random(seed)
    sizes = [round(15 * (1500 / 15) ** (i / (N_BRANCH - 1))) for i in range(N_BRANCH)]
    true = [min(max(rng.gauss(P0, TAU), 0.01), 0.99) for _ in sizes]
    planted = N_BRANCH - 6  # one of the six largest
    true[planted] = PLANTED_P

    def draw(n, p):
        return sum(1 for _ in range(n) if rng.random() < p)

    m1 = [draw(n, p) for n, p in zip(sizes, true)]
    m2 = [draw(n, p) for n, p in zip(sizes, true)]
    rows = []
    for i, (n, p, k1, k2) in enumerate(zip(sizes, true, m1, m2)):
        rows.append(
            dict(
                id=i,
                name=f"B{i + 1:02d}",
                n=n,
                true=p,
                k1=k1,
                r1=k1 / n,
                k2=k2,
                r2=k2 / n,
                planted=(i == planted),
            )
        )
    # pooled rate (all invoices), as a funnel plot's centre line
    pool = sum(r["k1"] for r in rows) / sum(r["n"] for r in rows)
    for r in rows:
        se = math.sqrt(pool * (1 - pool) / r["n"])
        r["se"] = se
        r["z"] = (r["r1"] - pool) / se
        r["out95"] = abs(r["z"]) > Z95
        r["out998"] = abs(r["z"]) > Z998
    # empirical Bayes: between-branch variance tau² by the weighted method of moments
    # (DerSimonian–Laird): Q = Σ wᵢ(rᵢ − r̄)², wᵢ = 1/vᵢ, tau² = max(0, (Q − (k − 1)) / (Σw − Σw²/Σw))
    v = [pool * (1 - pool) / r["n"] for r in rows]
    wt = [1 / x for x in v]
    rbar = sum(w * r["r1"] for w, r in zip(wt, rows)) / sum(wt)
    Q = sum(w * (r["r1"] - rbar) ** 2 for w, r in zip(wt, rows))
    k = len(rows)
    tau2 = max(0.0, (Q - (k - 1)) / (sum(wt) - sum(w * w for w in wt) / sum(wt)))
    for r, vi in zip(rows, v):
        w = tau2 / (tau2 + vi) if tau2 + vi else 0.0
        r["w"] = w
        r["shrunk"] = pool + w * (r["r1"] - pool)
    var_raw, samp = Q, k - 1
    return rows, dict(pool=pool, tau2=tau2, tau=math.sqrt(tau2), var_raw=var_raw, samp=samp)


def rank(rows, key, reverse=True):
    order = sorted(rows, key=lambda r: (-r[key] if reverse else r[key], r["id"]))
    return {r["id"]: i + 1 for i, r in enumerate(order)}, order


def summary(rows, g):
    third = sorted(rows, key=lambda r: r["n"])[: N_BRANCH // 3]
    small = {r["id"] for r in third}
    big = {r["id"] for r in sorted(rows, key=lambda r: r["n"])[-N_BRANCH // 3 :]}
    _, worst = rank(rows, "r1", True)
    _, best = rank(rows, "r1", False)
    rk_raw, _ = rank(rows, "r1", True)
    rk_shr, worst_s = rank(rows, "shrunk", True)
    p = next(r for r in rows if r["planted"])
    top10w = worst[:10]
    top10b = best[:10]
    out = dict(
        pool=g["pool"],
        tau=g["tau"],
        small_max_n=max(r["n"] for r in third),
        big_min_n=min(r["n"] for r in rows if r["id"] in big),
        worst10_small=sum(r["id"] in small for r in top10w),
        best10_small=sum(r["id"] in small for r in top10b),
        worst10_big=sum(r["id"] in big for r in top10w),
        best10_big=sum(r["id"] in big for r in top10b),
        worst1=dict(
            name=worst[0]["name"], n=worst[0]["n"], r1=worst[0]["r1"], true=worst[0]["true"]
        ),
        best1=dict(name=best[0]["name"], n=best[0]["n"], r1=best[0]["r1"], true=best[0]["true"]),
        planted=dict(
            name=p["name"],
            n=p["n"],
            true=p["true"],
            r1=p["r1"],
            z=p["z"],
            rank_raw=rk_raw[p["id"]],
            rank_shrunk=rk_shr[p["id"]],
            shrunk=p["shrunk"],
            w=p["w"],
        ),
        out95=[r["name"] for r in rows if r["out95"]],
        out998=[r["name"] for r in rows if r["out998"]],
        # regression to the mean: the 10 worst in month one, in month two
        worst10_m1_avg=sum(r["r1"] for r in top10w) / 10,
        worst10_m2_avg=sum(r["r2"] for r in top10w) / 10,
        best10_m1_avg=sum(r["r1"] for r in top10b) / 10,
        best10_m2_avg=sum(r["r2"] for r in top10b) / 10,
        worst10_still_worst10_m2=len(
            {r["id"] for r in top10w} & {r["id"] for r in rank(rows, "r2", True)[1][:10]}
        ),
        shrunk_worst5=[
            (r["name"], r["n"], round(r["r1"], 4), round(r["shrunk"], 4)) for r in worst_s[:5]
        ],
        raw_range=(min(r["r1"] for r in rows), max(r["r1"] for r in rows)),
        true_range=(
            min(r["true"] for r in rows if not r["planted"]),
            max(r["true"] for r in rows if not r["planted"]),
        ),
        smallest_w=min(r["w"] for r in rows),
        largest_w=max(r["w"] for r in rows),
    )
    return out


def long_run(reps=2000):
    """The same 60 branches' world drawn `reps` times (seeds SEED+1 … SEED+reps): how often each size
    third lands in the 10 highest or 10 lowest late rates, and how often the planted branch is
    the worst (raw vs shrunk) and outside the 99.8% funnel."""
    ext = {"small": 0, "middle": 0, "large": 0}
    worst1_raw = worst1_shr = out998 = 0
    for k in range(1, reps + 1):
        rows, _ = simulate(SEED + k)
        by_n = sorted(rows, key=lambda r: r["n"])
        third = {
            r["id"]: ("small" if i < 20 else "middle" if i < 40 else "large")
            for i, r in enumerate(by_n)
        }
        order = sorted(rows, key=lambda r: (-r["r1"], r["id"]))
        for r in order[:10] + order[-10:]:
            ext[third[r["id"]]] += 1
        p = next(r for r in rows if r["planted"])
        worst1_raw += order[0]["planted"]
        worst1_shr += max(rows, key=lambda r: (r["shrunk"], -r["id"]))["planted"]
        out998 += p["out998"]
    slots = 20 * reps
    return dict(
        reps=reps,
        extreme_share={k: v / slots for k, v in ext.items()},
        planted_worst_raw=worst1_raw / reps,
        planted_worst_shrunk=worst1_shr / reps,
        planted_out998=out998 / reps,
    )


if __name__ == "__main__":
    rows, g = simulate()
    s = summary(rows, g)
    s["long_run"] = long_run()
    (Path(__file__).resolve().parent / "blog_sim_dangerous.json").write_text(
        json.dumps(dict(summary=s, rows=rows), indent=1)
    )
    print(json.dumps(s, indent=1))
