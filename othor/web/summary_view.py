# SPDX-License-Identifier: Apache-2.0
"""Summary (v0.16, M16.2): one page of every key number, by area, each against a comparison.

Pure: the same stored points, spec, layout and view give the same rows. Every figure is read from
the run's stored series (no source is read, no model is called); every chart is a ChartSpec.

- Rows are areas (the spec's `area`, M16.1; none is "Other numbers"), in the owner's order, then
  the short list's, then the owner's own names, Other numbers last. Hidden areas are counted.
- Each row shows its lead number: the owner's pick, else a headline number, else the area's
  first number in Ontology order.
- The value of the latest complete period, against the period before or the same period a year
  back, as % or absolute. 4 and 13 weeks use week points, 12 months month points.
- One breakdown value at a time ("region=South"); a number not split that way shows its total.
- A row expands into the lead number's signed parts (the formula's terms) and the area's other
  numbers.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from othor.briefs import values
from othor.briefs.tiles import NO_CHANGE, _when
from othor.briefs.tiles import grain as own_grain
from othor.charts.spec import ChartSpec, Period, Series, Unit, YAxis
from othor.core import fmt
from othor.engine.store import Point
from othor.ontology import areas

WINDOWS = {
    "7d": ("day", 7, "7 days"),
    "14d": ("day", 14, "14 days"),
    "4w": ("week", 4, "4 weeks"),
    "13w": ("week", 13, "1 Quarter"),  # 13 weeks (owner, 14 Nov 2026: named 1 Quarter)
    "12m": ("month", 12, "12 months"),
}
# periods of the metric's own grain when it has none at the window's grain
FALLBACK = {"7d": 2, "14d": 2, "4w": 2, "13w": 3, "12m": 12}
# owner, 14 Nov 2026: the charts show the period alone by default; the owner turns on a
# comparison, the period before (the default when turned on) or last year
COMPARES = {"none": "No comparison", "before": "Period before", "year": "Same time last year"}
# #65 (owner, 14 Nov 2026): Actual plots the values; % and ABS plot each period's change against
# the comparison (the period before, or last year), with a faint zero line
SHOWS = {"actual": "Actual", "pct": "%", "abs": "ABS"}
# owner, 14 Nov 2026: an extra view with every number as its own row, beside the area rows
ROWS = {"areas": "By area", "numbers": "Every number"}
SHOWN_FIRST = 8
GRAIN_WORD = {"day": "Daily", "week": "Weekly", "month": "Monthly"}

Series_ = list[tuple[dt.date, Decimal]]


@dataclass(frozen=True)
class View:
    window: str = "13w"
    compare: str = "none"
    show: str = "actual"
    split: str = ""  # "" or "breakdown=value"
    rows: str = "areas"  # areas: a row per area · numbers: a row per number, under area headings

    @classmethod
    def parse(
        cls,
        query: Mapping[str, Any] | None,
        saved: Mapping[str, Any] | None = None,
        splits: Mapping[str, list[str]] | None = None,
    ) -> View:
        """The URL wins, then the saved view, then the defaults; anything unknown falls back."""
        allowed = {"window": WINDOWS, "compare": COMPARES, "show": SHOWS, "rows": ROWS}
        got: dict[str, str] = {}
        for f in ("window", "compare", "show", "split", "rows"):
            for src in (query or {}, saved or {}):
                v = src.get(f)
                if v is None:
                    continue
                v = str(v)
                if f == "split":
                    if v == "" or _split_ok(v, splits):
                        got[f] = v
                        break
                elif v in allowed[f]:
                    got[f] = v
                    break
        return cls(**got)

    @property
    def basis(self) -> str:
        """What the change is measured against: last year when that is the comparison, else the
        period before (also when no comparison line is shown)."""
        return "year" if self.compare == "year" else "before"

    @property
    def pair(self) -> tuple[str, str] | None:
        if "=" not in self.split:
            return None
        k, v = self.split.split("=", 1)
        return k, v

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _split_ok(v: str, splits: Mapping[str, list[str]] | None) -> bool:
    if "=" not in v:
        return False
    k, val = v.split("=", 1)
    return splits is None or val in (splits.get(k) or [])


# --- the series -------------------------------------------------------------------------------


def series(
    points: Mapping[str, tuple[Point, ...]] | list[Point],
    metric: str,
    g: str,
    pair: tuple[str, str] | None = None,
) -> Series_:
    """Complete, non-null points of one metric at one grain, the total or one breakdown value."""
    pts = points.get(metric, ()) if isinstance(points, Mapping) else points
    b, v = pair if pair else ("", None)
    out = [
        (p.period, p.value)
        for p in pts
        if p.metric == metric
        and p.grain == g
        and p.breakdown == b
        and p.breakdown_value == v
        and p.complete
        and p.value is not None
    ]
    return sorted(out)  # type: ignore[arg-type]


def year_back(g: str, d: dt.date) -> dt.date:
    """The same period a year back: 52 weeks for weeks (Mondays stay Mondays), the same month,
    and for a day the same weekday 52 weeks back."""
    if g == "week":
        return d - dt.timedelta(weeks=52)
    if g == "month":
        return d.replace(year=d.year - 1)
    return d - dt.timedelta(days=364)  # a day: the same weekday a year back


def compared(s: Series_, g: str, compare: str, at: int = -1) -> Decimal | None:
    """The comparison for the point at index `at`: the period before, or a year back."""
    if not s:
        return None
    i = at % len(s)
    if compare == "before":
        return s[i - 1][1] if i >= 1 else None
    want = year_back(g, s[i][0])
    return next((v for p, v in s if p == want), None)


def delta(unit: str | None, value: Decimal, comp: Decimal | None, show: str) -> Decimal | None:
    """The change the column and the chart both show: a ratio under % (Actual too), points for a
    share under ABS, else the difference in the number's unit. None with nothing to compare."""
    if comp is None:
        return None
    d = value - comp
    if show != "abs":
        return d / abs(comp) if comp != 0 else None
    return d * 100 if unit in ("percent", "ratio") else d


def change(unit: str | None, value: Decimal, comp: Decimal | None, show: str) -> tuple[str, str]:
    """(text, direction up|down|flat|none) of the change, through the one formatter. Actual
    states the change in %, as % does."""
    if comp is None:
        return "", "none"
    delta = value - comp
    if show in ("pct", "actual") and comp != 0:
        r = delta / abs(comp)
        if abs(r) < NO_CHANGE:
            return "no change", "flat"
        return fmt.pct(r, decimals=1, sign=True), "up" if r > 0 else "down"
    if unit in ("percent", "ratio"):  # a share changes in points
        pts = delta * 100
        if abs(pts) < Decimal("0.05"):
            return "no change", "flat"
        return fmt.num(pts, decimals=1, sign=True) + " pts", "up" if pts > 0 else "down"
    if delta == 0:
        return "no change", "flat"
    sign = "+" if delta > 0 else fmt.MINUS
    return sign + values.text(unit, abs(delta)), "up" if delta > 0 else "down"


# --- areas and leads ---------------------------------------------------------------------------


def listed(spec: dict[str, Any]) -> list[str]:
    """The confirmed, listed numbers: headline numbers first, then Ontology order (as the
    Ontology page lists them)."""
    ms = spec.get("metrics") or {}
    head = set((spec.get("forecast") or {}).get("headline") or [])
    keys = [
        k
        for k in spec.get("order") or []
        if (ms.get(k) or {}).get("status", "active") == "active"
        and (ms.get(k) or {}).get("listed") is not False
    ]
    return sorted(keys, key=lambda k: not (k in head or (ms.get(k) or {}).get("headline")))


def by_area(spec: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k in listed(spec):
        out.setdefault(areas.label((spec["metrics"][k]).get("area")), []).append(k)
    return out


def area_order(found: list[str], saved: list[str] | None) -> list[str]:
    def rank(a: str) -> tuple[int, int, str]:
        if a == areas.OTHER:
            return (3, 0, a)
        if a in areas.SHORT:
            return (1, areas.SHORT.index(a), a)
        return (2, 0, a)

    first = [a for a in saved or [] if a in found]
    rest = sorted((a for a in found if a not in first), key=rank)
    if areas.OTHER in first:  # the owner put it somewhere: theirs
        return first + rest
    return (
        first
        + [a for a in rest if a != areas.OTHER]
        + ([areas.OTHER] if areas.OTHER in rest else [])
    )


def lead_of(spec: dict[str, Any], area: str, metrics: list[str], leads: Mapping[str, str]) -> str:
    pick = leads.get(area)
    if pick in metrics:
        return str(pick)
    head = set((spec.get("forecast") or {}).get("headline") or [])
    ms = spec.get("metrics") or {}
    for k in metrics:
        if k in head or ms.get(k, {}).get("headline"):
            return k
    return metrics[0]


# --- findings ----------------------------------------------------------------------------------


def covers(spec: dict[str, Any], metric: str) -> set[str]:
    """The metric and every metric it is computed from (its parts, recursively)."""
    ms = spec.get("metrics") or {}
    seen: set[str] = set()
    todo = [metric]
    while todo:
        k = todo.pop()
        if k in seen:
            continue
        seen.add(k)
        todo += list((ms.get(k) or {}).get("components") or [])
    return seen


def marker(
    spec: dict[str, Any],
    metric: str,
    findings: list[dict[str, Any]],
    pair: tuple[str, str] | None,
) -> dict[str, Any] | None:
    """Open findings on the number, its family or its parts (with a split: that value or the
    total only)."""
    mine = covers(spec, metric)
    fam = (spec.get("metrics", {}).get(metric) or {}).get("family")
    hit = []
    for f in findings:
        if f.get("status", "open") != "open":
            continue
        if not (f.get("metric") in mine or (fam and f.get("family") == fam)):
            continue
        if (
            pair
            and f.get("breakdown")
            and (f.get("breakdown") != pair[0] or f.get("breakdown_value") != pair[1])
        ):
            continue
        hit.append(f)
    if not hit:
        return None
    return {"count": len(hit), "fp": hit[0].get("fingerprint") or hit[0].get("fp")}


# --- a row -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Ctx:
    spec: dict[str, Any]
    points: Mapping[str, tuple[Point, ...]]
    view: View
    run_id: str = ""


def _grain(ctx: Ctx, metric: str) -> tuple[str, int, str]:
    """(grain, periods in the window, note) for a number in this view."""
    g, n, _ = WINDOWS[ctx.view.window]
    pair = _pair_for(ctx, metric)
    e = ctx.spec.get("metrics", {}).get(metric) or {}
    # a ratio of two numbers (a derived metric that isn't a sum) by the day divides one day's
    # costs by one day's metres: shown by the week in the daily windows instead
    ratio = e.get("type") == "derived" and not e.get("terms")
    if g == "day" and ratio and not e.get("per_period"):
        if series(ctx.points, metric, "week", pair):
            return "week", FALLBACK[ctx.view.window], "Weekly only"
    elif not e.get("per_period") and series(ctx.points, metric, g, pair):
        return g, n, ""
    own = own_grain(e)
    if own == g:
        return g, n, ""
    return own, FALLBACK[ctx.view.window], f"{GRAIN_WORD.get(own, own)} only"


def _pair_for(ctx: Ctx, metric: str) -> tuple[str, str] | None:
    pair = ctx.view.pair
    if pair and pair[0] in (
        (ctx.spec.get("metrics", {}).get(metric) or {}).get("breakdowns") or []
    ):
        return pair
    return None


def figure(ctx: Ctx, metric: str) -> dict[str, Any]:
    """One number in this view: value, comparison, change, grain and any note."""
    e = ctx.spec.get("metrics", {}).get(metric) or {}
    unit, name = e.get("unit"), str(e.get("name") or metric)
    g, n, note = _grain(ctx, metric)
    pair = _pair_for(ctx, metric)
    if ctx.view.pair and pair is None:
        bname = (ctx.spec.get("breakdowns", {}).get(ctx.view.pair[0]) or {}).get("name")
        note = f"Not split by {bname or ctx.view.pair[0].replace('_', ' ')}"
    s = series(ctx.points, metric, g, pair)
    base = {"metric": metric, "name": name, "unit": unit, "grain": g, "periods": n, "note": note}
    if not s:
        return {
            **base,
            "value": None,
            "value_text": "No data yet",
            "change_text": "",
            "direction": "none",
            "vs": "",
            "series": s,
            "pair": pair,
        }
    period, value = s[-1]
    comp = compared(s, g, ctx.view.basis)
    text, direction = change(unit, value, comp, ctx.view.show)
    if comp is None:
        vs = (
            "No data for last year"
            if ctx.view.basis == "year" and len(s) > 1
            else ("Building history" if len(s) == 1 else "")
        )
    else:
        vs = (
            f"vs {_when(g, s[-2][0])}"
            if ctx.view.basis == "before"
            else f"vs {year_back(g, period):%b %Y}"
            if g == "month"
            else f"vs {_when(g, year_back(g, period))} last year"
        )
    return {
        **base,
        "period": period,
        "period_label": _when(g, period),
        "value": value,
        "value_text": values.text(unit, value),
        "comparison": comp,
        "change_text": text,
        "direction": direction,
        "vs": vs,
        "series": s,
        "pair": pair,
    }


def charts(ctx: Ctx, fig: dict[str, Any]) -> tuple[ChartSpec | None, ChartSpec | None]:
    """The row's line (this window, and the comparison re-dated onto its dates, in grey) and its
    phone sparkline."""
    s: Series_ = fig["series"]
    if not s:
        return None, None
    g, n, unit, name, metric = fig["grain"], fig["periods"], fig["unit"], fig["name"], fig["metric"]
    if ctx.view.show in ("pct", "abs"):
        return change_charts(ctx, fig)
    now = s[-n:]
    start = len(s) - len(now)
    small = unit == "inr" and values.small_money([v for _p, v in s])
    comp: list[tuple[str, float]] = []
    for j, (p, _v) in enumerate(now):
        if ctx.view.compare == "none":
            break
        c = (
            compared(s, g, ctx.view.compare, at=start + j)
            if ctx.view.compare == "year"
            else (s[start + j - n][1] if start + j - n >= 0 else None)
        )
        if c is not None:
            comp.append((p.isoformat(), values.chart_value(unit, c, small)))
    pts = tuple((p.isoformat(), values.chart_value(unit, v, small)) for p, v in now)
    label = COMPARES[ctx.view.compare] if ctx.view.compare == "year" else "Period before"
    split = ctx.view.split if fig.get("pair") else ""
    prov = {
        "metric_id": metric,
        "run": ctx.run_id,
        "spec_version": str(ctx.spec.get("ontology_version", "")),
        "window": ctx.view.window,
        "compare": ctx.view.compare,
        "split": split,
    }
    last = values.text(unit, now[-1][1])
    alt = f"{name}, {len(now)} {g}s to {_when(g, now[-1][0])}: {last}" + (
        f"; the {label.lower()} in grey." if comp else "."
    )
    period = Period(g, now[0][0].isoformat(), now[-1][0].isoformat())  # type: ignore[arg-type]
    v = ctx.view
    cid = f"summary:{metric}:{g}:{now[-1][0].isoformat()}:{v.window}:{v.compare}:{split}"
    sers = [Series("now", WINDOWS[ctx.view.window][2], "positive", pts, style="smooth")]
    if comp:  # the period in theme blue (calm, 19 Nov 2026), the comparison dotted grey
        sers.append(Series("compare", label, "muted", tuple(comp), style="dotted"))
    line = ChartSpec(
        id=cid,
        kind="line",
        title=f"{name} · {WINDOWS[ctx.view.window][2]}",
        alt_text=alt,
        unit=values.chart_unit(unit, small),
        period=period,
        series=tuple(sers),
        provenance=prov,
    )
    spark = ChartSpec(
        id=cid + ":spark",
        kind="sparkline",
        title=f"{name} · {WINDOWS[ctx.view.window][2]}",
        alt_text=alt,
        unit=values.chart_unit(unit, small),
        period=period,
        # the row's long chart (owner, 14 Nov 2026): the period smoothed (theme blue from
        # 0.28.4), the comparison dotted grey behind it
        series=(Series("now", name, "positive", pts, style="smooth"),)
        + ((Series("compare", label, "muted", tuple(comp), style="dotted"),) if comp else ()),
        provenance=prov,
    )
    return line, spark


def change_charts(ctx: Ctx, fig: dict[str, Any]) -> tuple[ChartSpec | None, ChartSpec | None]:
    """% and ABS (#65): each period's change against the period before (Compare off or Period
    before) or the same period a year back, ink on a faint zero line, no comparison line. A
    period with nothing to compare against is left out."""
    s: Series_ = fig["series"]
    g, n, unit, name, metric = fig["grain"], fig["periods"], fig["unit"], fig["name"], fig["metric"]
    v = ctx.view
    start = max(len(s) - n, 0)
    raw: list[tuple[str, Decimal]] = []
    for i in range(start, len(s)):
        d = delta(unit, s[i][1], compared(s, g, v.basis, at=i), v.show)
        if d is not None:
            raw.append((s[i][0].isoformat(), d))
    plain = v.show == "pct" or unit in ("percent", "ratio")
    small = not plain and unit == "inr" and values.small_money([d for _p, d in raw])
    pts: list[tuple[str, float]] = [
        (p, float(d) if plain else values.chart_value(unit, d, small)) for p, d in raw
    ]
    if len(pts) < 2:  # a line needs two points
        return None, None
    if v.show == "pct":
        cu = Unit("percent", decimals=0)
    elif unit in ("percent", "ratio"):
        cu = Unit("count", decimals=1)  # a share's change, in points
    else:
        cu = values.chart_unit(unit, small)
    against = "the same period last year" if v.basis == "year" else f"the {g} before"
    last = fig.get("change_text") or ""
    alt = f"{name}, change on {against}, {len(pts)} {g}s to {_when(g, s[-1][0])}" + (
        f": {last}." if last else "."
    )
    split = v.split if fig.get("pair") else ""
    prov = {
        "metric_id": metric,
        "run": ctx.run_id,
        "spec_version": str(ctx.spec.get("ontology_version", "")),
        "window": v.window,
        "compare": v.compare,
        "show": v.show,
        "split": split,
    }
    first = dt.date.fromisoformat(pts[0][0])
    period = Period(g, first.isoformat(), s[-1][0].isoformat())  # type: ignore[arg-type]
    cid = f"summary:{metric}:{g}:{s[-1][0].isoformat()}:{v.window}:{v.compare}:{v.show}:{split}"
    title = f"{name} · {'% change' if v.show == 'pct' else 'change'} · {WINDOWS[v.window][2]}"
    y = YAxis(zero_line=True)
    line = ChartSpec(
        id=cid,
        kind="line",
        title=title,
        alt_text=alt,
        unit=cu,
        period=period,
        y=y,
        series=(Series("now", WINDOWS[v.window][2], "positive", tuple(pts), style="smooth"),),
        provenance=prov,
    )
    spark = ChartSpec(
        id=cid + ":spark",
        kind="sparkline",
        title=title,
        alt_text=alt,
        unit=cu,
        period=period,
        y=y,
        series=(Series("now", name, "positive", tuple(pts), style="smooth"),),
        provenance=prov,
    )
    return line, spark


def expand(ctx: Ctx, area_metrics: list[str], lead: str) -> dict[str, Any]:
    """The lead number's signed parts (its formula's terms; per breakdown with a split), then the
    area's other numbers. A missing part counts as zero, as the formula does."""
    lf = figure(ctx, lead)
    e = ctx.spec.get("metrics", {}).get(lead) or {}
    pair = lf.get("pair")
    terms = (e.get("terms_by") or {}).get(pair[0]) if pair else None
    terms = terms or e.get("terms") or []
    parts = []
    if terms and lf.get("value") is not None:
        at = lf["period"]
        for t in terms:
            m = t["metric"]
            pe = ctx.spec.get("metrics", {}).get(m) or {}
            pp = pair if pair and pair[0] in (pe.get("breakdowns") or []) else None
            ps = series(ctx.points, m, lf["grain"], pp)
            idx = next((i for i, (p, _v) in enumerate(ps) if p == at), None)
            v = ps[idx][1] if idx is not None else Decimal(0)
            comp = compared(ps, lf["grain"], ctx.view.basis, at=idx) if idx is not None else None
            text, direction = change(lf["unit"], v, comp, ctx.view.show)
            parts.append(
                {
                    "metric": m,
                    "name": str(pe.get("name") or m),
                    "sign": int(t["sign"]),
                    "value": v,
                    "value_text": values.text(lf["unit"], v),
                    "change_text": text,
                    "direction": direction,
                }
            )
    others = [figure(ctx, m) for m in area_metrics if m != lead]
    for o in others:
        o.pop("series", None)
    return {"parts": parts, "others": others}


def build(
    spec: dict[str, Any],
    points: Mapping[str, tuple[Point, ...]],
    *,
    layout: Mapping[str, Any] | None = None,
    view: View | None = None,
    findings: list[dict[str, Any]] | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    """Every row of the page, the hidden areas, and the view."""
    lay = layout or {}
    view = view or View()
    ctx = Ctx(spec, points, view, run_id)
    groups = by_area(spec)
    order = area_order(list(groups), list(lay.get("order") or []))
    hidden = [a for a in order if a in set(lay.get("hidden") or [])]
    rows = []
    for a in order:
        if a in hidden:
            continue
        ms = groups[a]
        lead = lead_of(spec, a, ms, lay.get("leads") or {})
        fig = figure(ctx, lead)
        line, spark = charts(ctx, fig)
        fig.pop("series", None)
        rows.append(
            {
                "area": a,
                "lead": lead,
                **fig,
                "finding": marker(spec, lead, findings or [], view.pair),
                "chart": line,
                "spark": spark,
                "metrics": [(m, str(spec["metrics"][m].get("name") or m)) for m in ms],
            }
        )
    out: dict[str, Any] = {"rows": rows, "hidden": hidden, "view": view, "order": order}
    hidden_numbers = [m for m in lay.get("hidden_numbers") or [] if m in spec.get("metrics", {})]
    out["hidden_numbers"] = [(m, str(spec["metrics"][m].get("name") or m)) for m in hidden_numbers]
    if view.rows == "numbers":  # every number as its own row, under its area's heading
        out["groups"] = []
        for a in order:
            if a in hidden:
                continue
            nums = []
            for m in groups[a]:
                if m in hidden_numbers:
                    continue
                fig = figure(ctx, m)
                line, spark = charts(ctx, fig)
                fig.pop("series", None)
                nums.append(
                    {
                        "area": a,
                        "lead": m,
                        **fig,
                        "finding": marker(spec, m, findings or [], view.pair),
                        "chart": line,
                        "spark": spark,
                        "metrics": [],
                    }
                )
            if nums:
                out["groups"].append({"area": a, "count": len(groups[a]), "rows": nums})
    return out


# --- the page (reads the stored run) -----------------------------------------------------------


def page(
    s: Any,
    settings: Any,
    today: dt.date,
    query: Mapping[str, Any] | None = None,
    *,
    expanded: bool = False,
) -> dict[str, Any]:
    """The page's dict; `expanded` adds each row's parts, other numbers and definition (M16.3)."""
    from othor.web import run_cache
    from othor.web import summary_layout as sl
    from othor.web import sweep_view as sv
    from othor.web.ontology_view import run_data

    rd = run_data(s, settings)
    lay = sl.get(s)
    if rd.run is None or rd.files is None:
        return {
            "empty": "before_first_run",
            "rows": [],
            "hidden": [],
            "view": View.parse(query, lay["view"]),
        }
    pts = run_cache.by_metric(rd.files.series) if rd.files.series.exists() else {}
    splits = run_cache.scope_options(rd.spec, rd.files.series) if rd.files.series.exists() else {}
    view = View.parse(query, lay["view"], splits)
    runs = sv.detecting_runs(s)
    rv = sv.load(s, settings, runs[0], today) if runs else None
    out = build(
        rd.spec,
        pts,
        layout=lay,
        view=view,
        findings=list(rv.shown) if rv else [],
        run_id=str(rd.run.id),
    )
    if expanded:
        ctx = Ctx(rd.spec, pts, view, str(rd.run.id))
        groups = by_area(rd.spec)
        every = [r for g in out.get("groups") or [] for r in g["rows"]]
        for r in out["rows"]:
            r["expand"] = expand(ctx, groups[r["area"]], r["lead"])
        for r in every:  # a number's own row opens to its parts: the others are rows themselves
            r["expand"] = expand(ctx, [r["lead"]], r["lead"])
        for r in out["rows"] + every:
            e = rd.spec["metrics"].get(r["lead"]) or {}
            r["description"] = str(e.get("description") or "")
            r["version"] = rd.spec.get("ontology_version")
    names = {k: str(b.get("name") or k) for k, b in (rd.spec.get("breakdowns") or {}).items()}
    return {
        **out,
        "empty": "" if out["rows"] or out["hidden"] else "no_numbers",
        "as_of": f"As of last night's run · {fmt.day_month(rd.run.run_date)}",
        "windows": [(k, w[2]) for k, w in WINDOWS.items()],
        "compares": list(COMPARES.items()),
        "shows": list(SHOWS.items()),
        "row_views": list(ROWS.items()),
        "splits": [("", "Whole company")]
        + [(f"{k}={v}", f"{names.get(k, k)} · {v}") for k, vs in splits.items() for v in vs],
        "shown_first": SHOWN_FIRST,
    }


def splits_of(s: Any, settings: Any) -> dict[str, list[str]]:
    """The breakdown values Summary offers, from the last run (low-cardinality, flows)."""
    from othor.web import run_cache
    from othor.web.ontology_view import run_data

    rd = run_data(s, settings)
    if rd.files is None or not rd.files.series.exists():
        return {}
    return run_cache.scope_options(rd.spec, rd.files.series)


def area_metrics(s: Any, settings: Any) -> dict[str, list[str]]:
    """Each area's numbers in the last run's spec (for the lead number's choice)."""
    from othor.web.ontology_view import run_data

    return by_area(run_data(s, settings).spec)
