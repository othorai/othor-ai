# SPDX-License-Identifier: Apache-2.0
"""The outlook, next four weeks (v0.12 part 1b, docs/plans/business-weather.md): This week, Next
week, Week 3 and Week 4, each the worst scene among its items, and the Ahead line, the most
important item. Picked by code from the run's own stored results; no model, no number typed in a
template.

What feeds a week (all stored before the outcome):
- **Weekly forecasts against the plan.** Every headline number with a goal has a weekly plan line:
  a fiscal-year total needs what is left of its target spread evenly over the days to the goal
  date (`plan per week = 7 × (target − actual to date) ÷ days left`); a goal on a level (a
  ratio, days, a snapshot) is its target. "likely" when the whole 80% band is on the wrong side of
  the line, "could" when only the point is. A band wider than the company setting (default ±25%
  of the point) can't be called: the Limited-view scene ("Too uncertain to call"). A headline
  number without a goal has no line to call, so it adds nothing to a week.
- **Goal projections** whose goal date falls in the week.
- **Consequence dates** on open findings (a payment due, a month close) that fall in the week.
- **Watches** whose number's weekly forecast is projected to cross the threshold (a watch on a
  number with a weekly forecast, read as a level; one already breached is today's matter).
- **Known patterns ahead:** a confirmed, company-wide pattern whose window touches the week (the
  Quiet-day scene).

A week with nothing is All clear. The precedence is the weather's (`weather.PRECEDENCE`); the
worst week (Limited view never counts as worse: an uncertain week isn't a bad one) is marked.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from othor.briefs import values
from othor.core import fmt
from othor.engine.forecast.ets import BAND_WEEKS, LEVEL
from othor.engine.store import Point

FORMAT = 1
WEEKS = 4
QUIET = ("clear", "clear_night", "fog")  # scenes that never make the worst week or the Ahead line

# One template per line, filled with computed values only: no digit here.
LINES: dict[str, str] = {
    "likely_short": "{metric} likely {gap} short of plan",
    "likely_over": "{metric} likely {gap} over plan",
    "could_short": "{metric} could be {gap} short of plan",
    "could_over": "{metric} could be {gap} over plan",
    "on_plan": "{metric} on plan: {point} against {plan}",
    "fog": "Too uncertain to call · bands too wide this far out",
    "fog_why": "The band is {band} wide; it narrows as more weeks of data come in.",
    "fog_noband": (
        "No band yet for {metric}; it starts after {weeks} weeks of history, as more data comes in."
    ),
    "goal_miss": "{goal}: likely missed, {bound} at best",
    "goal_amber": "{goal}: could be missed, {projection} projected",
    "goal_on_track": "{goal}: on track, {projection} projected",
    "finding": "{title} · {when}",
    "watch_likely": "{label} likely to cross {threshold}",
    "watch_could": "{label} could cross {threshold}",
    "pattern": "{pattern}: a quiet week, as your Business Model expects",
    "clear": "Nothing ahead on the forecasts or the open findings",
    # the Ahead line
    "ahead_likely": "{subject} likely {gap} {side} of plan ({level} band {band}) · {days}.",
    "ahead_could": (
        "{subject} could be {gap} {side} of plan ({level} band {band} against {plan}) · {days}."
    ),
    "ahead_goal": "{line} ({level} band {band}) · {days}.",
    "ahead_dated": "{line} · {date}, {days}.",
    "ahead_finding": "{line} ({date}).",
    "ahead_none": "Nothing stands out in the next four weeks.",
}


def _d(v: Any) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def _in(days: int) -> str:
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {fmt.num(days)} days"


def week_name(i: int) -> str:
    return {1: "This week", 2: "Next week"}.get(i) or f"Week {fmt.num(i)}"


def windows(run_date: dt.date) -> list[tuple[dt.date, dt.date]]:
    """The next 28 days in four 7-day weeks, from the morning after the run (owner, 12 Nov 2026:
    a month close 28 days out, like 30 Nov on the 2 Nov night, is in the outlook). A weekly
    forecast belongs to the window its calendar week ends in (`forecast_week`)."""
    start = run_date + dt.timedelta(days=1)
    return [
        (start + dt.timedelta(weeks=i), start + dt.timedelta(weeks=i, days=6)) for i in range(WEEKS)
    ]


def forecast_week(period: str, weeks: list[tuple[dt.date, dt.date]]) -> int | None:
    """The window a weekly forecast (Monday `period`) falls in: the one holding its Sunday."""
    return _week_of(dt.date.fromisoformat(period) + dt.timedelta(days=6), weeks)


@dataclass
class Item:
    kind: str  # forecast | goal | finding | watch | pattern
    key: str
    week: int
    scene: str
    line: str
    money: Decimal = Decimal(0)
    date: dt.date | None = None
    metric: str | None = None
    more: dict[str, Any] = field(default_factory=dict)

    def doc(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "scene": self.scene,
            "line": self.line,
            "money": str(self.money),
            "date": self.date.isoformat() if self.date else None,
            "metric": self.metric,
            **self.more,
        }


# --- the plan line -----------------------------------------------------------------------------


def _level(m: dict[str, Any]) -> bool:
    """A number read as a level (a ratio, days, a snapshot), not summed over a week."""
    return m.get("kind") == "snapshot" or m.get("unit") not in ("inr", "metres", "count", None)


def _complete(points: list[Point], metric: str, grain: str) -> list[tuple[dt.date, float]]:
    """A series' complete, current values, as the forecast step reads them."""
    from othor.engine.detectors.base import SeriesIndex

    s = SeriesIndex(p for p in points if p.metric == metric and p.grain == grain).get(metric, grain)
    if s is None:
        return []
    return [(p, float(v)) for p, v in zip(s.periods, s.values, strict=True) if v == v]


def _last_month(points: list[Point], metric: str, by: dt.date) -> dt.date | None:
    return max((p for p, _ in _complete(points, metric, "month") if p <= by), default=None)


def plans(
    spec: dict[str, Any], goals: list[dict[str, Any]], points: list[Point]
) -> dict[str, dict[str, Any]]:
    """Each goal's metric → its weekly plan line (the earliest goal first): the value, the
    direction, the goal and how the line is made."""
    metrics = spec.get("metrics", {})
    out: dict[str, dict[str, Any]] = {}
    for g in sorted(goals, key=lambda g: (str(g["by"]), str(g["key"]))):
        metric = g["metric"]
        if metric in out:
            continue
        m = metrics.get(metric) or {}
        by = dt.date.fromisoformat(str(g["by"]))
        target = _d(g["target"])
        if g.get("aggregate") == "fiscal_year_total":
            last = _last_month(points, metric, by)
            if last is None or g.get("to_date") is None:
                continue
            rest = (last.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            days = (by - rest).days + 1
            if days <= 0:
                continue
            plan = (target - _d(g["to_date"])) * 7 / days
            how = "what is left of the goal, spread evenly over the days to its date"
        elif _level(m):
            plan, how = target, "the goal's target"
        else:
            continue
        out[metric] = {
            "value": plan.quantize(Decimal("0.01")),
            "direction": g.get("direction", "at_least"),
            "goal": g["key"],
            "how": how,
        }
    return out


# --- the items ---------------------------------------------------------------------------------


def _name(spec: dict[str, Any], metric: str) -> str:
    return str((spec.get("metrics", {}).get(metric) or {}).get("name") or metric)


def _forecast_items(
    spec: dict[str, Any],
    rows: list[dict[str, Any]],
    plan: dict[str, dict[str, Any]],
    weeks: list[tuple[dt.date, dt.date]],
    cfg: dict[str, Any],
    month: Decimal | None,
) -> list[Item]:
    out = []
    wide_share = _d(cfg["fog_band_share"])
    for r in rows:
        metric = r["metric"]
        week = forecast_week(r["period"], weeks)
        if week is None or metric not in plan:
            continue
        unit = (spec.get("metrics", {}).get(metric) or {}).get("unit")
        name = _name(spec, metric)
        p = plan[metric]
        line_v = p["value"]
        point = _d(r["point"])
        base = {
            "horizon": int(r["horizon"]),
            "point": r["point"],
            "lower": r["lower"],
            "upper": r["upper"],
            "plan": str(line_v),
            "direction": p["direction"],
            "goal": p["goal"],
            "unit": unit,
            "name": name,
        }
        # the forecast's own week ends on its Sunday (the window may end a few days later)
        end = dt.date.fromisoformat(r["period"]) + dt.timedelta(days=6)
        if r["lower"] is None or r["upper"] is None:
            out.append(
                Item(
                    "forecast",
                    metric,
                    week,
                    "fog",
                    LINES["fog"],
                    date=end,
                    metric=metric,
                    more={
                        **base,
                        "verdict": "wide",
                        "why": LINES["fog_noband"].format(metric=name, weeks=fmt.num(BAND_WEEKS)),
                    },
                )
            )
            continue
        lo, hi = _d(r["lower"]), _d(r["upper"])
        if (hi - lo) / 2 > abs(point) * wide_share:
            out.append(
                Item(
                    "forecast",
                    metric,
                    week,
                    "fog",
                    LINES["fog"],
                    date=end,
                    metric=metric,
                    more={
                        **base,
                        "verdict": "wide",
                        "why": LINES["fog_why"].format(band=values.band(unit, lo, hi)),
                    },
                )
            )
            continue
        at_least = p["direction"] == "at_least"
        gap = (line_v - point) if at_least else (point - line_v)
        bad_all = (hi < line_v) if at_least else (lo > line_v)
        side = "short" if at_least else "over"
        cash = gap if unit == "inr" else Decimal(0)
        if bad_all:
            share = _d(cfg["large_share"]) * month if month is not None else None
            scene = "heavy_rain" if share is not None and cash >= share else "light_rain"
            out.append(
                Item(
                    "forecast",
                    metric,
                    week,
                    scene,
                    LINES[f"likely_{side}"].format(metric=name, gap=values.text(unit, gap)),
                    cash,
                    end,
                    metric,
                    {**base, "verdict": "likely", "gap": str(gap.quantize(Decimal("0.01")))},
                )
            )
        elif gap > 0:
            out.append(
                Item(
                    "forecast",
                    metric,
                    week,
                    "overcast",
                    LINES[f"could_{side}"].format(metric=name, gap=values.text(unit, gap)),
                    cash,
                    end,
                    metric,
                    {**base, "verdict": "could", "gap": str(gap.quantize(Decimal("0.01")))},
                )
            )
        else:
            out.append(
                Item(
                    "forecast",
                    metric,
                    week,
                    "clear",
                    LINES["on_plan"].format(
                        metric=name, point=values.text(unit, point), plan=values.text(unit, line_v)
                    ),
                    Decimal(0),
                    end,
                    metric,
                    {**base, "verdict": "on_plan"},
                )
            )
    return out


def _week_of(day: dt.date, weeks: list[tuple[dt.date, dt.date]]) -> int | None:
    return next((i for i, (a, b) in enumerate(weeks, start=1) if a <= day <= b), None)


def _goal_items(
    spec: dict[str, Any],
    goals: list[dict[str, Any]],
    weeks: list[tuple[dt.date, dt.date]],
    run_date: dt.date,
    cfg: dict[str, Any],
) -> list[Item]:
    out = []
    for g in goals:
        by = dt.date.fromisoformat(str(g["by"]))
        week = _week_of(by, weeks)
        if week is None or g.get("projection") is None:
            continue
        unit = (spec.get("metrics", {}).get(g["metric"]) or {}).get("unit")
        name = str(g.get("name") or g["key"])
        at_least = g.get("direction", "at_least") == "at_least"
        t, proj = _d(g["target"]), _d(g["projection"])
        lo = None if g.get("lower") is None else _d(g["lower"])
        hi = None if g.get("upper") is None else _d(g["upper"])
        short = _d(g.get("short_by"))
        cash = short if unit == "inr" and short > 0 else Decimal(0)
        more = {
            "goal": g["key"],
            "lower": g.get("lower"),
            "upper": g.get("upper"),
            "point": g["projection"],
            "plan": g["target"],
            "direction": g.get("direction", "at_least"),
            "unit": unit,
            "name": name,
        }
        miss = (
            lo is not None
            and hi is not None
            and ((at_least and hi < t) or (not at_least and lo > t))
        )
        if miss:
            near = by <= run_date + dt.timedelta(days=int(cfg["today_days"]))
            line = LINES["goal_miss"].format(
                goal=name, bound=values.text(unit, hi if at_least else lo)
            )
            out.append(
                Item(
                    "goal",
                    g["key"],
                    week,
                    "thunderstorm" if near else "heavy_rain",
                    line,
                    cash,
                    by,
                    g["metric"],
                    {**more, "verdict": "likely"},
                )
            )
        elif not g.get("on_track"):
            line = LINES["goal_amber"].format(goal=name, projection=values.text(unit, proj))
            out.append(
                Item(
                    "goal",
                    g["key"],
                    week,
                    "overcast",
                    line,
                    cash,
                    by,
                    g["metric"],
                    {**more, "verdict": "could"},
                )
            )
        else:
            line = LINES["goal_on_track"].format(goal=name, projection=values.text(unit, proj))
            out.append(
                Item(
                    "goal",
                    g["key"],
                    week,
                    "clear",
                    line,
                    Decimal(0),
                    by,
                    g["metric"],
                    {**more, "verdict": "on_plan"},
                )
            )
    return out


def _finding_items(
    spec: dict[str, Any],
    findings: list[dict[str, Any]],
    weeks: list[tuple[dt.date, dt.date]],
    run_date: dt.date,
    cfg: dict[str, Any],
    month: Decimal | None,
) -> list[Item]:
    from othor.briefs import weather

    out = []
    for i in weather.items(findings, spec, run_date):
        if i.date is None or i.date < run_date:
            continue
        week = _week_of(i.date, weeks)
        if week is None:
            continue
        det, up = i.f.get("detector"), i.f.get("direction") == "up"
        material = month is None or i.money >= month * _d(cfg["material_share"])
        big = month is None or i.money >= month * _d(cfg["big_share"])
        if not material:
            scene = "partly_cloudy"
        elif det in weather.UNSTEADY:
            scene = "windy"
        elif det in weather.HOT and up:
            scene = "heat_wave"
        elif big and i.date <= run_date + dt.timedelta(days=int(cfg["today_days"])):
            scene = "thunderstorm"
        else:
            scene = "heavy_rain"
        fp = str(i.f.get("fingerprint"))
        out.append(
            Item(
                "finding",
                fp,
                week,
                scene,
                LINES["finding"].format(title=weather._title(i.f), when=i.when),
                i.money,
                i.date,
                i.f.get("metric"),
                {"fingerprint": fp, "title": weather._title(i.f)},
            )
        )
    return out


def _watch_items(
    spec: dict[str, Any],
    watches: list[dict[str, Any]],
    status: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    weeks: list[tuple[dt.date, dt.date]],
) -> list[Item]:
    """A watch on a number with a weekly forecast, read as a level (a watch on the week, or on
    a number that isn't summed over a week), projected to cross its threshold."""
    breached = {w["id"] for w in status if w.get("status") == "breached"}
    out = []
    for w in sorted(watches, key=lambda w: str(w["id"])):
        ind = w.get("indicator") or {}
        metric = ind.get("metric")
        if w["id"] in breached or ind.get("kind") != "value" or ind.get("breakdown"):
            continue
        m = spec.get("metrics", {}).get(metric) or {}
        if not (ind.get("grain") == "week" or _level(m)):
            continue
        thr = _d(w["threshold"])
        above = w.get("direction") == "above"
        for r in rows:
            week = forecast_week(r["period"], weeks)
            if r["metric"] != metric or week is None or r["lower"] is None:
                continue
            point, lo, hi = _d(r["point"]), _d(r["lower"]), _d(r["upper"])
            likely = lo > thr if above else hi < thr
            could = point > thr if above else point < thr
            if not (likely or could):
                continue
            label = str(w.get("label") or metric)
            out.append(
                Item(
                    "watch",
                    str(w["id"]),
                    week,
                    "light_rain" if likely else "overcast",
                    LINES["watch_likely" if likely else "watch_could"].format(
                        label=label, threshold=values.indicator_text(thr, "value")
                    ),
                    date=weeks[week - 1][1],
                    metric=metric,
                    more={
                        "verdict": "likely" if likely else "could",
                        "horizon": int(r["horizon"]),
                        "point": r["point"],
                        "lower": r["lower"],
                        "upper": r["upper"],
                        "threshold": w["threshold"],
                        "name": label,
                        "unit": m.get("unit"),
                    },
                )
            )
            break  # the first week it is projected to cross
    return out


def _pattern_items(spec: dict[str, Any], weeks: list[tuple[dt.date, dt.date]]) -> list[Item]:
    from othor.engine.detect import windows as pattern_windows

    out = []
    years = range(weeks[0][0].year - 1, weeks[-1][1].year + 2)
    for p in sorted(spec.get("suppressions") or [], key=lambda p: str(p.get("key"))):
        if p.get("confirmed", True) is False or p.get("entity") or p.get("plant"):
            continue
        if p.get("metrics"):
            continue
        spans = pattern_windows(p, years)
        for i, (a, b) in enumerate(weeks, start=1):
            if any(x <= b and a <= y for x, y in spans):
                name = str(p.get("short") or p.get("name") or p.get("key"))
                out.append(
                    Item(
                        "pattern",
                        str(p.get("key")),
                        i,
                        "clear_night",
                        LINES["pattern"].format(pattern=name),
                        date=a,
                        more={"name": name},
                    )
                )
    return out


# --- the weeks and the Ahead line --------------------------------------------------------------


def _rank(it: Item, precedence: tuple[str, ...]) -> tuple[Any, ...]:
    return (precedence.index(it.scene), -it.money, it.date or dt.date.max, it.kind, it.key)


def make(
    spec: dict[str, Any],
    run_date: dt.date,
    cfg: dict[str, Any],
    *,
    ahead: dict[str, Any] | None,
    goals: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    points: list[Point],
    watches: list[dict[str, Any]],
    watch_status: list[dict[str, Any]],
    month: Decimal | None,
) -> dict[str, Any]:
    """The four weeks and the Ahead line, from the run's own results."""
    from othor.briefs.weather import PRECEDENCE, SCENES

    weeks = windows(run_date)
    rows = [r for r in (ahead or {}).get("rows") or [] if r.get("grain") == "week"]
    plan = plans(spec, goals, points)
    items = [
        *_forecast_items(spec, rows, plan, weeks, cfg, month),
        *_goal_items(spec, goals, weeks, run_date, cfg),
        *_finding_items(spec, findings, weeks, run_date, cfg, month),
        *_watch_items(spec, watches, watch_status, rows, weeks),
        *_pattern_items(spec, weeks),
    ]
    out_weeks = []
    for i, (a, b) in enumerate(weeks, start=1):
        mine = sorted((x for x in items if x.week == i), key=lambda x: _rank(x, PRECEDENCE))
        top = mine[0] if mine else None
        key = top.scene if top else "clear"
        s = SCENES[key]
        out_weeks.append(
            {
                "week": i,
                "name": week_name(i),
                "start": a.isoformat(),
                "end": b.isoformat(),
                "dates": fmt.day_range(a, b),
                "scene": key,
                "file": s.file,
                "label": s.label,
                "line": top.line if top else LINES["clear"],
                "top": None if top is None else f"{top.kind}:{top.key}",
                "items": [x.doc() for x in mine],
            }
        )
    callable_ = [w for w in out_weeks if w["scene"] not in QUIET]
    worst = min(callable_, key=lambda w: (PRECEDENCE.index(w["scene"]), w["week"]), default=None)
    notable = sorted((x for x in items if x.scene not in QUIET), key=lambda x: _rank(x, PRECEDENCE))
    pick = notable[0] if notable else None
    return {
        "format": FORMAT,
        "weeks": out_weeks,
        "worst": None if worst is None else worst["week"],
        "plans": {k: {**v, "value": str(v["value"])} for k, v in sorted(plan.items())},
        "ahead": None
        if pick is None
        else {"week": pick.week, "item": f"{pick.kind}:{pick.key}", "scene": pick.scene},
        "ahead_line": LINES["ahead_none"] if pick is None else _ahead_line(pick, weeks, run_date),
    }


def _ahead_line(it: Item, weeks: list[tuple[dt.date, dt.date]], run_date: dt.date) -> str:
    level = fmt.pct(LEVEL)
    days = _in(((it.date or run_date) - run_date).days)
    m = it.more
    unit = m.get("unit")
    if it.kind == "forecast" and m.get("verdict") in ("likely", "could"):
        when = (
            "this week"
            if it.week == 1
            else "next week"
            if it.week == 2
            else f"in the week of {fmt.day_month(weeks[it.week - 1][0])}"
        )
        subject = f"{m['name']} {when}"
        at_least = m["direction"] == "at_least"
        side = "short" if at_least else "over"
        gap = _d(m["gap"])
        if m["verdict"] == "likely":
            plan, lo, hi = _d(m["plan"]), _d(m["lower"]), _d(m["upper"])
            a, b = (plan - hi, plan - lo) if at_least else (lo - plan, hi - plan)
            return LINES["ahead_likely"].format(
                subject=subject,
                gap=values.text(unit, gap),
                side=side,
                level=level,
                band=values.band(unit, a, b),
                days=days,
            )
        return LINES["ahead_could"].format(
            subject=subject,
            gap=values.text(unit, gap),
            side=side,
            level=level,
            band=values.band(unit, m["lower"], m["upper"]),
            plan=values.text(unit, _d(m["plan"])),
            days=days,
        )
    if it.kind == "goal" and m.get("lower") is not None and m.get("upper") is not None:
        return LINES["ahead_goal"].format(
            line=it.line, level=level, band=values.band(unit, m["lower"], m["upper"]), days=days
        )
    if it.kind == "finding":  # its consequence already says when ("November close in 28 days")
        return LINES["ahead_finding"].format(line=it.line, date=fmt.day_month(it.date or run_date))
    return LINES["ahead_dated"].format(
        line=it.line, date=fmt.day_month(it.date or run_date), days=days
    )


# --- the forecast panel's chart ----------------------------------------------------------------


def chart(
    spec: dict[str, Any],
    item: dict[str, Any],
    rows: list[dict[str, Any]],
    points: list[Point],
    weeks_back: int = 12,
) -> Any:
    """The item's number, weekly: the actuals so far, then tonight's forecast to the item's week
    with its 80% band, and the plan (or the watch's threshold) as a line. A ChartSpec built from
    the stored forecast, rendered by `render_svg`; None when there's nothing to draw."""
    from othor.charts.spec import Annotation, Band, ChartSpec, Period, Series

    metric = item.get("metric")
    if not metric:
        return None
    m = spec.get("metrics", {}).get(metric) or {}
    unit = m.get("unit")
    name = str(m.get("name") or metric)
    actual = _complete(points, metric, "week")[-weeks_back:]
    mine = [r for r in rows if r["metric"] == metric]
    upto = int(item.get("horizon") or 0) or max((int(r["horizon"]) for r in mine), default=0)
    ahead = sorted((r for r in mine if int(r["horizon"]) <= upto), key=lambda r: r["horizon"])
    if not actual or not ahead:
        return None

    def v(x: Any) -> float:
        return values.chart_value(unit, x)

    last_p, last_v = actual[-1]
    joint = (last_p.isoformat(), v(Decimal(repr(last_v))))
    fc = [joint, *((r["period"], v(r["point"])) for r in ahead)]
    banded = all(r["lower"] is not None for r in ahead)
    band = (
        Band(
            "forecast",
            LEVEL,
            (joint, *((r["period"], v(r["lower"])) for r in ahead)),
            (joint, *((r["period"], v(r["upper"])) for r in ahead)),
        )
        if banded
        else None
    )
    marks: tuple[Any, ...] = ()
    if item.get("kind") == "watch" and item.get("threshold") is not None:
        marks = (
            Annotation(
                "threshold",
                value=v(item["threshold"]),
                label=f"threshold {values.text(unit, item['threshold'])}",
            ),
        )
    elif item.get("kind") == "forecast" and item.get("plan") is not None:
        marks = (
            Annotation(
                "threshold", value=v(item["plan"]), label=f"plan {values.text(unit, item['plan'])}"
            ),
        )
    end = str(ahead[-1]["period"])
    alt = (
        f"{name}, weekly: actuals to {fmt.day_month(last_p)}, then the forecast to "
        f"{fmt.day_month(dt.date.fromisoformat(end))}"
        + (f" with its {fmt.pct(LEVEL)} band" if banded else "")
        + (f", against the {marks[0].label}." if marks else ".")
    )
    return ChartSpec(
        id=f"outlook:{metric}:{end}",
        kind="line",
        title=f"{name} · weekly",
        alt_text=alt,
        unit=values.chart_unit(unit),
        period=Period("week", actual[0][0].isoformat(), end),
        series=(
            Series(
                "actual",
                "Actual",
                "neutral",
                tuple((p.isoformat(), v(Decimal(repr(x)))) for p, x in actual),
            ),
            Series("forecast", "Forecast", "muted", tuple(fc)),
        ),
        band=band,
        annotations=marks,
        provenance={"metric_id": metric},
    )
