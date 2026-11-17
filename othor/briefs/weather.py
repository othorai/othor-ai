# SPDX-License-Identifier: Apache-2.0
"""Business weather, today (v0.12 part 1a, docs/plans/business-weather.md): one of 15 scenes and
one line for the company, picked by code from the sealed run's own outputs. No model.

- **Scene:** the worst that applies wins, in this order: No signal → Sunrise → Frozen →
  Thunderstorm → Hail → Heavy rain → Heat wave → Windy → Light rain → Fog → Overcast → Partly
  cloudy → Rainbow → Clear night / Clear. Sunrise (the first brief) sits just under No signal: a
  source that couldn't be read is said first, even on the first morning.
- **Line:** one template per scene, filled with computed values only (as brief titles are); no
  number is typed in a template.
- **Why:** the facts the scene used (counts, money, the nearest date), shown in provenance.
- **Frozen and Clear night share one rule:** which days are normally active, from each weekday's
  activity over the last 12 weeks and the Ontology's known patterns. Frozen only when that
  weekday was active in about 90% of those weeks, never under a known pattern, and only when every
  source was read.

- **Since the last brief (part 1b):** a source connected since the last brief run is Sunrise too;
  a goal on track tonight that wasn't then is Rainbow (`previous_of` reads that run's files).
- **Rule:** the rule that picked the scene in plain words ("How this was picked"), thresholds
  filled from the settings read.
- **Outlook (part 1b):** the next four weeks and the Ahead line, `othor.briefs.outlook`.
- **Each brief's own (part 1c):** the same scoring and outlook over what is in the brief's scope
  only (`for_briefs`), stored next to the company's in `weather_briefs.json`.

Thresholds are company settings: `DEFAULTS`, overridden by the spec's `weather` block (an
Ontology `weather` record). Pure: the run's brief step and replay call `make` on the same inputs
and get the same bytes (`weather.json` in the run's outputs).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from othor.briefs import consequence
from othor.core import fmt
from othor.engine.store import Point, RunFiles, read_json, read_points
from othor.engine.sweep_text import money

FORMAT = 1
WEATHER = "weather.json"  # in the run's outputs, next to briefs.json


@dataclass(frozen=True)
class Scene:
    key: str
    file: str  # the graphics' name: static/weather/{web,phone}/<file>.{webp,png}, icons/<file>.svg
    name: str  # the picture (internal; never shown as the state)
    label: str  # the state of the business shown on screen (owner, 12 Nov 2026)
    text: str  # the text colour on the scene: ink #1F2430 on every scene (owner, 14 Nov 2026)
    panel: str  # colours.csv: the solid colour behind the phone and email text


# The blueprint pastel set (owner, 14 Nov 2026): light panels, dark text on every scene
INK = "#1F2430"


# #62 (owner, 14 Nov 2026): the weather is hidden in briefs (app, email, PDF, shared links) until
# it is revisited in v0.24; Home keeps it. The run still stores it, so turning this back on shows
# every stored brief's weather again.
IN_BRIEFS = False

SCENES: dict[str, Scene] = {
    s.key: s
    for s in (
        Scene("clear", "01-clear", "Clear", "All clear", INK, "#FFFFFF"),
        Scene(
            "partly_cloudy",
            "02-partly-cloudy",
            "Partly cloudy",
            "Minor items",
            INK,
            "#DCEEFF",
        ),
        Scene("overcast", "03-overcast", "Overcast", "Under watch", INK, "#E6E9F0"),
        Scene("fog", "04-fog", "Fog", "Limited view", INK, "#ECE9F5"),
        Scene("light_rain", "05-light-rain", "Light rain", "Worth a look", INK, "#E1E6FB"),
        Scene("heavy_rain", "06-heavy-rain", "Heavy rain", "Money at stake", INK, "#D9E2F7"),
        Scene("thunderstorm", "07-thunderstorm", "Thunderstorm", "Needs you", INK, "#E9DFFB"),
        Scene("hail", "08-hail", "Hail", "Sudden drop", INK, "#DCEFF0"),
        Scene("windy", "09-windy", "Windy", "Unsteady", INK, "#FDE3EE"),
        Scene("heat_wave", "10-heat-wave", "Heat wave", "Running hot", INK, "#FFE6D6"),
        Scene("rainbow", "11-rainbow", "Rainbow", "Recovering", INK, "#DFF5E6"),
        Scene(
            "clear_night",
            "12-clear-night",
            "Clear night",
            "Quiet day, as expected",
            INK,
            "#E2E0FA",
        ),
        Scene("sunrise", "13-sunrise", "Sunrise", "First brief", INK, "#FFFFFF"),
        Scene("no_signal", "14-no-signal", "No signal", "Data missing", INK, "#F7ECD8"),
        Scene("frozen", "15-frozen", "Frozen", "Activity stopped", INK, "#E4F3FB"),
    )
}

# what each picture shows (the owner's colours.csv, 12 Nov 2026): the screen readers' text
PLACES: dict[str, str] = {
    "01-clear": "A great tree on an open hill",
    "02-partly-cloudy": "Patchwork farmland with a barn",
    "03-overcast": "A still lake with a jetty and two boats",
    "04-fog": "A cedar forest and a rope bridge",
    "05-light-rain": "Rice paddies with a field hut",
    "06-heavy-rain": "A swollen river under a stone bridge",
    "07-thunderstorm": "A lighthouse on a sea cliff",
    "08-hail": "An apple orchard at harvest",
    "09-windy": "Wind turbines on a grassy ridge",
    "10-heat-wave": "Desert dunes and a lone acacia",
    "11-rainbow": "A waterfall valley after rain",
    "12-clear-night": "A sleeping town with festival lights",
    "13-sunrise": "A boat leaving harbour at dawn",
    "14-no-signal": "A relay tower on a dusty ridge",
    "15-frozen": "A watermill locked in ice",
}


def alt(file: str) -> str:
    """The picture's alt text: "<Label>: <place>" ("Money at stake: a swollen river under a stone
    bridge"), for the banner on a page and the header of an email."""
    s = next((x for x in SCENES.values() if x.file == file), None)
    place = PLACES.get(file, "")
    where = (place[0].lower() + place[1:]) if place else "an illustration"
    return f"{s.label}: {where}" if s else where


PRECEDENCE = (
    "no_signal",
    "sunrise",
    "frozen",
    "thunderstorm",
    "hail",
    "heavy_rain",
    "heat_wave",
    "windy",
    "light_rain",
    "fog",
    "overcast",
    "partly_cloudy",
    "rainbow",
    "clear_night",
    "clear",
)

# The thresholds, in one place (company settings; the spec's `weather` block overrides them).
DEFAULTS: dict[str, Any] = {
    "material_share": "0.01",  # an item at or over 1% of a month's revenue is material
    "big_share": "0.05",  # Thunderstorm: one item this large, due within `today_days`
    "large_share": "0.10",  # Heavy rain: this much at stake in all, whatever the dates
    "today_days": 7,  # Thunderstorm: the consequence date is this close
    "near_days": 30,  # Heavy rain: a material item due within this many days
    "sudden_days": 7,  # Hail: the step or the break started this recently
    "several": 3,  # Overcast: this many minor items
    "fog_history_days": 56,  # Fog: less history than this
    "fog_ran_share": "0.5",  # Fog: fewer than this share of the checks could run
    "active_weeks": 12,  # Frozen / Clear night: the weeks of history read per weekday
    "active_share": "0.9",  # Frozen: the weekday was active in at least this share of them
    # the outlook (part 1b): a week's band wider than this share of the point either side
    # can't be called (Limited view)
    "fog_band_share": "0.35",  # ±35% (owner, 12 Nov 2026; was 0.25)
}
# which thresholds are shares (decimal strings) and which are whole numbers: an Ontology
# `weather` record is checked against these (othor/ontology/types.py)
SHARES = (
    "material_share",
    "big_share",
    "large_share",
    "fog_ran_share",
    "active_share",
    "fog_band_share",
)

# what each scene reads in a finding (plan: Hail "a step down or broken link", Windy "rising
# variance, a mix shifting", Heat wave "well above its band")
SUDDEN = ("changepoint", "broken_relationship")
UNSTEADY = ("rising_variance", "bimodal_average", "concentration")
HOT = ("forecast_breach",)
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}

# One template per scene (and its variants), filled with computed values only: no digit here.
LINES: dict[str, str] = {
    "clear": "Nothing needs you today. All {checks} checks ran clean.",
    "clear_some": "Nothing needs you today. {ran} of {checks} checks ran and found nothing.",
    "clear_night_pattern": (
        "A quiet day: no activity on {day}, as your Business Model expects ({pattern})."
    ),
    "clear_night": "A quiet day: no activity on {day}, as usual for a {weekday}.",
    "partly_cloudy": "{count} to glance at, none large; the business is on plan.",
    "overcast_goal": "Grey but steady: {metric} is tracking {projection} against {target}.",
    "overcast": "{count}, none large and none urgent; the business is steady.",
    "fog_checks": (
        "Only {ran} of {checks} checks could run; the view clears as the Business Model fills in."
    ),
    "fog_history": "{days} days of history so far; the view clears as more data comes in.",
    "light_rain": "{title}; worth a look this week.",
    "light_rain_more": "{title}; {more} worth a look this week.",
    "heavy_rain": "{money} at stake across {count}; the nearest date is {nearest}.",
    "heavy_rain_share": "{money} at stake across {count}, a large share of a month’s revenue.",
    "thunderstorm": "{title}. This needs you today.",
    "goal_miss": (
        "Even at the {edge} of its band, {metric} reaches {bound} against the {target} goal "
        "for {by}."
    ),
    "hail": "{title}. It started {day}.",
    "windy": "{title}. Unsteady, rather than lower.",
    "heat_wave": "{title}. Running above its forecast band.",
    "rainbow": "{count} since yesterday; nothing new needs you.",
    "rainbow_goal": "{goal} is back on track since the last brief; nothing new needs you.",
    "sunrise": "Your first brief: here’s how the business looks.",
    "sunrise_source": (
        "{sources} connected since the last brief: here’s how the business looks with it."
    ),
    "no_signal": "{sources} couldn’t be read last night; today’s view is missing {metrics}.",
    "no_signal_none": (
        "{sources} couldn’t be read last night; today’s view uses the last good read."
    ),
    "frozen": "No activity on {day}, a working day; all sources were read.",
    # #57 (owner, 14 Nov 2026): read fine, but no rows for yesterday on a day it normally has them
    "no_signal_not_updated": "{source} wasn’t updated yesterday; its newest data is from {day}.",
    "no_signal_not_updated_few": "A few of your sources weren’t updated yesterday: {sources}.",
}


# "How this was picked" (part 1b): the rule behind each scene in plain words, the thresholds
# filled from the settings the run read (never typed here). Labels only, never weather words.
RULES: dict[str, str] = {
    "no_signal": (
        "Data missing: a source couldn’t be read last night, or had no rows for yesterday on a "
        "day it normally has them (that weekday in at least {active_share} of the last "
        "{active_weeks} weeks) and no known pattern."
    ),
    "sunrise": "First brief: the first brief, or a source connected since the last one.",
    "frozen": (
        "Activity stopped: no activity on a day that’s normally active (that weekday active in "
        "at least {active_share} of the last {active_weeks} weeks), every source read and no "
        "known pattern."
    ),
    "thunderstorm": (
        "Needs you: an item of at least {big_share} of a month’s revenue due within "
        "{today_days} days, or a goal due within {near_days} days whose whole band misses."
    ),
    "hail": (
        "Sudden drop: a step down or a broken link that started in the last {sudden_days} days."
    ),
    "heavy_rain": (
        "Money at stake: items of at least {material_share} of a month’s revenue due within "
        "{near_days} days, {large_share} of a month’s revenue at stake in all, or a goal whose "
        "whole band misses."
    ),
    "heat_wave": "Running hot: a number above its forecast band.",
    "windy": "Unsteady: rising variance, a split average or a mix shifting, with no loss yet.",
    "light_rain": (
        "Worth a look: an item of at least {material_share} of a month’s revenue, none due "
        "within {near_days} days."
    ),
    "fog": (
        "Limited view: under {fog_history_days} days of history, or fewer than {fog_ran_share} "
        "of the checks could run."
    ),
    "overcast": (
        "Under watch: a goal off its projection but inside its band, or {several} or more "
        "minor items."
    ),
    "partly_cloudy": "Minor items: items under {material_share} of a month’s revenue.",
    "rainbow": "Recovering: findings resolved since yesterday, or a goal back on track.",
    "clear_night": (
        "Quiet day, as expected: no activity on a day that’s normally quiet, or under a known "
        "pattern."
    ),
    "clear": "All clear: the checks ran and nothing material is open.",
}


def settings(spec: dict[str, Any]) -> dict[str, Any]:
    return {**DEFAULTS, **(spec.get("weather") or {})}


def rule(key: str, cfg: dict[str, Any]) -> str:
    """The rule that picks a scene, its thresholds from the settings: shares as percentages."""

    def share(v: Any) -> str:
        d = Decimal(str(v)) * 100
        return fmt.pct(Decimal(str(v)), decimals=0 if d == d.to_integral_value() else 1)

    filled = {k: share(v) if k in SHARES else fmt.num(int(v)) for k, v in cfg.items()}
    return RULES[key].format(**filled)


# --- the facts ---------------------------------------------------------------------------------


def _d(v: Any) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def _count(n: int, noun: str, plural: str | None = None) -> str:
    return f"{WORDS.get(n, fmt.num(n))} {noun if n == 1 else (plural or noun + 's')}"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _and(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def _title(f: dict[str, Any]) -> str:
    t = str((f.get("text") or {}).get("title") or f.get("detector") or "").strip()
    return t[:-1] if t.endswith(".") else t


def revenue(spec: dict[str, Any], baselines: dict[str, Any]) -> tuple[str, Decimal] | None:
    """A month's revenue, the yardstick for 'material' and 'large': the mean of the last 12
    months of the year goal's metric (in ₹), else of the first headline metric in ₹."""
    metrics = spec.get("metrics", {})
    goals = sorted(spec.get("forecast", {}).get("goals", []), key=lambda g: (g["by"], g["key"]))
    keys = [g["metric"] for g in goals if g.get("aggregate") == "fiscal_year_total"]
    keys += sorted(spec.get("forecast", {}).get("headline", []))
    for k in keys:
        m = metrics.get(k) or {}
        if m.get("unit") != "inr" or m.get("kind", "flow") != "flow":
            continue
        b = baselines.get(f"{k}|month") or {}
        if b.get("mean") is not None and _d(b["mean"]) > 0:
            return k, _d(b["mean"])
    return None


@dataclass(frozen=True)
class Item:
    f: dict[str, Any]
    money: Decimal
    date: dt.date | None
    when: str  # the consequence's words


def items(findings: list[dict[str, Any]], spec: dict[str, Any], run_date: dt.date) -> list[Item]:
    """The open findings a brief could carry (needs confirmation never enters), largest first."""
    out = []
    for f in findings:
        if f.get("status") != "open" or f.get("state") == "needs_confirmation":
            continue
        c = consequence.of(f, spec, run_date)
        when = dt.date.fromisoformat(c["date"]) if c["date"] else None
        out.append(Item(f, _d(f.get("money")), when, str(c["text"])))
    return sorted(out, key=lambda i: (-i.money, str(i.f.get("fingerprint"))))


def _quiet_pattern(spec: dict[str, Any], day: dt.date) -> str | None:
    """A confirmed, company-wide known pattern whose window covers the day."""
    from othor.engine.detect import windows

    for p in sorted(spec.get("suppressions") or [], key=lambda p: str(p.get("key"))):
        if p.get("confirmed", True) is False:
            continue
        if p.get("entity") or p.get("plant") or p.get("metrics"):
            continue
        if any(a <= day <= b for a, b in windows(p, range(day.year - 1, day.year + 2))):
            return str(p.get("short") or p.get("name") or p.get("key"))
    return None


def activity(
    spec: dict[str, Any], points: list[Point], run_date: dt.date, cfg: dict[str, Any]
) -> dict[str, Any] | None:
    """Yesterday's activity against the same weekday over the last weeks: any base flow metric
    with a day value other than zero. None when no metric is read by the day."""
    metrics = spec.get("metrics", {})
    flow = {
        k
        for k, m in metrics.items()
        if m.get("type") == "base"
        and m.get("kind", "flow") == "flow"
        and m.get("status") == "active"
    }
    days = [p for p in points if p.grain == "day" and not p.breakdown and p.metric in flow]
    if not days:
        return None
    active = {p.period for p in days if p.value is not None and p.value != 0}
    start = min(p.period for p in days)
    day = run_date - dt.timedelta(days=1)
    weeks = int(cfg["active_weeks"])
    past = [day - dt.timedelta(weeks=w) for w in range(1, weeks + 1)]
    seen = [d for d in past if d >= start]
    return {
        "day": day.isoformat(),
        "active": day in active,
        "weeks": len(seen),
        "weekday_active": sum(1 for d in seen if d in active),
        "full_history": len(seen) == weeks,
        "pattern": _quiet_pattern(spec, day),
    }


def freshness(
    spec: dict[str, Any], points: list[Point], run_date: dt.date, cfg: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Each source's newest day of data and whether it has yesterday (#57, owner 14 Nov 2026).
    A day with no rows has no point, so a missing point is "not updated". `late`: not updated on
    a weekday this source normally has rows (Frozen's guard, per source), with no known pattern.
    Flow metrics only: a snapshot can be carried forward."""
    by_src: dict[str, set[str]] = {}
    for k, m in sorted(spec.get("metrics", {}).items()):
        if (
            m.get("type") == "base"
            and m.get("kind", "flow") == "flow"
            and m.get("status") == "active"
            and m.get("source")
        ):
            by_src.setdefault(str(m["source"]), set()).add(k)
    day = run_date - dt.timedelta(days=1)
    weeks = int(cfg["active_weeks"])
    pattern = _quiet_pattern(spec, day)
    out: dict[str, dict[str, Any]] = {}
    for src in sorted(by_src, key=lambda n: (n.lower(), n)):
        days = {
            p.period
            for p in points
            if p.grain == "day" and not p.breakdown and p.metric in by_src[src]
        }
        if not days:
            out[src] = {"newest": None, "updated": None, "late": False}
            continue
        start = min(days)
        seen = [d for d in (day - dt.timedelta(weeks=w) for w in range(1, weeks + 1)) if d >= start]
        had = sum(1 for d in seen if d in days)
        updated = day in days
        usual = len(seen) == weeks and Decimal(had) >= Decimal(len(seen)) * Decimal(
            str(cfg["active_share"])
        )
        out[src] = {
            "newest": max(days).isoformat(),
            "updated": updated,
            "late": not updated and usual and pattern is None,
        }
    return out


def _goal_states(goals: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Goals whose whole band misses (Thunderstorm), and amber ones: off the point, but the target
    is inside the band (Overcast)."""
    miss, amber = [], []
    for g in sorted(goals, key=lambda g: (str(g["by"]), str(g["key"]))):
        if g.get("on_track") or g.get("lower") is None or g.get("upper") is None:
            continue
        t, lo, hi = _d(g["target"]), _d(g["lower"]), _d(g["upper"])
        at_least = g.get("direction", "at_least") == "at_least"
        if (at_least and hi < t) or (not at_least and lo > t):
            miss.append(g)
        else:
            amber.append(g)
    return miss, amber


def _value(unit: str | None) -> Any:
    """How a goal's numbers read: money in ₹, else a plain number."""
    if unit == "inr":
        return money

    def plain(v: Any) -> str:
        return fmt.num(_d(v), decimals=1)

    return plain


# --- the scene ---------------------------------------------------------------------------------


def make(
    spec: dict[str, Any],
    run_date: dt.date,
    *,
    kind: str,
    first_brief: bool,
    findings: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    outputs_doc: dict[str, Any],
    points: list[Point],
    ahead: dict[str, Any] | None = None,
    watches: list[dict[str, Any]] | None = None,
    watch_status: list[dict[str, Any]] | None = None,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The run's weather: the scene, its line, the rule that picked it, the facts it used, the
    thresholds read, and the outlook for the next four weeks (part 1b). `previous`: the last
    brief run's sources and goal states (`previous_of`), None on the first brief."""
    from othor.briefs import outlook

    doc = _today(
        spec,
        run_date,
        kind=kind,
        first_brief=first_brief,
        findings=findings,
        goals=goals,
        outputs_doc=outputs_doc,
        points=points,
        previous=previous,
    )
    rev = revenue(spec, outputs_doc.get("baselines") or {})
    doc["outlook"] = outlook.make(
        spec,
        run_date,
        settings(spec),
        ahead=ahead,
        goals=goals,
        findings=findings,
        points=points,
        watches=watches or [],
        watch_status=watch_status or [],
        month=None if rev is None else rev[1],
    )
    return doc


def _today(
    spec: dict[str, Any],
    run_date: dt.date,
    *,
    kind: str,
    first_brief: bool,
    findings: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    outputs_doc: dict[str, Any],
    points: list[Point],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    cfg = settings(spec)
    names = {k: str(m.get("name") or k) for k, m in spec.get("metrics", {}).items()}
    cov = outputs_doc.get("coverage") or {}
    sources = cov.get("sources") or {}
    unread = sorted(n for n, meta in sources.items() if meta.get("status") == "stale")
    sweep = outputs_doc.get("sweep") or {}
    checks = sweep.get("coverage") or []
    ran = sum(1 for c in checks if c.get("status") in ("ran", "partly"))
    rev = revenue(spec, outputs_doc.get("baselines") or {})

    def share(key: str) -> Decimal:
        return rev[1] * Decimal(str(cfg[key])) if rev else Decimal(0)

    its = items(findings, spec, run_date)
    material = [i for i in its if i.money >= share("material_share")] if rev else list(its)
    minor = [i for i in its if i not in material]
    # rain is money lost or due: running above a band (Heat wave) and unsteadiness (Windy) are
    # their own scenes, never rain
    rain = [
        i
        for i in material
        if i.f.get("detector") not in UNSTEADY
        and not (i.f.get("detector") in HOT and i.f.get("direction") == "up")
    ]
    near = run_date + dt.timedelta(days=int(cfg["near_days"]))
    today = run_date + dt.timedelta(days=int(cfg["today_days"]))
    sudden = run_date - dt.timedelta(days=int(cfg["sudden_days"]))
    miss, amber = _goal_states(goals)
    act = activity(spec, points, run_date, cfg)
    fresh = freshness(spec, points, run_date, cfg)
    late = [n for n, f in fresh.items() if f["late"]]
    history = max(
        (int(b.get("history_days") or 0) for b in (outputs_doc.get("baselines") or {}).values()),
        default=0,
    )
    resolved = [
        f
        for f in findings
        if f.get("status") == "resolved"
        and (f.get("resolved") or {}).get("date") == run_date.isoformat()
    ]
    # since the last brief (part 1b): a source connected, a goal back on track
    new_sources = (
        sorted(set(sources) - set(previous.get("sources") or [])) if previous is not None else []
    )
    was = (previous or {}).get("goals") or {}
    recovered = [
        g
        for g in sorted(goals, key=lambda g: (str(g["by"]), str(g["key"])))
        if g.get("on_track") and was.get(g["key"]) is False
    ]

    facts: dict[str, Any] = {
        "items": len(its),
        "material": len(material),
        "at_stake": str(sum((i.money for i in rain), Decimal(0))),
        "checks": len(checks),
        "checks_ran": ran,
        "revenue": None if rev is None else {"metric": rev[0], "month": str(rev[1])},
        "unread": unread,
        "resolved": len(resolved),
        "history_days": history,
        "activity": act,
        "sources_fresh": fresh,
        "run_kind": kind,
        "new_sources": new_sources,
        "recovered": [g["key"] for g in recovered],
    }

    def scene(
        key: str, line: str, why: list[str], subset: list[Item] | None = None, **more: Any
    ) -> dict[str, Any]:
        s = SCENES[key]
        # the nearest dated item behind the scene: "How this was picked" links to it
        dated = sorted(
            (i for i in (subset or []) if i.date is not None),
            key=lambda i: (i.date, str(i.f.get("fingerprint"))),
        )
        nearest = (
            {"date": dated[0].date.isoformat(), "fingerprint": dated[0].f.get("fingerprint")}  # type: ignore[union-attr]
            if dated
            else None
        )
        return {
            "format": FORMAT,
            "scene": key,
            "file": s.file,
            "name": s.name,
            "label": s.label,
            "text": s.text,
            "panel": s.panel,
            "line": line,
            "why": why,
            "rule": rule(key, cfg),
            "facts": {**facts, "nearest": nearest, **more},
            "settings": {k: cfg[k] for k in sorted(cfg)},
        }

    def money_why(xs: list[Item]) -> list[str]:
        out = [_count(len(xs), "item"), money(sum((i.money for i in xs), Decimal(0)))]
        dates = sorted(i.date for i in xs if i.date is not None)
        if dates:
            out.append(f"nearest {fmt.day_month(dates[0])}")
        return out

    # 14 · No signal: a source couldn't be read
    if unread:
        stale = [
            names.get(m, m)
            for m in cov.get("stale_metrics") or []
            if m in (spec.get("order") or [])
        ]
        srcs = _and(unread)
        if stale:
            shown = stale[:2] + ([_count(len(stale) - 2, "more metric")] if len(stale) > 2 else [])
            line = LINES["no_signal"].format(sources=srcs, metrics=_and([s.lower() for s in shown]))
        else:
            line = LINES["no_signal_none"].format(sources=srcs)
        return scene("no_signal", line, [f"{_count(len(unread), 'source')} not read: {srcs}"])

    # 13 · Sunrise: the first brief, or a source connected since the last brief
    if first_brief:
        return scene(
            "sunrise",
            LINES["sunrise"],
            [_count(len(its), "item"), f"{fmt.num(ran)} of {fmt.num(len(checks))} checks ran"],
        )
    if new_sources:
        srcs = _and(new_sources)
        return scene(
            "sunrise",
            LINES["sunrise_source"].format(sources=srcs),
            [f"{_count(len(new_sources), 'new source')}: {srcs}", _count(len(its), "item")],
        )

    # 14 · No signal, #57: read fine, but no rows for yesterday where there normally are
    if late:
        if len(late) == 1:
            line = LINES["no_signal_not_updated"].format(
                source=late[0],
                day=fmt.date_short(dt.date.fromisoformat(fresh[late[0]]["newest"])),
            )
        else:
            line = LINES["no_signal_not_updated_few"].format(sources=_and(late))
        return scene(
            "no_signal", line, [f"{_count(len(late), 'source')} not updated: {_and(late)}"]
        )

    # 15 · Frozen: no activity on a normally active day, every source read, no known pattern
    if act and not act["active"] and act["full_history"] and not act["pattern"]:
        k, n = act["weekday_active"], act["weeks"]
        if n and Decimal(k) >= Decimal(n) * Decimal(str(cfg["active_share"])):
            day = dt.date.fromisoformat(act["day"])
            return scene(
                "frozen",
                LINES["frozen"].format(day=fmt.date_short(day)),
                [
                    f"no activity on {fmt.date_short(day)}",
                    f"{day:%A}s active in {fmt.num(k)} of the last {fmt.num(n)} weeks",
                ],
            )

    # 07 · Thunderstorm: a big item due within days, or a goal due within 30 days whose whole band
    # misses (one further out is Heavy rain: "only when the data says today")
    storm = [i for i in rain if i.date is not None and i.date <= today]
    storm = [i for i in storm if not rev or i.money >= share("big_share")]
    if storm:
        top = storm[0]
        return scene(
            "thunderstorm",
            LINES["thunderstorm"].format(title=_title(top.f)),
            money_why(storm),
            storm,
            top=top.f.get("fingerprint"),
        )

    def goal_miss(key: str, g: dict[str, Any]) -> dict[str, Any]:
        val = _value((spec.get("metrics", {}).get(g["metric"]) or {}).get("unit"))
        at_least = g.get("direction", "at_least") == "at_least"
        metric = names.get(g["metric"], g["metric"])
        by = fmt.day_month(dt.date.fromisoformat(g["by"]))
        return scene(
            key,
            LINES["goal_miss"].format(
                edge="top" if at_least else "bottom",
                metric=metric if metric.split()[0].isupper() else metric[:1].lower() + metric[1:],
                bound=val(g["upper"] if at_least else g["lower"]),
                target=val(g["target"]),
                by=by,
            ),
            [
                f"{metric}: band {val(g['lower'])}–{val(g['upper'])}",
                f"goal {val(g['target'])} by {by}",
            ],
            goal=g["key"],
        )

    soon = [g for g in miss if dt.date.fromisoformat(g["by"]) <= near]
    if soon:
        return goal_miss("thunderstorm", soon[0])

    # 08 · Hail: a step down or a broken link that started in the last few days
    hail = [
        i
        for i in rain
        if i.f.get("detector") in SUDDEN
        and i.f.get("direction") == "down"
        and dt.date.fromisoformat(str((i.f.get("window") or [run_date.isoformat()])[0])) >= sudden
    ]
    if hail:
        top = hail[0]
        start = dt.date.fromisoformat(str(top.f["window"][0]))
        return scene(
            "hail",
            LINES["hail"].format(title=_title(top.f), day=fmt.day_month(start)),
            [*money_why(hail), f"started {fmt.day_month(start)}"],
            hail,
            top=top.f.get("fingerprint"),
        )

    # 06 · Heavy rain: material items due within 30 days, or a large share of revenue at stake
    due = [i for i in rain if i.date is not None and i.date <= near]
    total = sum((i.money for i in rain), Decimal(0))
    if due or (rev and rain and total >= share("large_share")):
        dates = sorted(i.date for i in rain if i.date is not None)
        what = _count(len(rain), "item")
        if due:
            line = LINES["heavy_rain"].format(
                money=money(total), count=what, nearest=fmt.day_month(dates[0])
            )
        else:
            line = LINES["heavy_rain_share"].format(money=money(total), count=what)
        return scene("heavy_rain", line, money_why(rain), rain)
    if miss:
        return goal_miss("heavy_rain", miss[0])

    # 10 · Heat wave: running above its forecast band
    hot = [i for i in material if i.f.get("detector") in HOT and i.f.get("direction") == "up"]
    if hot:
        top = hot[0]
        return scene(
            "heat_wave",
            LINES["heat_wave"].format(title=_title(top.f)),
            money_why(hot),
            hot,
            top=top.f.get("fingerprint"),
        )

    # 09 · Windy: unsteady, no loss yet
    windy = [i for i in its if i.f.get("detector") in UNSTEADY]
    if windy:
        top = windy[0]
        return scene(
            "windy",
            LINES["windy"].format(title=_title(top.f)),
            money_why(windy),
            windy,
            top=top.f.get("fingerprint"),
        )

    # 05 · Light rain: material items, none due within 30 days
    if rain:
        top = rain[0]
        line = (
            LINES["light_rain"].format(title=_title(top.f))
            if len(rain) == 1
            else LINES["light_rain_more"].format(
                title=_title(top.f), more=_count(len(rain) - 1, "more", "more")
            )
        )
        return scene("light_rain", line, money_why(rain), rain, top=top.f.get("fingerprint"))

    # 04 · Fog: too little history, or most checks couldn't run
    if checks and Decimal(ran) < Decimal(len(checks)) * Decimal(str(cfg["fog_ran_share"])):
        return scene(
            "fog",
            LINES["fog_checks"].format(ran=fmt.num(ran), checks=fmt.num(len(checks))),
            [f"{fmt.num(ran)} of {fmt.num(len(checks))} checks ran"],
        )
    if history < int(cfg["fog_history_days"]):
        return scene(
            "fog",
            LINES["fog_history"].format(days=fmt.num(history)),
            [f"{fmt.num(history)} days of history"],
        )

    # 03 · Overcast: a goal amber, or several minor items
    if amber:
        g = amber[0]
        unit = (spec.get("metrics", {}).get(g["metric"]) or {}).get("unit")
        val = _value(unit)
        metric = names.get(g["metric"], g["metric"])
        lower = metric if metric.split()[0].isupper() else metric[:1].lower() + metric[1:]
        return scene(
            "overcast",
            LINES["overcast_goal"].format(
                metric=lower, projection=val(g["projection"]), target=val(g["target"])
            ),
            [f"{metric}: projected {val(g['projection'])}", f"target {val(g['target'])}"],
            goal=g["key"],
        )
    if len(minor) >= int(cfg["several"]):
        return scene(
            "overcast",
            LINES["overcast"].format(count=_cap(_count(len(minor), "minor item"))),
            money_why(minor),
            minor,
        )

    # 02 · Partly cloudy: one or two small items
    if minor:
        return scene(
            "partly_cloudy",
            LINES["partly_cloudy"].format(count=_cap(_count(len(minor), "small thing"))),
            money_why(minor),
            minor,
        )

    # 11 · Rainbow: findings resolved tonight, or a goal back on track since the last brief
    if resolved:
        return scene(
            "rainbow",
            LINES["rainbow"].format(count=_cap(_count(len(resolved), "finding")) + " resolved"),
            [_count(len(resolved), "finding") + " resolved"],
        )
    if recovered:
        g = recovered[0]
        name = str(g.get("name") or g["key"])
        return scene(
            "rainbow",
            LINES["rainbow_goal"].format(goal=name),
            [f"back on track: {name}"],
            goal=g["key"],
        )

    # 12 · Clear night: no activity on a day that's normally quiet, or under a known pattern
    if act and not act["active"]:
        day = dt.date.fromisoformat(act["day"])
        k, n = act["weekday_active"], act["weeks"]
        if act["pattern"]:
            return scene(
                "clear_night",
                LINES["clear_night_pattern"].format(
                    day=fmt.date_short(day), pattern=act["pattern"]
                ),
                [f"no activity on {fmt.date_short(day)}", f"known pattern: {act['pattern']}"],
            )
        if act["full_history"] and Decimal(k) < Decimal(n) * Decimal(str(cfg["active_share"])):
            return scene(
                "clear_night",
                LINES["clear_night"].format(day=fmt.date_short(day), weekday=f"{day:%A}"),
                [
                    f"no activity on {fmt.date_short(day)}",
                    f"{day:%A}s active in {fmt.num(k)} of the last {fmt.num(n)} weeks",
                ],
            )

    # 01 · Clear
    if checks and ran == len(checks):
        line = LINES["clear"].format(checks=fmt.num(len(checks)))
    else:
        line = LINES["clear_some"].format(ran=fmt.num(ran), checks=fmt.num(len(checks)))
    return scene(
        "clear", line, [f"{fmt.num(ran)} of {fmt.num(len(checks))} checks ran", "no items"]
    )


AHEAD = "forecasts_ahead.json"  # the forecast step's weeks ahead (engine.orchestrator.AHEAD)


def previous_of(files: RunFiles) -> dict[str, Any]:
    """What tonight's weather compares with in the last brief run's sealed files: the sources it
    read and each goal's state (part 1b: a new source → Sunrise; a goal back on track →
    Recovering)."""
    doc = read_json(files.outputs_json) or {}
    goals = (read_json(files.outputs / "forecasts.json") or {}).get("goals") or []
    return {
        "sources": sorted(((doc.get("coverage") or {}).get("sources") or {}).keys()),
        "goals": {str(g["key"]): bool(g.get("on_track")) for g in goals},
    }


def from_files(
    files: RunFiles,
    spec: dict[str, Any],
    run_date: dt.date,
    *,
    kind: str,
    first_brief: bool,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The run's brief step: from the run's own sealed outputs (and its inputs' watches)."""
    return make(
        spec,
        run_date,
        kind=kind,
        first_brief=first_brief,
        findings=read_json(files.outputs / "findings.json") or [],
        goals=(read_json(files.outputs / "forecasts.json") or {}).get("goals") or [],
        outputs_doc=read_json(files.outputs_json) or {},
        points=read_points(files.series),
        ahead=read_json(files.outputs / AHEAD),
        watches=(read_json(files.inputs / "context.json") or {}).get("watches") or [],
        watch_status=read_json(files.outputs / "watches.json") or [],
        previous=previous,
    )


def stored(files: RunFiles) -> dict[str, Any] | None:
    """A sealed run's weather, as written at its brief step (None before v0.12 or on a run that
    makes no brief)."""
    doc = read_json(files.outputs / WEATHER)
    return doc if isinstance(doc, dict) and doc.get("format") == FORMAT else None


# --- each brief's own weather (part 1c) --------------------------------------------------------

# in the run's outputs, next to weather.json, which stays as it was (the company's, on Home)
BRIEFS = "weather_briefs.json"


def scoped(
    spec: dict[str, Any],
    sc: dict[str, Any],
    *,
    findings: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    ahead: dict[str, Any] | None,
    watches: list[dict[str, Any]],
    watch_status: list[dict[str, Any]],
) -> dict[str, Any]:
    """What a brief's weather reads: only what is in its scope (findings by `finding_in`; goals,
    watches and the weekly forecasts by `metric_in`). Source health, the checks, history and
    activity stay company-wide (they are the run's, not a metric's). An unscoped brief keeps
    everything, so its weather is the company's."""
    from othor.briefs import scope

    kept = [w for w in watches if scope.watch_in(spec, sc, w)]
    ids = {w.get("id") for w in kept}
    rows = [
        r for r in (ahead or {}).get("rows") or [] if scope.metric_in(spec, sc, r.get("metric"))
    ]
    return {
        "findings": [f for f in findings if scope.finding_in(spec, sc, f)],
        "goals": [g for g in goals if scope.goal_in(spec, sc, g)],
        "ahead": None if ahead is None else {**ahead, "rows": rows},
        "watches": kept,
        "watch_status": [w for w in watch_status if w.get("id") in ids],
    }


def for_briefs(
    spec: dict[str, Any],
    run_date: dt.date,
    versions: list[dict[str, Any]],
    *,
    kind: str,
    first_brief: bool,
    findings: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    outputs_doc: dict[str, Any],
    points: list[Point],
    ahead: dict[str, Any] | None = None,
    watches: list[dict[str, Any]] | None = None,
    watch_status: list[dict[str, Any]] | None = None,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Each active brief's weather and outlook (part 1c): the same scoring as the company's, over
    what is in the brief's scope only. Keyed by brief id, with the version it was made for."""
    out: dict[str, Any] = {}
    for v in sorted(versions, key=lambda v: str(v["id"])):
        sc = v.get("scope") or {}
        mine = scoped(
            spec,
            sc,
            findings=findings,
            goals=goals,
            ahead=ahead,
            watches=watches or [],
            watch_status=watch_status or [],
        )
        out[str(v["id"])] = {
            "version": v.get("number"),
            "scope": {
                "metrics": sc.get("metrics"),
                "exclude": sc.get("exclude") or {},
                # only when chosen (Phase 0 §8), so a brief without areas keeps its bytes
                **({"areas": sc["areas"]} if sc.get("areas") is not None else {}),
            },
            "weather": make(
                spec,
                run_date,
                kind=kind,
                first_brief=first_brief,
                outputs_doc=outputs_doc,
                points=points,
                previous=previous,
                **mine,
            ),
        }
    return {"format": FORMAT, "briefs": out}


def briefs_from_files(
    files: RunFiles,
    spec: dict[str, Any],
    run_date: dt.date,
    *,
    kind: str,
    first_brief: bool,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The run's brief step: each brief's weather from the run's own sealed outputs."""
    context = read_json(files.inputs / "context.json") or {}
    return for_briefs(
        spec,
        run_date,
        context.get("briefs") or [],
        kind=kind,
        first_brief=first_brief,
        findings=read_json(files.outputs / "findings.json") or [],
        goals=(read_json(files.outputs / "forecasts.json") or {}).get("goals") or [],
        outputs_doc=read_json(files.outputs_json) or {},
        points=read_points(files.series),
        ahead=read_json(files.outputs / AHEAD),
        watches=context.get("watches") or [],
        watch_status=read_json(files.outputs / "watches.json") or [],
        previous=previous,
    )


def stored_brief(files: RunFiles, brief_id: Any) -> dict[str, Any] | None:
    """A brief's own entry in a sealed run (`version`, `scope`, `weather`): None for a run sealed
    before part 1c, or a brief the run didn't make an issue for."""
    doc = read_json(files.outputs / BRIEFS)
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        return None
    got = (doc.get("briefs") or {}).get(str(brief_id))
    if not isinstance(got, dict) or got.get("weather", {}).get("scene") not in SCENES:
        return None
    return got
