# SPDX-License-Identifier: Apache-2.0
"""What's ahead (v0.19, M19.4): one row per headline number in every brief, built by code from
the forecast step's v2 results (`forecasts.json`: v2, goals_v2, parts, known_ahead, changes,
track, abstain). Pure: no model; replayed with the brief.

Plain words on the surface (owner's focus from v0.2): a sentence, a small chart, how sure
(Reliable · Fair · Rough), or a plain reason to say nothing. The technical detail (method, the
80% range's ends, the calibration, the scores) sits behind the (i), in `depth`.

- A number with a goal looks to its goal (a fiscal-year total: "FY27"); otherwise its own
  grain's next period (owner, 15 Nov 2026: next month for a monthly number).
- Every figure in a sentence is a computed value, formatted by `values` (the one formatter).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from othor.briefs import values
from othor.core import fmt
from othor.engine.store import Point

WORDS = {"high": "Reliable", "medium": "Fair", "low": "Rough"}

METHOD_WORDS = {
    "ETS(A,N,N)": "follows the recent level",
    "ETS(A,A,N)": "follows the trend",
    "ETS(A,Ad,N)": "follows the trend, easing off",
    "ETS(A,N,A)": "follows the season",
    "ETS(A,Ad,A)": "follows the season and the trend",
    "naive": "repeats the last period",
    "seasonal-naive": "repeats the same period last year",
}

ABSTAIN = {
    "short_history": "Not enough history yet to forecast it",
    "source_stale": "Waiting for {sources} to update",
    "too_wide": "Too uncertain to call right now: its range is too wide",
    "definition_changed": "Its definition changed recently: forecasts return in a few weeks",
    "no_monthly_forecast": "Forecast weekly only: no forecast reaches its goal date yet",
}


def _name(spec: dict[str, Any], metric: str) -> str:
    return str((spec.get("metrics", {}).get(metric) or {}).get("name") or metric)


def _unit(spec: dict[str, Any], metric: str) -> str | None:
    return (spec.get("metrics", {}).get(metric) or {}).get("unit")


LAKH = Decimal(100000)


def _metres(d: Decimal) -> str:
    """Metres whole, as the tile writes them (0.28.4: '1.75 L m' beside the tile's '1,75,436 m')."""
    return f"{fmt.num(d)} m"


def _one(unit: str | None, v: Any) -> str:
    """One figure, as precise as it needs to be: lakh under ₹1 Cr, metres whole."""
    d = Decimal(str(v))
    if unit == "inr" and abs(d) < Decimal("1e7"):
        return values.text(unit, d)
    if unit == "metres":
        return _metres(d)
    if unit == "count":  # customers, orders: whole numbers (owner, 15 Nov 2026)
        return fmt.num(d.quantize(Decimal(1)))
    return values.rounded(unit, d)


def _band(unit: str | None, lo: Any, hi: Any) -> str:
    a, b = Decimal(str(lo)), Decimal(str(hi))
    if unit == "inr" and max(abs(a), abs(b)) < Decimal("1e7"):
        return values.text_band(unit, a, b)
    if unit == "count":  # customers, orders: whole numbers, "106–122"
        return f"{fmt.num(a.quantize(Decimal(1)))}–{fmt.num(b.quantize(Decimal(1)))}"
    if unit == "metres":
        return f"{fmt.num(a)}–{fmt.num(b)} m"
    return values.band(unit, a, b)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _fy(by: dt.date) -> str:
    return f"FY{by.year % 100:02d}"


def _period_word(grain: str, period: dt.date, today: dt.date | None = None) -> str:
    if grain == "month":
        return f"in {period:%b}"
    if grain == "week":
        if today is not None and period <= today < period + dt.timedelta(weeks=1):
            return "this week"
        return f"in the week of {fmt.day_month(period)}"
    return f"on {fmt.day_month(period)}"


def _rows_v2(doc: dict[str, Any], series: str) -> list[dict[str, Any]]:
    return sorted(
        (r for r in doc.get("rows", []) if r["series"] == series and not r.get("reconstructed")),
        key=lambda r: r["period"],
    )


def _sources(spec: dict[str, Any], metric: str) -> str:
    names = (spec.get("metrics", {}).get(metric) or {}).get("sources") or []
    return " and ".join(str(n) for n in names) or "its source"


def _goal_for(doc: dict[str, Any], metric: str, keys: list[str] | None) -> dict[str, Any] | None:
    for g in doc.get("goals_v2") or []:
        if g.get("metric") == metric and (not keys or g.get("key") in keys):
            return dict(g)
    return None


def _chance(spec: dict[str, Any], g: dict[str, Any]) -> str | None:
    p = g.get("probability")
    if p is None:
        return None
    unit = _unit(spec, g["metric"])
    target = values.rounded(unit, g["target"])
    if g.get("direction", "at_least") == "at_least":
        return (
            f"{p}% likely to reach the {target} goal"
            if p >= 50
            else f"{100 - p}% likely to miss the {target} goal"
        )
    return (
        f"{p}% likely to stay under {target}"
        if p >= 50
        else f"{100 - p}% likely to go over {target}"
    )


def _driver(doc: dict[str, Any], series: str) -> str | None:
    b = ((doc.get("parts") or {}).get(series) or {}).get("biggest_mover")
    if not b:
        return None
    return f"mostly {'lower' if b['part_goes'] == 'down' else 'higher'} {b['name'].lower()}"


def _general(spec: dict[str, Any]) -> set[str]:
    """Known patterns that list no numbers: they touch every row, so they're named once under the
    section, not on each row (owner, 15 Nov 2026)."""
    return {str(p.get("key")) for p in spec.get("suppressions", []) if not p.get("metrics")}


def _known_text(pat: dict[str, Any]) -> str:
    name = str(pat.get("name") or pat["key"])
    return f"includes {name}" if pat.get("effect") else f"{name} (its effect not yet measured)"


def _known(spec: dict[str, Any], doc: dict[str, Any], series: str, periods: list[str]) -> list[str]:
    seen, out, general = set(), [], _general(spec)
    for p in periods:
        for pat in ((doc.get("known_ahead") or {}).get(series) or {}).get(p, []):
            if pat["key"] in seen or str(pat["key"]) in general:
                continue
            seen.add(pat["key"])
            text = _known_text(pat)
            out.append(text if pat.get("effect") else f"known ahead: {text}")
    return out


def note(spec: dict[str, Any], doc: dict[str, Any], shown: list[dict[str, Any]]) -> str | None:
    """The line under the section: the known patterns that list no numbers and touch a shown
    row's forecast, named once."""
    general, seen, names = _general(spec), set(), []
    for r in shown:
        if not r.get("says"):
            continue
        series = f"{r['metric']}|{r['grain']}"
        for pats in ((doc.get("known_ahead") or {}).get(series) or {}).values():
            for pat in pats:
                k = str(pat["key"])
                if k in general and k not in seen:
                    seen.add(k)
                    names.append(_known_text(pat))
    return "Known ahead for the whole business: " + "; ".join(names) + "." if names else None


def _card(
    spec: dict[str, Any],
    unit: str | None,
    label: str,
    mid: Any,
    lo: Any,
    hi: Any,
    target: Any,
    verdict: str | None,
    goal: dict[str, Any] | None,
) -> dict[str, Any]:
    """The forecast as a card (Big Picture, M19.5): the expected value, its likely range and the
    goal, as computed text, and as positions for the range bar (the chart's units)."""

    def pos(x: Any) -> str:
        return str(Decimal(repr(values.chart_value(unit, x))).quantize(Decimal("0.0001")))

    bad = bool(
        goal
        and goal.get("probability") is not None
        and verdict
        and ("miss" in verdict or "go over" in verdict)
    )
    return {
        "label": _cap(label),
        "expected": _one(unit, mid),
        "low": _one(unit, lo),
        "high": _one(unit, hi),
        "target": values.rounded(unit, target) if target is not None else None,
        "at": {
            "expected": pos(mid),
            "low": pos(lo),
            "high": pos(hi),
            **({"target": pos(target)} if target is not None else {}),
        },
        "verdict": _cap(verdict) if verdict else None,
        "tone": "negative" if bad else "neutral",
    }


def _confidence(t: dict[str, Any] | None) -> dict[str, Any] | None:
    if not t:
        return None
    if t["source"] == "real":
        why = f"inside its range {t['inside']} of the last {t['n']} times"
    else:
        why = f"tested on its last {t['n']} periods: inside its range {t['inside']} times"
    return {"word": WORDS[t["level"]], "level": t["level"], "why": why}


def _depth(spec: dict[str, Any], doc: dict[str, Any], series: str, metric: str) -> dict[str, Any]:
    """Behind the (i): plain first, the technical term beside it."""
    unit = _unit(spec, metric)
    sel = (doc.get("selection") or {}).get(series) or {}
    out: dict[str, Any] = {}
    if sel:
        how = METHOD_WORDS.get(sel["method"], sel["method"])
        base = METHOD_WORDS.get(sel["baseline"], sel["baseline"])
        mae, bmae = Decimal(str(sel["mae"])), Decimal(str(sel["baseline_mae"]))
        if sel["method"] == sel["baseline"]:
            out["method"] = f"It {how}: nothing tested beat that on this number's own past."
        else:
            better = int(((1 - mae / bmae) * 100).quantize(Decimal(1))) if bmae else 0
            out["method"] = (
                f"It {how}, {better}% more accurate on this number's own past than one that {base}."
            )
        cal = sel.get("calibration") or {}
        k = Decimal(str(cal.get("k") or "1"))
        out["range"] = (
            f"The range is widened {fmt.num(k, decimals=1)}× so most past values would have "
            "fallen inside it."
            if k > 1
            else "The range held most past values without widening."
        )
        out["technical"] = (
            f"{sel['method']} · 80% range (P10–P90) · calibration ×{fmt.num(k, decimals=1)} · "
            f"tested on {sel['periods']} periods"
        )
    last = ((doc.get("track") or {}).get(series) or {}).get("last")
    if last:
        p = dt.date.fromisoformat(last["period"])
        out["last"] = (
            f"{_cap(_period_word(series.split('|')[1], p))}: expected "
            f"{_band(unit, last['p10'], last['p90'])}, came in at "
            f"{_one(unit, last['actual'])} "
            f"({'inside' if last['inside'] else 'outside'} its range)."
        )
    parts = ((doc.get("parts") or {}).get(series) or {}).get("parts") or []
    if parts:
        out["parts"] = [f"{r['name']}: {_one(unit, r['p50'])} expected" for r in parts]
    ch = (doc.get("changes") or {}).get(series)
    if ch:
        why = {
            "new_actuals": "new actuals came in",
            "known_pattern_changed": "a known pattern changed",
            "goal_changed": "a goal changed",
            "method_changed": "a better method was chosen",
            "definition_changed": "its definition changed",
        }
        out["changed"] = (
            f"Since the last brief: {_one(unit, ch['from'])} → "
            f"{_one(unit, ch['to'])}, because " + ", ".join(why.get(w, w) for w in ch["why"]) + "."
        )
    return out


def _chart(
    spec: dict[str, Any],
    metric: str,
    grain: str,
    rows: list[dict[str, Any]],
    points: list[Point],
    target: str | None,
) -> str | None:
    from othor.briefs.outlook import _complete
    from othor.charts.spec import Annotation, Band, ChartSpec, Period, Series, Unit
    from othor.engine.finding_charts import dump

    unit = _unit(spec, metric)
    actual = _complete(points, metric, grain)[-12:]
    ahead = [r for r in rows if (r.get("v2") or {}).get("p50") is not None]
    if not actual or not ahead:
        return None

    lakh = (
        unit == "metres"
        and max(
            [abs(Decimal(repr(x))) for _, x in actual]
            + [abs(Decimal(str(r["v2"].get("p90") or r["v2"]["p50"]))) for r in ahead]
        )
        >= LAKH
    )

    def v(x: Any) -> float:
        if lakh:
            return float(Decimal(str(x)) / LAKH)
        return values.chart_value(unit, x)

    last_p, last_v = actual[-1]
    joint = (last_p.isoformat(), v(Decimal(repr(last_v))))
    fc = [joint, *((r["period"], v(r["v2"]["p50"])) for r in ahead)]
    banded = all(r["v2"].get("p10") is not None for r in ahead)
    band = (
        Band(
            "forecast",
            0.8,
            (joint, *((r["period"], v(r["v2"]["p10"])) for r in ahead)),
            (joint, *((r["period"], v(r["v2"]["p90"])) for r in ahead)),
            role="positive",
        )
        if banded
        else None
    )
    marks = (
        (Annotation("threshold", value=v(target), label=f"goal {values.rounded(unit, target)}"),)
        if target is not None
        else ()
    )
    name = _name(spec, metric)
    end = str(ahead[-1]["period"])
    alt = (
        f"{name}: actuals to {fmt.day_month(last_p)}, then the forecast to "
        f"{fmt.day_month(dt.date.fromisoformat(end))}"
        + (" with its likely range" if banded else "")
        + (f", against the {marks[0].label}." if marks else ".")
    )
    spec_ = ChartSpec(
        id=f"ahead:{metric}:{grain}:{end}",
        kind="line",
        title=f"{name} · what's ahead",
        alt_text=alt,
        unit=Unit("metres", scale="l", decimals=1) if lakh else values.chart_unit(unit),
        period=Period(grain, actual[0][0].isoformat(), end),  # type: ignore[arg-type]
        series=(
            Series(
                "actual",
                "Actual",
                "neutral",
                tuple((p.isoformat(), v(Decimal(repr(x)))) for p, x in actual),
            ),
            Series("forecast", "Forecast", "muted", tuple(fc), style="dotted"),
        ),
        band=band,
        annotations=marks,
        provenance={"metric_id": metric, "source": "forecast v2"},
    )
    return dump(spec_)


def rows(
    spec: dict[str, Any],
    version: dict[str, Any],
    doc: dict[str, Any],
    points: list[Point],
    today: dt.date | None = None,
) -> list[dict[str, Any]]:
    """One row per headline number (the brief's own, max 6), in the brief's order."""
    out = []
    for metric in (version.get("headline") or [])[:6]:
        m = spec.get("metrics", {}).get(metric) or {}
        if m.get("status") not in (None, "active"):
            continue
        name, unit = _name(spec, metric), _unit(spec, metric)
        goal = _goal_for(doc, metric, version.get("goals"))
        if goal is not None and goal.get("why") == "no_monthly_forecast":
            goal = None  # forecast weekly only: show the next forecast, not the far goal
        # a monthly series when the number has a goal or is monthly by nature (owner: next month
        # for a monthly number with no goal); else its weekly series
        has_month = any(r["series"] == f"{metric}|month" for r in doc.get("rows", []))
        grain = "month" if has_month and (goal is not None or m.get("grain") == "month") else "week"
        if not any(r["series"] == f"{metric}|{grain}" for r in doc.get("rows", [])) and has_month:
            grain = "month"
        series = f"{metric}|{grain}"
        mine = _rows_v2(doc, series)
        why = (doc.get("abstain") or {}).get(series)
        if goal is not None and goal.get("probability") is None and goal.get("why"):
            why = why or goal["why"]
        row: dict[str, Any] = {"metric": metric, "name": name, "grain": grain}
        if why or not mine or not (mine[0].get("v2") or {}).get("p50"):
            row["says"] = None
            row["why"] = ABSTAIN.get(why or "short_history", ABSTAIN["short_history"]).format(
                sources=_sources(spec, metric)
            )
            out.append(row)
            continue
        if goal is not None and goal.get("probability") is not None:
            by = dt.date.fromisoformat(str(goal["by"]))
            when = (
                f"for {_fy(by)}" if "to_date" in goal and goal.get("to_date") else f"by {by:%b %Y}"
            )
            lead = f"{_one(unit, goal['projection'])} expected {when}"
            rng = _band(unit, goal["p10"], goal["p90"])
            bits = [f"{lead} ({rng})", _chance(spec, goal)]
            row["card"] = _card(
                spec,
                unit,
                f"{name} · {when.removeprefix('for ')}",
                goal["projection"],
                goal["p10"],
                goal["p90"],
                goal["target"],
                _chance(spec, goal),
                goal,
            )
            chart_rows = [r for r in mine if r["period"] <= str(goal["by"])]
            target = goal["target"] if not goal.get("to_date") else None
        else:
            first = mine[0]
            p = dt.date.fromisoformat(first["period"])
            v2 = first["v2"]
            bits = [
                f"{_band(unit, v2['p10'], v2['p90'])} expected {_period_word(grain, p, today)}"
                if v2.get("p10") is not None
                else f"about {_one(unit, v2['p50'])} expected {_period_word(grain, p, today)}"
            ]
            chart_rows = [first]
            target = None
            if v2.get("p10") is not None:
                row["card"] = _card(
                    spec,
                    unit,
                    f"{name} · {_period_word(grain, p, today)}",
                    v2["p50"],
                    v2["p10"],
                    v2["p90"],
                    None,
                    None,
                    None,
                )
        bits.append(_driver(doc, series))
        bits += _known(spec, doc, series, [r["period"] for r in chart_rows])
        row["says"] = " · ".join(b for b in bits if b)
        row["confidence"] = _confidence((doc.get("track") or {}).get(series))
        row["chart"] = _chart(spec, metric, grain, chart_rows, points, target)
        row["depth"] = _depth(spec, doc, series, metric)
        out.append(row)
    return out


def range_chart(c: dict[str, Any]) -> Any:
    """A forecast card's range bar, as a ChartSpec (code, from the computed card): Big Picture's,
    and What's ahead's from 16 Nov 2026 (page, email, PDF)."""
    from othor.charts.spec import Annotation, Category, ChartSpec

    at = c["at"]
    goal = (
        (Annotation("threshold", value=float(at["target"]), label=f"Goal {c['target']}"),)
        if c.get("target") and "target" in at
        else ()
    )
    alt = f"{c['label']}: {c['expected']} expected, most likely {c['low']} to {c['high']}" + (
        f", against the goal of {c['target']}." if goal else "."
    )
    return ChartSpec(
        id=f"range:{c['label']}",
        kind="range_bar",
        title=c["label"],
        alt_text=alt,
        categories=(
            Category("low", float(at["low"]), "positive", label=c["low"]),
            Category("high", float(at["high"]), "positive", label=c["high"]),
            Category("expected", float(at["expected"]), "neutral", label=c["expected"]),
        ),
        annotations=goal,
    )
