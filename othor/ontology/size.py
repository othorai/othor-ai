# SPDX-License-Identifier: Apache-2.0
"""The company's size, computed (#76, docs/plans/next-1126.md §3, step 1).

Net revenue over the last 12 complete months, from the run's own series (the headline revenue
number; none → no band), converted to ₹ crore at the rate the spec records when the company
reports in another currency (a rate fixed per release, `catalogue/numbers.yaml`), and the band
it falls in. With it, the one figure behind each of the band's numbers that can be built from the
company's numbers (step 5): the largest 10 customers' share, gross margin %, … Everything here is
Decimal over the stored points, so the night and its replay write the same `size` into
`outputs.json` (rule 2). No model, no rows: only the series the run already stored.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from othor.ontology import catalogue as cat_mod
from othor.ontology.catalogue import REVENUE, Catalogue, Entry

CRORE = Decimal("1e7")
MONTHS = 12
MIN_MONTHS = 6  # fewer complete months than this: no band yet
OTHER = "Other"
Q = Decimal("0.0001")


def spec_rate(currency: str, cat: Catalogue | None = None) -> dict[str, str] | None:
    """What the compiler writes into a spec for a company that reports in another currency than
    ₹: the rate fixed for this release. None for ₹ (nothing to convert: its spec stays as it
    was) and for a currency the catalogue has no rate for."""
    if currency == "INR":
        return None
    c = cat or cat_mod.load()
    rate = c.rates.get(currency)
    if rate is None:
        return None
    return {"currency": currency, "inr_rate": str(rate), "rates_as_of": c.rates_as_of}


def _active(spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        k: m
        for k, m in (spec.get("metrics") or {}).items()
        if m.get("status", "active") == "active"
    }


def revenue_metric(spec: Mapping[str, Any]) -> str | None:
    """The headline revenue number: an active ₹ flow named revenue or sales, not a variant of
    another number; a headline one first, then net, then by key (the same every time)."""
    out = []
    for k, m in _active(spec).items():
        name = str(m.get("name") or k).lower()
        fam = m.get("family")
        if (fam and fam != k) or m.get("unit", "inr") != "inr" or m.get("kind") == "snapshot":
            continue
        if not any(w in k or w in name for w in ("revenue", "sales")):
            continue
        out.append((0 if m.get("headline") else 1, 0 if "net" in k or "net" in name else 1, k))
    return min(out)[2] if out else None


def resolve(spec: Mapping[str, Any], cat: Catalogue) -> dict[str, str]:
    """Each part a build uses → the company's own active number for it (by key, then by name)."""
    metrics = _active(spec)
    out: dict[str, str] = {}
    rev = revenue_metric(spec)
    if rev:
        out[REVENUE] = rev
    for part, how in sorted(cat.parts.items()):
        hit = next((k for k in how["keys"] if k in metrics), None)
        if hit is None:
            hit = next(
                (
                    k
                    for k in sorted(metrics)
                    if str(metrics[k].get("name") or "").strip().lower() in how["names"]
                ),
                None,
            )
        if hit is not None:
            out[part] = hit
    return out


def _months(
    points: Iterable[Any], metric: str, breakdown: str = "", run_date: dt.date | None = None
) -> dict[tuple[str | None, dt.date], Decimal]:
    out: dict[tuple[str | None, dt.date], Decimal] = {}
    for p in points:
        if p.metric != metric or p.grain != "month" or p.breakdown != breakdown:
            continue
        if p.value is None or not getattr(p, "complete", True):
            continue
        if run_date is not None and p.period >= run_date:
            continue
        out[(p.breakdown_value, p.period)] = Decimal(p.value)
    return out


def _last(periods: Iterable[dt.date], n: int = MONTHS) -> list[dt.date]:
    return sorted(set(periods))[-n:]


def _div(a: Decimal, b: Decimal) -> Decimal | None:
    return None if b == 0 else (a / b).quantize(Q)


def of(
    spec: Mapping[str, Any],
    points: list[Any],
    run_date: dt.date,
    cat: Catalogue | None = None,
) -> dict[str, Any] | None:
    """The run's `size`: revenue over the last 12 complete months (annualised from 6 or more),
    the band, and each band number's computed figure. None without a revenue number or months."""
    c = cat or cat_mod.load()
    rev = revenue_metric(spec)
    if rev is None:
        return None
    totals = _months(points, rev, run_date=run_date)
    months = _last(p for _, p in totals)
    if len(months) < MIN_MONTHS:
        return None
    revenue = sum((totals[(None, p)] for p in months), Decimal(0))
    currency = str((spec.get("company") or {}).get("currency") or "INR")
    rate = Decimal(1)
    if currency != "INR":
        got = (spec.get("size") or {}).get("inr_rate")
        if got is None:
            return None  # no rate recorded for this currency: no band rather than a guess
        rate = Decimal(str(got))
    yearly = revenue * MONTHS / len(months)
    crore = (yearly * rate / CRORE).quantize(Decimal("0.01"))
    band = c.band_for(crore)
    parts = resolve(spec, c)
    figures: dict[str, Any] = {}
    for key in band.numbers:
        e = c.numbers[key]
        f = figure(e, parts, spec, points, run_date)
        if f is not None:
            figures[key] = f
    return {
        "metric": rev,
        "months": len(months),
        "from": months[0].isoformat(),
        "through": months[-1].isoformat(),
        "revenue": str(revenue.quantize(Decimal("0.01"))),
        "currency": currency,
        "inr_rate": str(rate),
        "crore": str(crore),
        "band": band.key,
        "catalogue": f"v{c.version}-{c.sha}",
        "parts": dict(sorted(parts.items())),
        "figures": figures,
    }


def figure(
    e: Entry,
    parts: Mapping[str, str],
    spec: Mapping[str, Any],
    points: list[Any],
    run_date: dt.date,
) -> dict[str, Any] | None:
    """The one computed figure behind a number built from the company's numbers, or None when a
    part is missing (or it's built from a table: it has no figure until it exists)."""
    if e.kind == "table" or any(p not in parts for p in e.parts()):
        return None
    metrics = spec.get("metrics") or {}
    b = e.build
    if e.kind == "top":
        return _top(parts[b["of"]], str(b["by"]), int(b["top"]), metrics, points, run_date)
    by = [
        x
        for x in e.by()
        if all(x in (metrics[parts[p]].get("breakdowns") or []) for p in e.parts())
    ]
    if e.by() and not by:
        return None
    snap = any(metrics[parts[p]].get("kind") == "snapshot" for p in e.parts())
    if by:
        return _ratio_by(e, parts, by[0], points, run_date)
    return _ratio(e, parts, points, run_date, latest=snap)


def _top(
    metric: str,
    by: str,
    n: int,
    metrics: Mapping[str, Any],
    points: list[Any],
    run_date: dt.date,
) -> dict[str, Any] | None:
    if by not in (metrics.get(metric, {}).get("breakdowns") or []):
        return None
    total = _months(points, metric, run_date=run_date)
    months = _last(p for _, p in total)
    if not months:
        return None
    parts = _months(points, metric, by, run_date=run_date)
    each: dict[str, Decimal] = defaultdict(Decimal)
    for (v, p), x in parts.items():
        if p in months and v is not None and v != OTHER:
            each[v] += x
    whole = sum((total[(None, p)] for p in months), Decimal(0))
    if whole <= 0 or not each:
        return None
    ranked = sorted(each.items(), key=lambda kv: (-kv[1], kv[0]))
    share = _div(sum((x for _, x in ranked[:n]), Decimal(0)), whole)
    if share is None:
        return None
    # the breakdown cap keeps the largest by name and sums the rest as "Other": then there are
    # more than the count, and the sentence says so
    more = any(v == OTHER and p in months and x > 0 for (v, p), x in parts.items())
    return {
        "value": str(share),
        "count": sum(1 for _, x in ranked if x > 0),
        **({"more": True} if more else {}),
        "months": len(months),
        "metric": metric,
    }


def _sums(
    e: Entry, parts: Mapping[str, str], points: list[Any], run_date: dt.date, bv: str = ""
) -> dict[str, dict[tuple[str | None, dt.date], Decimal]]:
    return {p: _months(points, parts[p], bv, run_date=run_date) for p in e.parts()}


def _value(e: Entry, got: Mapping[str, Decimal]) -> Decimal | None:
    b = e.build
    top = got[b["take"]] - (got[b["less"]] if b.get("less") else Decimal(0))
    if not b.get("over"):
        return top
    return _div(top, got[b["over"]])


def _ratio(
    e: Entry, parts: Mapping[str, str], points: list[Any], run_date: dt.date, *, latest: bool
) -> dict[str, Any] | None:
    series = _sums(e, parts, points, run_date)
    common = set.intersection(*({p for _, p in s} for s in series.values()))
    if not common:
        return None
    if latest:  # a level (active customers, cash) in it: the latest complete month
        month = max(common)
        v = _value(e, {k: s[(None, month)] for k, s in series.items()})
        if v is None:
            return None
        return {"value": str(v.quantize(Q)), "month": month.isoformat(), "months": 1}
    months = _last(common)
    got = {k: sum((s[(None, p)] for p in months), Decimal(0)) for k, s in series.items()}
    v = _value(e, got)
    if v is None:
        return None
    if not e.build.get("over"):  # a difference: on average a month
        v = (v / len(months)).quantize(Decimal("0.01"))
    return {"value": str(v), "months": len(months)}


def _ratio_by(
    e: Entry, parts: Mapping[str, str], by: str, points: list[Any], run_date: dt.date
) -> dict[str, Any] | None:
    series = _sums(e, parts, points, run_date, by)
    keys = set.intersection(*(set(s) for s in series.values()))
    months = _last(p for _, p in keys)
    if not months:
        return None
    names = sorted({v for v, p in keys if v is not None and v != OTHER and p in months})
    out = []
    for name in names:
        got = {
            k: sum((by_part.get((name, p), Decimal(0)) for p in months), Decimal(0))
            for k, by_part in series.items()
        }
        v = _value(e, got)
        if v is not None:
            out.append((v, name))
    if len(out) < 2:
        return None
    low, name = min(out)
    return {"value": str(low), "low": name, "count": len(out), "by": by, "months": len(months)}
