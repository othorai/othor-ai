# SPDX-License-Identifier: Apache-2.0
"""From detector results to findings (docs/04 §3–§6): run the detectors, Benjamini–Hochberg
across the run, then filters and states. Pure: the same outputs, probes, spec and run date give the
same results (replay re-runs this).

Benjamini–Hochberg runs once per pool (Advanced Intelligence Phase 0 §4): every core detector
tests in the "inside" pool, with the spec's q and materiality floor; a plug-in's detector may
test in a pool of its own (`register_pool`) with its own q, and a pool without money skips the
materiality gate. Pools never share a test count, so a plug-in can't move an inside q-value.

A result that doesn't become a finding keeps the first reason that stopped it, in this order:
not eligible → no signal (raw p ≥ 0.01) → below materiality (no ₹ at stake, or under the floor) →
not significant after FDR → suppressed by a known pattern → also seen as another finding (one
finding per detector, family and window).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from othor.engine.detectors import REGISTRY
from othor.engine.detectors.base import Context, Coverage, Result

RAW_P = Decimal("0.01")
HIGH_Q = Decimal("0.001")
PREFER = ("listed", "headline")  # among one family's results, the metric the owner sees
INSIDE = "inside"


@dataclass(frozen=True)
class Pool:
    """A Benjamini–Hochberg pool: its q (None: the spec's `fdr_q`, the inside pool) and whether
    its results must clear the materiality floor (a pool without money doesn't)."""

    name: str
    q: Decimal | None = None
    materiality: bool = True


POOLS: dict[str, Pool] = {INSIDE: Pool(INSIDE)}


def register_pool(name: str, q: Decimal | str, materiality: bool = True) -> Pool:
    if name == INSIDE:
        raise ValueError("the inside pool is the core's")
    pool = Pool(name, Decimal(str(q)), materiality)
    if name in POOLS and POOLS[name] != pool:
        raise ValueError(f"pool {name!r} is already taken")
    POOLS[name] = pool
    return pool


def pool_of(r: Result) -> Pool:
    spec = REGISTRY.get(r.detector)
    return POOLS.get(spec.pool if spec is not None else INSIDE) or POOLS[INSIDE]


@dataclass
class Detection:
    results: list[Result]
    coverage: list[Coverage]

    @property
    def findings(self) -> list[Result]:
        return [r for r in self.results if r.passed]

    def counts(self) -> dict[str, int]:
        tests = [r for r in self.results if r.eligible and r.p_value is not None]
        return {
            "tests": len(tests),
            "findings": len(self.findings),
            # one item per known pattern, whatever metrics and periods it covered (decided
            # 5 Nov 2026): the count Sweep, Home's card and the brief all show
            "suppressed": len(
                {r.reason for r in self.results if (r.reason or "").startswith("suppressed")}
            ),
            "held": sum(1 for r in self.results if _held(r.reason)),
            "below_materiality": sum(1 for r in self.results if r.reason == "below materiality"),
            "could_not_run": sum(1 for c in self.coverage if c.status == "could_not_run"),
        }


def run(ctx: Context) -> Detection:
    from othor.core import features

    results: list[Result] = []
    coverage: list[Coverage] = []
    for spec in REGISTRY.values():
        if not spec.enabled and not (spec.name == "forecast_breach" and features.FORECASTS):
            continue
        got, cov = spec.run(ctx)
        results += got
        coverage.append(cov)
    inside_q = Decimal(str(ctx.spec.get("detectors", {}).get("fdr_q", "0.01")))
    floor = Decimal(str(ctx.spec.get("detectors", {}).get("materiality_inr", "500000")))
    pools: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        pools[pool_of(r).name].append(r)
    for name, rs in sorted(pools.items()):
        pool = POOLS[name]
        bh(rs, inside_q if pool.q is None else pool.q, pool=name)
    for r in results:
        pool = pool_of(r)
        q = inside_q if pool.q is None else pool.q
        r.passed, r.reason = _gate(r, floor if pool.materiality else None, ctx, q)
    _dedupe(ctx, results)
    for r in results:
        if r.passed:
            _allocation_cap(ctx, r)
    for r in results:
        r.state_hint = state(r)[0] if r.passed else r.state_hint
    results.sort(key=lambda r: (r.detector, r.series, r.entities))
    return Detection(results, coverage)


def _held(reason: str | None) -> bool:
    return bool(reason) and (reason.startswith("snoozed until") or reason == "muted")  # type: ignore[union-attr]


def bh(results: list[Result], q: Decimal, pool: str = INSIDE) -> None:
    """Benjamini–Hochberg q-values over every result with a p-value (one pool's tests)."""
    tested = [
        r for r in results if r.eligible and r.p_value is not None and r.slots.get("kind") != "goal"
    ]
    m = len(tested)
    if not m:
        return
    order = sorted(tested, key=lambda r: (Decimal(r.p_value or "1"), r.key()))
    prev = Decimal(1)
    for rank in range(m, 0, -1):
        r = order[rank - 1]
        val = min(prev, Decimal(r.p_value or "1") * m / rank)
        prev = val
        r.q_value = str(min(val, Decimal(1)).quantize(Decimal("1e-12")))
    for r in tested:
        r.params = {**r.params, "fdr_q": str(q), "tests": m}
        if pool != INSIDE:  # provenance names a plug-in's pool; inside results keep their bytes
            r.params["pool"] = pool


GOAL_AT_RISK = "goal at risk"


def _gate(r: Result, floor: Decimal | None, ctx: Context, q: Decimal) -> tuple[bool, str | None]:
    """`floor` None: the result's pool has no money (no materiality gate)."""
    if not r.eligible:
        return False, r.reason or "not eligible"
    if r.p_value is None or Decimal(r.p_value) >= RAW_P:
        return False, r.reason or "no signal"
    if floor is not None and (r.money is None or Decimal(r.money) < floor):
        return False, "below materiality"
    if r.slots.get("kind") == "goal":
        # where the year will land, not an anomaly (decided 5 Nov 2026): tested and recorded,
        # never a Sweep finding; the brief's goal line and Home's at-risk proposal read it
        return False, GOAL_AT_RISK
    if r.q_value is None or Decimal(r.q_value) > q:
        return False, f"not significant after FDR (q {r.q_value})"
    pattern = suppressed_by(ctx, r)
    if pattern:
        return False, f"suppressed by {pattern}"
    held = held_by(ctx, r)
    if held:
        return False, held
    return True, None


def held_by(ctx: Context, r: Result) -> str | None:
    """The owner's snooze (this finding, until a date) or mute (its pattern, any direction),
    read from the run's inputs: the engine applies them from the next run (M6b)."""
    from othor.engine.findings import fingerprint, pattern

    holds = ctx.holds or {}
    fp = fingerprint(r)
    for s in holds.get("snoozes", []):
        if s["fingerprint"] == fp and ctx.run_date <= dt.date.fromisoformat(s["until"]):
            return f"snoozed until {s['until']}"
    pat = pattern(r)
    if any(m["pattern"] == pat for m in holds.get("mutes", [])):
        return "muted"
    return None


ALLOCATED_SHARE = Decimal("0.05")


def _allocation_cap(ctx: Context, r: Result) -> None:
    """A finding on a value with more than 5% of it allocated by rule (credit notes and rebates
    spread to plants) is Assessed at most; its provenance shows the allocated amount."""
    allocs = ctx.spec.get("allocations", [])
    if not allocs or not r.breakdown_value:
        return
    allocated = [a["metric"] for a in allocs if a["breakdown"] == r.breakdown]
    if not allocated or not _depends_on(ctx, r.metric, set(allocated)):
        return
    period = r.window[1].replace(day=1)
    amount = Decimal(0)
    for m in allocated:
        s = ctx.series.get(m, "month", r.breakdown, r.breakdown_value)
        if s is not None:
            amount += sum(
                (
                    Decimal(str(round(v, 6)))
                    for p, v in zip(s.periods, s.values, strict=True)
                    if p == period
                ),
                Decimal(0),
            )
    base_metric = next(
        (k for k in ("net_revenue",) if ctx.series.get(k, "month", r.breakdown, r.breakdown_value)),
        None,
    )
    base = Decimal(0)
    if base_metric:
        s = ctx.series.get(base_metric, "month", r.breakdown, r.breakdown_value)
        if s is not None:
            base = sum(
                (
                    Decimal(str(round(v, 6)))
                    for p, v in zip(s.periods, s.values, strict=True)
                    if p == period
                ),
                Decimal(0),
            )
    share = abs(amount) / abs(base) if base else Decimal(0)
    r.slots = {
        **r.slots,
        "allocated": str(amount.quantize(Decimal("0.01"))),
        "allocated_share": str(share.quantize(Decimal("0.0001"))),
    }
    if share > ALLOCATED_SHARE:
        r.slots["capped"] = "allocation over 5%"


def _depends_on(ctx: Context, metric: str, targets: set[str], seen: set[str] | None = None) -> bool:
    seen = seen or set()
    if metric in targets:
        return True
    if metric in seen:
        return False
    seen.add(metric)
    return any(
        _depends_on(ctx, c, targets, seen) for c in ctx.metric(metric).get("components") or []
    )


def state(r: Result) -> tuple[str, str | None]:
    """(state, confidence). Measured facts; Assessed High when q < 0.001 and strong, else
    Medium; Needs confirmation for cross-source; Not determinable when reasons stay open."""
    hint = r.state_hint
    if hint == "needs_confirmation":
        return "needs_confirmation", None
    if hint == "not_determinable":
        open_ = [x for x in r.reasons if not x.get("ruled_out")]
        if open_:
            return "not_determinable", None
        hint = "assessed"
    if hint == "measured" and not r.reconstructed_only and not r.slots.get("capped"):
        return "measured", None
    high = r.strong and r.q_value is not None and Decimal(r.q_value) < HIGH_Q
    return "assessed", "high" if high else "medium"


# --- suppression by known patterns (P14) ------------------------------------------------------


def windows(pattern: dict[str, Any], years: range) -> list[tuple[dt.date, dt.date]]:
    w = pattern.get("window") or {}
    kind = w.get("kind")
    out: list[tuple[dt.date, dt.date]] = []
    if kind == "range" and w.get("start") and w.get("end"):
        out.append((dt.date.fromisoformat(w["start"]), dt.date.fromisoformat(w["end"])))
    elif kind == "annual" and w.get("start") and w.get("end"):
        sm, sd = (int(x) for x in w["start"].split("-"))
        em, ed = (int(x) for x in w["end"].split("-"))
        out += [(dt.date(y, sm, sd), dt.date(y, em, ed)) for y in years]
    elif kind == "week_of":
        for d in w.get("dates") or []:
            day = dt.date.fromisoformat(str(d))
            mon = day - dt.timedelta(days=day.weekday())
            out.append((mon, mon + dt.timedelta(days=6)))
    elif kind == "quarter_end_days":
        n = int(w.get("days") or 5)
        for y in years:
            for m in (3, 6, 9, 12):
                end = (dt.date(y, m, 28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(
                    days=1
                )
                out.append((end - dt.timedelta(days=n - 1), end))
    return out


# a window pattern explains a level or a step in a series; it doesn't explain two systems
# disagreeing (a spike shows in both), or an entity's silence (a pause: Missing records)
NOT_BY_WINDOW = ("cross_source", "missing_records")


def suppressed_by(ctx: Context, r: Result) -> str | None:
    """The known pattern that covers the result, for its metric, entity or plant (docs/04 §4).

    A pattern covers its window and its settle-by period after it (`settle_days`: the catch-up
    after a shutdown). A tested period (Forecast breach) is covered when it overlaps either; for
    other detectors, the change's start (or a changepoint's episode) must fall inside. Only a
    confirmed pattern covers anything. The result's slots say which part covered it."""
    if r.detector in NOT_BY_WINDOW or r.slots.get("kind") == "goal":
        return None  # a goal's shortfall is the year's, never one window's
    start = r.window[0]
    end = r.window[1]
    if r.detector == "changepoint" and r.slots.get("episode"):
        start, end = (
            dt.date.fromisoformat(r.slots["episode"][0]),
            dt.date.fromisoformat(r.slots["episode"][1]),
        )
    years = range(start.year - 1, end.year + 1)
    for p in ctx.spec.get("suppressions", []):
        if not p.get("confirmed", True) or not pattern_applies(
            ctx, p, r.metric, r.breakdown_value, r.entities
        ):
            continue
        settle = dt.timedelta(days=int(p.get("settle_days") or 0))
        for a, b in windows(p, years):
            if r.detector == "forecast_breach":
                hit = start <= b + settle and a <= end
                if hit and r.slots.get("grain") not in ("day", "week"):
                    # a longer period is covered only when the window explains it (5 Nov 2026)
                    hit = _explained(ctx, r, a, b + settle)
                    if not hit:
                        r.slots = {**r.slots, "partly": str(p.get("name") or p.get("key"))}
            else:
                hit = a <= start <= b + settle or (
                    a <= end <= b and r.detector == "changepoint" and bool(r.slots.get("episode"))
                )
            if hit:
                r.slots = {
                    **r.slots,
                    "covered": {
                        "pattern": str(p.get("key") or ""),
                        "name": str(p.get("name") or p.get("key")),
                        "short": p.get("short"),
                        "settle": start > b or (end - b).days > (b - start).days,  # mostly after
                        "answered_on": str(p["answered_on"]) if p.get("answered_on") else None,
                    },
                }
                return str(p.get("name") or p.get("key"))
    return None


def _explained(ctx: Context, r: Result, a: dt.date, b: dt.date) -> bool:
    """A month (or quarter, or year) overlapping a short window is covered only when the
    window explains it: take out the window weeks' excess over their own stored forecasts; if
    the period is then inside its band, it's covered."""
    sl = r.slots
    if sl.get("lower") is None or sl.get("upper") is None:
        return False
    period_start, period_end = r.window
    excess = Decimal(0)
    found = False
    for w in (ctx.forecasts or {}).get("of_record", []):
        if w["metric"] != r.metric or w["grain"] != "week":
            continue
        ws = dt.date.fromisoformat(w["period"])
        we = ws + dt.timedelta(days=6)
        if not (
            ws <= b and a <= we and period_start <= ws and we <= period_end + dt.timedelta(days=6)
        ):
            continue
        s = ctx.series.get(r.metric, "week")
        actual = None
        if s is not None:
            actual = next(
                (v for p, v in zip(s.periods, s.values, strict=True) if p == ws and v == v), None
            )
        if actual is None:
            continue
        excess += Decimal(str(round(float(actual), 6))) - Decimal(w["point"])
        found = True
    if not found:
        return False
    adjusted = Decimal(sl["actual"]) - excess
    return Decimal(sl["lower"]) <= adjusted <= Decimal(sl["upper"])


def pattern_applies(
    ctx: Context, p: dict[str, Any], metric: str, value: str | None, entities: list[str]
) -> bool:
    """Does a known pattern cover this metric and breakdown value? Patterns name a plant or an
    entity by key (surat, skarfab) or by name (Surat, Skarfab); matching ignores case."""
    metrics = p.get("metrics") or []
    if metrics and metric not in metrics:
        return False
    for kind in ("plant", "entity"):
        want = p.get(kind)
        if not want:
            continue
        if kind == "plant" and value is None and not entities:
            # a plant's pattern also explains the company total that holds it: a metric broken
            # down by plant (metres, revenue); never one that isn't (receivables) (M7)
            if "plant" in (ctx.metric(metric).get("breakdowns") or []):
                continue
            return False
        names = {str(want).lower(), str(want).replace("_", " ").lower()}
        got = {(value or "").lower(), *(e.lower() for e in entities)}
        if not names & got:
            return False
    return True


# --- one finding per detector, family and window --------------------------------------------


SAME_WINDOW_DAYS = 42  # changes within six weeks of each other are one change
# across detectors, on one metric and window, the most specific explanation keeps the finding
SPECIFIC = (
    "cross_source",
    "net_hiding_gross",
    "missing_records",
    "concentration",
    "broken_relationship",
    "lead_lag",
    "process_artefact",
    "bimodal_average",
    "rising_variance",
    "changepoint",
    "forecast_breach",  # least specific: "outside the expected range" explains least (M7)
)


def _dedupe(ctx: Context, results: list[Result]) -> None:
    """One finding per detector and family for one change: the total (or the headline metric)
    keeps it, and the breakdown values and sibling metrics that moved with it are listed in it."""
    passed = sorted((r for r in results if r.passed), key=lambda r: _rank(ctx, r))
    kept: list[Result] = []
    for r in passed:
        home = next(
            (
                k
                for k in kept
                if k.detector == r.detector
                and k.family == r.family
                and (k.breakdown == "" or k.breakdown_value == r.breakdown_value)
                and abs((k.window[0] - r.window[0]).days) <= SAME_WINDOW_DAYS
            ),
            None,
        )
        if home is None:
            kept.append(r)
            continue
        r.passed, r.reason = False, f"also seen as {home.metric} ({home.detector})"
        where = r.breakdown_value or r.metric
        home.slots = {**home.slots, "also_in": sorted({*home.slots.get("also_in", []), where})}
    # one finding per metric and window across detectors
    by_specific = sorted(
        kept,
        key=lambda r: (SPECIFIC.index(r.detector) if r.detector in SPECIFIC else 99, _rank(ctx, r)),
    )
    final: list[Result] = []
    for r in by_specific:
        home = next(
            (
                k
                for k in final
                if k.metric == r.metric
                and k.breakdown_value == r.breakdown_value
                and k.window[0] <= r.window[1]
                and r.window[0] <= k.window[1]
            ),
            None,
        )
        if home is None:
            final.append(r)
            continue
        r.passed, r.reason = False, f"also seen as {home.metric} ({home.detector})"
        home.slots = {**home.slots, "also_by": sorted({*home.slots.get("also_by", []), r.detector})}


def _rank(ctx: Context, r: Result) -> tuple[Any, ...]:
    m = ctx.metric(r.metric)
    return (
        r.breakdown != "" and r.breakdown_value is not None,  # the total first
        not m.get("headline"),
        m.get("listed") is False,
        -(Decimal(r.money) if r.money else Decimal(0)),
        r.metric,
    )
