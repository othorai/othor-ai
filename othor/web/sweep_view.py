# SPDX-License-Identifier: Apache-2.0
"""View models for Sweep (boards 5a–5f, 10i) and the finding panel (5b, 5c, 4f–4f3, 10p).

Everything shown comes from one run: its `finding` rows, its outputs (coverage, every detector
result), its steps and queries, the watch table. What the owner did since the run (snoozes and
mutes) is applied by `findings.overlay`, the one function every count reads. Numbers are
formatted here or in the templates through `othor.core.fmt`; nothing is computed from a model.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.charts.spec import Annotation, ChartSpec, Period, Series, Unit
from othor.connectors.models import RunQuery, Source
from othor.core import features, fmt
from othor.core.config import Settings
from othor.core.fmt import lower_first
from othor.core.models import Event
from othor.engine import findings as fnd
from othor.engine import holds, sweep_text, watches
from othor.engine.detectors import REGISTRY, core
from othor.engine.detectors.base import SeriesIndex
from othor.engine.models import DETECTING_KINDS, Finding, Proposal, Run, RunStep, Watch
from othor.engine.store import RunFiles, read_json
from othor.ontology.models import Spec

RUNS_SHOWN = 7
STORE = "Othor AI’s analytics store, from the run’s stored series"
FULL_HISTORY_DAYS = 28  # changepoint and variance on a snapshot number (docs/04 §3)
# What each detector's test statistic measures, for the Test tab (4f). Read from the result;
# the words only name it.
STATISTIC: dict[str, tuple[str, str]] = {
    "net_hiding_gross": (
        "Offset ratio",
        "the parts’ moves against each other, over the net change",
    ),
    "changepoint": ("Test statistic", "the size of the shift against the series’ noise"),
    "concentration": ("Trend t", "the slope of the share over its standard error"),
    "cross_source": ("Unmatched", "records in one source with no match in the other"),
    "forecast_breach": ("z", "how far outside the forecast, in standard errors"),
}


def _months(text: str) -> str:
    """'2026-09 to 2026-10' in stored text → 'Sep 2026 to Oct 2026' for the page."""
    import re

    return re.sub(
        r"\b(\d{4})-(\d{2})\b",
        lambda m: f"{dt.date(int(m[1]), int(m[2]), 1):%b} {m[1]}",
        text,
    )


METHOD = {
    "net_hiding_gross": "Decomposition across a breakdown. Flags offsetting slices that each "
    "exceed the materiality floor while the total stays inside its usual range.",
    "changepoint": "PELT on the weekly series, then a Welch test of the weeks after against the "
    "weeks before the break.",
    "rising_variance": "F-test of the recent spread against the 12 weeks before, per breakdown "
    "value, Bonferroni across them.",
    "broken_relationship": "Chow test: does the relationship before the break predict the weeks "
    "after it?",
    "lead_lag": "Cross-correlation at each lag, against a seeded permutation null.",
    "concentration": "The top customers' share of the total against its own history.",
    "missing_records": "Each customer's invoice intervals as a log-normal; a gap beyond its "
    "95th percentile, or active customers silent for 90 days or more.",
    "process_artefact": "Binomial test of the events just under a threshold against just above.",
    "cross_source": "Keys on one side without a match on the other after the rule's lag; or the "
    "same measure from two sources compared period by period.",
    "bimodal_average": "Hartigan's dip test, then a two-group mixture chosen by BIC.",
}


BOARD_ORDER = (
    "net_hiding_gross",
    "changepoint",
    "concentration",
    "forecast_breach",
    "rising_variance",
    "broken_relationship",
    "cross_source",
    "missing_records",
    "lead_lag",
    "bimodal_average",
    "process_artefact",
)


def shipped() -> list[str]:
    """The detectors the product runs tonight (Forecast breach from M7, 5 Nov 2026), in the
    boards' order (5f, 10i)."""
    on = {
        d.name
        for d in core().values()  # a plug-in's checks show in their own places
        if d.enabled or (d.name == "forecast_breach" and features.FORECASTS)
    }
    return [n for n in BOARD_ORDER if n in on] + sorted(on - set(BOARD_ORDER))


def _new(rv: RunView, detector: str) -> int:
    return sum(
        1
        for f in rv.shown
        if f["detector"] == detector and f["status"] == "open" and f["change"] == "New"
    )


# --- the run being shown --------------------------------------------------------------------


@dataclass
class RunView:
    run: Run
    spec: Any  # the Spec row
    files: RunFiles
    doc: dict[str, Any]  # outputs.json["sweep"]
    results: list[dict[str, Any]]
    stored: list[dict[str, Any]]  # findings as the run sealed them
    shown: list[dict[str, Any]]  # with the snoozes and mutes active now
    counts: dict[str, int]
    today: dt.date
    series: SeriesIndex | None = None

    @property
    def compiled(self) -> dict[str, Any]:
        return dict(self.spec.compiled)


# what a run was, in the owner's words (the runs menu, #39)
RUN_KIND = {"dry": "first run", "nightly": "nightly", "rerun": "run again", "manual": "read now"}


def detecting_runs(s: Session, n: int = RUNS_SHOWN) -> list[Run]:
    return list(
        s.scalars(
            sa.select(Run)
            .where(Run.status.in_(("done", "partial")), Run.kind.in_(DETECTING_KINDS))
            .order_by(Run.number.desc())
            .limit(n)
        )
    )


def load(s: Session, settings: Settings, run: Run, today: dt.date) -> RunView | None:
    files = RunFiles(settings.runs_dir(run.company_id), run.id)
    outputs = read_json(files.outputs_json) or {}
    doc = outputs.get("sweep")
    if not doc:
        return None
    spec = s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one()
    rows = s.scalars(sa.select(Finding).where(Finding.run_id == run.id)).all()
    stored = sorted((dict(r.body) for r in rows), key=_order)
    snoozes, mutes = holds.active(s, today)
    shown = fnd.overlay(stored, snoozes, mutes, today)
    live = watch_rows(s, doc)
    counts = fnd.view_counts(shown, live, doc["counts"])
    results = read_json(files.outputs / "detection.json") or []
    return RunView(run, spec, files, doc, results, stored, shown, counts, today)


def _order(f: dict[str, Any]) -> tuple[Any, ...]:
    return (f["change"] != "New", -Decimal(f.get("money") or 0), f["fingerprint"])


def series_of(rv: RunView) -> SeriesIndex:
    if rv.series is None:
        from othor.web import run_cache

        rv.series = run_cache.series_index(rv.files.series)  # built once per run (M15.1)
    return rv.series


# --- 5a: summary, run line, tabs --------------------------------------------------------------


def summary(rv: RunView, s: Session) -> dict[str, Any]:
    spec = rv.compiled
    metrics = [k for k in spec.get("order", []) if spec["metrics"][k].get("listed") is not False]
    breakdowns = {b for k in metrics for b in spec["metrics"][k].get("breakdowns") or []}
    pairs = {(k, b) for k in metrics for b in spec["metrics"][k].get("breakdowns") or []}
    cov = rv.doc.get("coverage", [])
    nothing = [c for c in cov if c["detector"] in shipped() and c["status"] != "could_not_run"]
    nothing = [c for c in nothing if not _found(rv, c["detector"])]
    cnr = [c for c in cov if c["detector"] in shipped() and c["status"] == "could_not_run"]
    from othor.core.localtime import local_now
    from othor.core.models import Company

    company = s.scalars(sa.select(Company).where(Company.id == rv.run.company_id)).one()
    start = local_now(company, rv.run.started_at)
    parts = [f"Run #{rv.run.number:04d}", fmt.clock(start.time())]
    c = rv.counts
    parts.append(f"{fmt.num(c['new'])} new")
    if c["needs_confirmation"]:
        parts.append(f"{fmt.num(c['needs_confirmation'])} needs confirmation")
    if nothing:
        parts.append(f"{fmt.num(len(nothing))} categories found nothing")
    # the same things the Coverage card lists under "Could not run": detectors, and the numbers
    # that need a source (5a, M12b)
    waiting = [
        k
        for k, m in spec.get("metrics", {}).items()
        if m.get("status") == "needs_source" and m.get("listed") is not False
    ]
    if cnr or waiting:
        parts.append(f"{fmt.num(len(cnr) + len(waiting))} could not run")
    rep = last_replay(s, rv.run.id)
    if rep is not None and rep.get("ok"):
        parts.append("replay identical")  # 5f
    del breakdowns
    return {
        "sources": len(spec.get("sources", {})),
        "metrics": len(metrics),
        "breakdowns": len(pairs),
        "tests": rv.doc.get("tests", 0),
        "fdr": fmt.pct(q := Decimal(str(spec.get("detectors", {}).get("fdr_q", "0.01")))),
        # the same rate in plain words (non-negotiable 10): "at most 1 in 100 could be chance"
        "chance": fmt.num(int((1 / q).to_integral_value())) if q > 0 else "",
        "line": " · ".join(parts),
        "date": rv.run.run_date,
    }


def _found(rv: RunView, detector: str) -> int:
    return sum(1 for f in rv.shown if f["detector"] == detector and f["status"] == "open")


def tabs(rv: RunView, base: str) -> list[tuple[str, str]]:
    c = rv.counts
    return [
        (f"Findings · {fmt.num(c['findings'])}", f"{base}"),
        (f"Watching · {fmt.num(c['watching'])}", f"{base}&tab=watching"),
        (f"Suppressed · {fmt.num(c['suppressed'])}", f"{base}&tab=suppressed"),
        (f"Resolved · {fmt.num(c['resolved'])}", f"{base}&tab=resolved"),
    ]


# --- cards --------------------------------------------------------------------------------------


def chart_of(f: dict[str, Any]) -> ChartSpec | None:
    from othor.engine.finding_charts import load

    raw = f.get("chart")
    return load(raw) if raw else None


def card(f: dict[str, Any], number: int) -> dict[str, Any]:
    w = f.get("text") or {}
    return {
        "number": f"{number:02d}",
        "fp": f["fingerprint"],
        "label": w.get("label", f["detector"]),
        "money": w.get("money", ""),
        "state": f["state"],
        "confidence": f.get("confidence"),
        "change": _change(f["change"]),
        "title": w.get("title", ""),
        "line": w.get("line", ""),
        "chart": chart_of(f),
        "rows": _card_rows(w.get("rows")),
        "why": w.get("why_unseen"),
        "question": w.get("question"),
        "home_note": _home_note(f, w),
        "detector": f["detector"],
    }


def _change(tag: str) -> tuple[str, int | None]:
    if tag == "Came back":  # resolved within the week, firing again (findings.CAME_BACK)
        return ("back", None)
    if tag.startswith("Open · day "):
        return ("open", int(tag.rsplit(" ", 1)[1]))
    return (tag.lower(), None)


def _rows(r: dict[str, Any] | None, n: int) -> dict[str, Any] | None:
    if not r:
        return None
    rows = r["rows"][:n]
    keep = [i for i in range(len(r["headers"])) if any(str(x[i]) for x in rows)]
    return {
        **r,
        "headers": [r["headers"][i] for i in keep],
        "rows": [[x[i] for i in keep] for x in rows],
        "more": max(int(r.get("total", 0)) - n, 0),
    }


def _card_rows(r: dict[str, Any] | None) -> dict[str, Any] | None:
    """The card shows three rows of the first, second and last columns (5a); the panel all."""
    out = _rows(r, 3)
    if not out or len(out["headers"]) <= 3:
        return out
    pick = [0, 1, len(out["headers"]) - 1]
    return {
        **out,
        "headers": [out["headers"][i] for i in pick],
        "rows": [[row[i] for i in pick] for row in out["rows"]],
    }


def _home_note(f: dict[str, Any], w: dict[str, Any]) -> str | None:
    if f["state"] != "needs_confirmation":
        return None
    to = (w.get("question") or {}).get("to") or []
    if not to:
        return None
    first = [o["name"].split(" ")[0] for o in to]
    return "Added to Home as a question for " + " and ".join(first)


def finding_cards(rv: RunView) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(new, open before): the open findings, each group by money."""
    live = [f for f in rv.shown if f["status"] == "open"]
    new = [f for f in live if f["change"] == "New"]
    old = [f for f in live if f["change"] != "New"]

    def by_money(xs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(xs, key=lambda f: (-Decimal(f.get("money") or 0), f["fingerprint"]))

    out_new = [card(f, i + 1) for i, f in enumerate(by_money(new))]
    out_old = [card(f, len(new) + i + 1) for i, f in enumerate(by_money(old))]
    return out_new, out_old


# --- Coverage (5a), detectors checked tonight (5f, 10i) ------------------------------------


def coverage(rv: RunView) -> dict[str, list[dict[str, Any]]]:
    spec = rv.compiled
    cov = {c["detector"]: c for c in rv.doc.get("coverage", []) if c["detector"] in shipped()}
    clean: list[dict[str, Any]] = []
    cnr: list[dict[str, Any]] = []
    supp: list[dict[str, Any]] = []
    for name in shipped():
        c = cov.get(name)
        if c is None:
            continue
        label = sweep_text.LABELS.get(name, name)
        if c["status"] == "could_not_run":
            cnr.append(
                {
                    "label": label,
                    "text": sweep_text.NEEDS.get(c.get("needs") or "", ""),
                    "action": None,
                }
            )
        elif not _found(rv, name):
            clean.append({"label": label, "text": sweep_text.checked(c, 0)})
    for key, m in spec.get("metrics", {}).items():
        if m.get("status") == "needs_source" and m.get("listed") is not False:
            cnr.append(
                {
                    "label": m.get("name") or key,
                    "text": m.get("needs") or "Needs a source",
                    "action": ("Connect a source", "/sources?panel=add"),
                }
            )
    # one item per known pattern, naming the metrics and periods it covered (5 Nov 2026)
    groups: dict[str, dict[str, Any]] = {}
    for r in rv.results:
        why = r.get("reason") or ""
        if not why.startswith("suppressed by "):
            continue
        cov = (r.get("slots") or {}).get("covered") or {}
        g = groups.setdefault(why, {"cov": cov, "metrics": {}})
        g["metrics"].setdefault(r["metric"], []).append(_period_label(r, cov))
    for why, g in groups.items():
        parts = []
        rows = []
        for metric, periods in g["metrics"].items():
            name = (spec["metrics"].get(metric, {}).get("name") or metric).lower()
            uniq = list(dict.fromkeys(p for p in periods if p))
            parts.append(f"{name} ({', '.join(uniq)})" if uniq else name)
            rows.append((name[:1].upper() + name[1:], ", ".join(uniq)))
        when = g["cov"].get("answered_on")
        tail = "known pattern" + (
            f", confirmed {fmt.day_month(dt.date.fromisoformat(when))}" if when else ""
        )
        label = why.removeprefix("suppressed by ")
        # the nights a finding was open before the answer covered it (decided 5 Nov 2026)
        history = [
            "open "
            + fmt.day_range(
                dt.date.fromisoformat(f["covered"]["open_from"]),
                dt.date.fromisoformat(f["covered"]["open_to"]),
            )
            + " before the answer"
            for f in rv.shown
            if f["status"] == "covered"
            and f["covered"].get("by") == label
            and f["covered"].get("open_from")
        ]
        supp.append(
            {
                "label": label,
                "text": " · ".join([", ".join(parts), tail, *dict.fromkeys(history)]),
                "tail": " · ".join([tail, *dict.fromkeys(history)]),
                "metrics": parts,
                # 0.33: how many findings the pattern kept out, and each number with its dates
                "count": sum(len(p) for p in g["metrics"].values()),
                "rows": rows,
            }
        )
    return {"clean": clean, "could_not_run": cnr, "suppressed": supp}


def _period_label(r: dict[str, Any], cov: dict[str, Any]) -> str:
    start = dt.date.fromisoformat(r["window"][0])
    grain = (r.get("slots") or {}).get("grain")
    if grain == "week" or r["detector"] != "forecast_breach":
        kind = "catch-up week" if cov.get("settle") else "week"
        return f"{kind} of {fmt.day_month(start)}" if grain == "week" else fmt.day_month(start)
    return start.strftime("%B")


STATUS = {"ran": "Running", "partly": "Partly", "could_not_run": "Could not run"}
STATUS_KIND = {"ran": "on", "partly": "partly", "could_not_run": "off"}
# what each need asks of the owner, in the table's words (M15.2, owner's design 12 Nov 2026)
NEEDS_SHORT = {
    "entity": "Needs your customer list",
    "reconcile": "Needs to know which records should match",
    "pair": "Needs two numbers that move together",
    "threshold": "Needs an approval limit",
    "distribution": "Needs each order's value",
}


def open_needs(s: Session) -> dict[str, Any]:
    """{need: its question} for the needs questions still waiting (open or set aside)."""
    from othor.ontology.models import Question
    from othor.ontology.questions import NEEDS_QUESTIONS, _fingerprint

    fps = {_fingerprint("needs", n): n for n in NEEDS_QUESTIONS}
    rows = s.scalars(
        sa.select(Question).where(Question.fingerprint.in_(fps), Question.status == "open")
    ).all()
    return {fps[q.fingerprint]: q for q in rows}


def detector_rows(rv: RunView, s: Session | None = None) -> list[dict[str, Any]]:
    """The detectors table (M15.2; was 5f, 10i): one row per shipped detector, in plain words.
    A check waiting on the owner links to its question, answered in place on Sweep; one waiting
    on history says when it turns on by itself."""
    cov = {c["detector"]: c for c in rv.doc.get("coverage", [])}
    asked = open_needs(s) if s is not None else {}
    early = first_days(rv, s) if s is not None else None
    full_from = early["full_from"] if early else None
    out = []
    core_names = shipped()
    # a plug-in's check (External Signals' Outside) joins the list on a night it ran (owner,
    # 21 Nov 2026: "make it 12 including external signals")
    names = core_names + sorted(n for n in cov if n not in core_names)
    for name in names:
        c = cov.get(name)
        if c is None:
            continue
        status = STATUS[c["status"]]
        missing, action = "", None
        covers = sweep_text.count(c["checked"], c["unit"])
        if c["status"] == "partly":
            short = max(int(c.get("eligible_total", 0)) - int(c.get("checked", 0)), 0)
            covers = (
                f"{fmt.num(c['checked'])} of {sweep_text.count(c['eligible_total'], c['unit'])}"
            )
            missing = f"{fmt.num(short)} still building history"
            if c.get("notes"):
                missing = "; ".join(c["notes"])
            elif full_from is not None:
                missing += f" · turns on fully around {fmt.day_month(full_from)}"
        elif c["status"] == "could_not_run":
            covers = ""
            need = c.get("needs") or ""
            q = asked.get(need)
            missing = sweep_text.NEEDS.get(need, "")
            if q is not None:  # answered in place on Sweep (owner, 13 Nov 2026)
                missing = NEEDS_SHORT.get(need) or missing
                action = (
                    "Answer 1 question →",
                    f"/sweep?run={rv.run.number}&panel=othor&subject=questions",
                )
        out.append(
            {
                "key": name,
                "label": sweep_text.LABELS.get(name, name),
                "short": sweep_text.short_of(name),
                "catches": sweep_text.CATCHES.get(name)
                or (sweep_text.OUTSIDE_WORDS["catches"] if name == sweep_text.OUTSIDE else ""),
                "status": status,
                "kind": STATUS_KIND[c["status"]],
                "covers": covers,
                "missing": missing,
                "action": action,
                "result": sweep_text.checked(c, _new(rv, name)),
                "plugin": name not in core_names,
            }
        )
    if sweep_text.OUTSIDE not in names:  # always 12: Outside grey where it isn't on (21 Nov)
        out.append(_outside_off())
    return out


def _outside_off() -> dict[str, Any]:
    """External Signals' check on a night it didn't run: with the feature, it waits for a link;
    without it, it says where it comes from."""
    on = sweep_text.has_words(sweep_text.OUTSIDE)
    name = sweep_text.OUTSIDE
    return {
        "key": name,
        "label": sweep_text.LABELS.get(name) or sweep_text.OUTSIDE_WORDS["label"],
        "short": sweep_text.short_of(name) if on else sweep_text.OUTSIDE_WORDS["short"],
        "catches": sweep_text.OUTSIDE_WORDS["catches"],
        "status": "Not on",
        "kind": "off",
        "covers": "",
        "missing": "Needs an outside number linked to one of yours"
        if on
        else "Comes with External Signals (Enterprise Edition)",
        "action": ("Link one →", "/signals") if on else None,
        "result": "",
        "plugin": True,
    }


def strip(rv: RunView, s: Session) -> dict[str, Any]:
    """The status strip (M15.2): "Total 11 detectors. 3 activated. 8 inactive. 0 suppressed." and
    one square bar per detector, its short name under it and a card behind it (M15.7b);
    suppressed is the Suppressed tab's count."""
    rows = detector_rows(rv, s)
    asked = open_needs(s)
    unlock = sum(1 for r in rows if r["action"] and not r.get("plugin"))
    return {
        "total": len(rows),
        "active": sum(1 for r in rows if r["kind"] != "off"),
        "inactive": sum(1 for r in rows if r["kind"] == "off"),
        "suppressed": rv.counts["suppressed"],
        "segments": rows,
        "questions": len(asked),
        "unlock": unlock,
    }


def strip_info(st: dict[str, Any]) -> str:
    """The checks' (i): what a check is, what couldn't run means, and what answering would do."""
    text = (
        "Each check is one way Sweep tests your numbers every night. Couldn’t run means it could "
        "not run tonight: it needs something from you (a customer list, two numbers that move "
        "together, an approval limit) or more history. Suppressed findings matched a pattern "
        "you told Othor AI about, or you snoozed or muted them."
    )
    if st["questions"]:
        n = st["questions"]
        text += (
            f" Answer {fmt.num(n)} question{'s' if n != 1 else ''} to turn on up to "
            f"{fmt.num(st['unlock'])} more."
        )
    return text


def finding_rows(rv: RunView, s: Session) -> list[dict[str, Any]]:
    """Sweep's findings (M15.2): every finding open after the run, one column, in the brief
    ranking's order (money, then how soon it bites), each with the brief it is in."""
    from othor.briefs.rank import score

    spec = rv.compiled
    live = [f for f in rv.shown if f["status"] == "open"]
    live.sort(key=lambda f: (-score(f, spec, rv.run.run_date), f["fingerprint"]))
    briefs: dict[str, str] = {}
    if features.BRIEFS:
        from othor.web import brief_view as bvw

        briefs = bvw.fp_briefs(s, rv.run)
    out = []
    for i, f in enumerate(live):
        c = card(f, i + 1)
        name = briefs.get(f["fingerprint"])
        c["brief"] = (
            None if not name else name if name.lower().endswith("brief") else f"{name} brief"
        )
        out.append(c)
    return out


def _history(rv: RunView) -> tuple[list[dict[str, Any]], list[dt.date]]:
    """(the listed snapshot numbers still under 28 days of points, the first real snapshot of
    each): what "metrics with enough history" and the history note count (10i, M15.2)."""
    spec = rv.compiled
    series = series_of(rv)
    as_of = rv.run.run_date - dt.timedelta(days=1)
    snaps = [k for k in spec.get("order", []) if spec["metrics"][k].get("kind") == "snapshot"]
    real_start: list[dt.date] = []
    short = []
    for key in snaps:
        sr = series.get(key, "day")
        if sr is None:
            continue
        real = [p for p, rec in zip(sr.periods, sr.reconstructed, strict=True) if not rec]
        if real:
            real_start.append(min(real))
        days = (as_of - min(sr.periods)).days + 1 if sr.periods else 0
        m = spec["metrics"][key]
        if days < FULL_HISTORY_DAYS and m.get("listed") is not False:
            start = min(sr.periods)
            short.append(
                {
                    "name": m.get("name") or key,
                    "why": _why_short(m),
                    "history": f"{fmt.num(days)} day{'s' if days != 1 else ''}",
                    "ready": start + dt.timedelta(days=FULL_HISTORY_DAYS),
                }
            )
    return short, real_start


def history_counts(rv: RunView) -> dict[str, int]:
    """The first of Sweep's three numbers: metrics with enough history (M15.2), every night."""
    spec = rv.compiled
    short, _ = _history(rv)
    listed = [k for k in spec.get("order", []) if spec["metrics"][k].get("listed") is not False]
    return {"ok": len(listed) - len(short), "all": len(listed)}


def first_days(rv: RunView, s: Session) -> dict[str, Any] | None:
    """10i: while the company's own nightly snapshots are young. "Day N" counts from its first
    real (not back-filled) snapshot; a number is still building history while it has under 28
    days of points (back-filled ones count: they are graded Assessed at most, docs/04)."""
    short, real_start = _history(rv)
    if not real_start:
        return None
    as_of = rv.run.run_date - dt.timedelta(days=1)
    day = (as_of - min(real_start)).days + 1
    cov = [c for c in rv.doc.get("coverage", []) if c["detector"] in shipped()]
    partly = [c for c in cov if c["status"] == "partly"]
    if day > FULL_HISTORY_DAYS or not (short or partly):
        return None
    first = min(real_start)
    full = max((x["ready"] for x in short), default=first + dt.timedelta(days=FULL_HISTORY_DAYS))
    return {"day": day, "short": short, "full_from": full, "partly": len(partly)}


def _why_short(m: dict[str, Any]) -> str:
    label = str(m.get("source_label") or "")
    if "derived" in label:
        return "Uses a snapshot number"
    if label.startswith("Google Sheets"):
        return "The sheet keeps today only"
    return "Its source keeps the current state only"


# --- Watching, Suppressed, Resolved -------------------------------------------------------


def watch_rows(s: Session, doc: dict[str, Any]) -> list[dict[str, Any]]:
    """The watches the run evaluated, still active now (a stopped one leaves at once)."""
    active = {str(w.id): w for w in s.scalars(sa.select(Watch).where(Watch.stopped_at.is_(None)))}
    out = []
    for w in doc.get("watches", []):
        row = active.get(w["id"])
        if row is None:
            continue
        out.append({**w, "row": row})
    return out


def watching(rv: RunView, s: Session) -> list[dict[str, Any]]:
    out = []
    for w in watch_rows(s, rv.doc):
        unit = (w["row"].indicator or {}).get("kind")
        out.append(
            {
                "id": w["id"],
                "label": w["label"],
                "alert": f"{_sign(w['direction'])} {_ind_value(w['threshold'], unit)}",
                "today": _ind_value(w["value"], unit) if w["value"] is not None else "—",
                "status": w["status"],
                "direction": w["direction"],
                "owner": w["owner_name"],
                "since": dt.date.fromisoformat(w["since"]),
            }
        )
    # breached first, then the rest; oldest first within each (5e, M12b)
    return sorted(out, key=lambda x: (x["status"] != "breached", x["since"]))


def _sign(direction: str) -> str:
    return ">" if direction == "above" else "<"


def _ind_value(v: Any, kind: str | None) -> str:
    from othor.briefs.values import indicator_text

    return indicator_text(v, kind)


SHOW_FIRST = 5  # Suppressed: each section shows five, then "Show all N" in place (0.33)


def suppressed(rv: RunView, s: Session) -> dict[str, Any]:
    """Sweep › Suppressed (0.33, owner's previews of 22 Nov 2026): your snoozes and mutes
    (snoozed first, the soonest back on top; then muted, the newest first), each with when, where
    it was done and Undo; the findings your Business Model's known patterns kept out, one group
    per pattern with its count; Othor AI's proposed patterns, each to review on Home."""
    from othor.engine.models import Mute, Snooze

    found = [f for f in rv.shown if f["status"] in fnd.HELD]
    fps = [f["fingerprint"] for f in found]
    snz: dict[str, Any] = {}
    mut: dict[str, Any] = {}
    if fps:
        snz = {
            r.fingerprint: r
            for r in s.scalars(
                sa.select(Snooze).where(Snooze.fingerprint.in_(fps), Snooze.ended_at.is_(None))
            )
        }
        mut = {
            r.fingerprint: r
            for r in s.scalars(
                sa.select(Mute).where(Mute.fingerprint.in_(fps), Mute.ended_at.is_(None))
            )
        }
    origin = holds.origins(s, [r.id for r in (*snz.values(), *mut.values())])
    briefs = _brief_names(s, list(origin.values()))
    held = []
    for f in found:
        fp = f["fingerprint"]
        snoozed = f["status"] == "snoozed"
        row = snz.get(fp) if snoozed else mut.get(fp)
        if snoozed:
            until = dt.date.fromisoformat(f["snoozed_until"])
            how, order = f"Snoozed until {fmt.day_month(until)}", (0, until.toordinal())
        else:
            on = fmt.local(row.created_at).date() if row is not None else None
            how = f"Muted on {fmt.day_month(on)}" if on else "Muted"
            order = (1, -(on.toordinal() if on else 0))
        held.append(
            {
                "fp": fp,
                "title": (f.get("text") or {}).get("title", f["metric"]),
                "kind": "snooze" if snoozed else "mute",
                "how": how,
                "where": _where(origin.get(str(row.id)) if row is not None else None, briefs),
                "order": order,
            }
        )
    held.sort(key=lambda h: h["order"])
    props = [
        {"text": p.text, "href": "/ontology#proposals"}
        for p in s.scalars(
            sa.select(Proposal)
            .where(Proposal.kind == "suppression", Proposal.status == "open")
            .order_by(Proposal.created_at)
        )
    ]
    groups = coverage(rv)["suppressed"]
    return {
        "by_ontology": groups,
        "held": held,
        "proposals": props,
        "snoozed": sum(h["kind"] == "snooze" for h in held),
        "muted": sum(h["kind"] == "mute" for h in held),
        "hidden": sum(g.get("count", 0) for g in groups),
        "first": SHOW_FIRST,
    }


def _brief_names(s: Session, origins: list[str]) -> dict[str, str]:
    ids = []
    for o in origins:
        if o.startswith("brief-"):
            try:
                ids.append(uuid.UUID(o[6:]))
            except ValueError:
                continue
    if not ids:
        return {}
    from othor.briefs.models import Brief

    return {f"brief-{b.id}": b.name for b in s.scalars(sa.select(Brief).where(Brief.id.in_(ids)))}


def _where(origin: str | None, briefs: dict[str, str]) -> str:
    """Where a hold was made, in plain words; nothing for a hold made before 0.33."""
    if not origin:
        return ""
    if origin in ("action-plan", "what-to-do"):
        return "from the action plan"
    if origin == "sweep":
        return "from Sweep"
    name = briefs.get(origin)
    if not name:
        return "from a brief"
    return f"from the {name}" if name.lower().endswith("brief") else f"from the {name} brief"


def resolved(rv: RunView) -> list[dict[str, Any]]:
    return [
        {
            "title": (f.get("text") or {}).get("title", f["metric"]),
            "label": (f.get("text") or {}).get("label", f["detector"]),
            "on": dt.date.fromisoformat(f["resolved"]["date"]),
            "how": fnd.shown_reason(f["resolved"]["reason"]),
            "into": f["resolved"].get("into"),
        }
        for f in rv.shown
        if f["status"] == "resolved"
    ]


# --- the finding panel (5b, 5c, 4f–4f3, 10p) ---------------------------------------------------

PANEL_TABS = ("finding", "test", "metric", "sql", "run")


def finding(rv: RunView, fp: str) -> dict[str, Any] | None:
    return next((f for f in rv.shown if f["fingerprint"] == fp), None)


def panel(
    rv: RunView, s: Session, f: dict[str, Any], tab: str, where: str | None = None
) -> dict[str, Any]:
    """The five-tab panel. `where`: the context it opened from ("Leadership brief · Sat 1 Nov
    · In the brief · day 1"); Sweep's own is "Sweep · run #0031"."""
    w = f.get("text") or {}
    base = {
        "fp": f["fingerprint"],
        "tab": tab if tab in PANEL_TABS else "finding",
        "state": f["state"],
        "confidence": f.get("confidence"),
        "change": _change(f["change"]),
        "label": w.get("label", f["detector"]),
        "money": w.get("money", ""),
        "title": w.get("title", ""),
        "context": " · ".join(
            x
            for x in (
                *((where,) if where else ("Sweep", f"run #{rv.run.number:04d}")),
                w.get("consequence")
                if f["state"] == "needs_confirmation"
                else (f"Consequence · {w['consequence']}" if w.get("consequence") else ""),
            )
            if x
        ),
        "status": f["status"],
        "footer": footer(rv, s),
        # 0.33: the foot's run line in plain words; its details behind the (i)
        "run_number": rv.run.number,
        "run_word": run_word(rv, s),
        "run_detail": run_detail(rv, s),
        # already watched (matched by what is watched, not only the finding it came from): the
        # foot says so instead of offering Watch (0.33)
        "watching": watching_text(s, f) if s is not None else "",
    }
    if base["tab"] == "finding":
        base["body"] = {
            "line": w.get("line", ""),
            "why": w.get("why_unseen"),
            "chart": None if f["state"] == "needs_confirmation" else chart_of(f),
            "rows": _rows(w.get("rows"), 5),
            "reasons": w.get("reasons", []),
            "measured": w.get("measured", []),
            "settle": w.get("settle", []),
            "question": ask_for(rv, f),
            "home_note": _home_note(f, w),
            "nd": f["state"] == "not_determinable",
            "nc": f["state"] == "needs_confirmation",
            "history": history(s, f) if s is not None else [],
        }
    elif base["tab"] == "test":
        base["body"] = test_tab(rv, s, f)
    elif base["tab"] == "metric":
        base["body"] = metric_tab(rv, s, f)
    elif base["tab"] == "sql":
        base["body"] = sql_tab(rv, s, f)
    else:
        base["body"] = run_tab(rv, s)
    return base


def supporting(rv: RunView, f: dict[str, Any]) -> list[str]:
    """Supporting evidence (decided 5 Nov 2026): a weekly forecast breach on the finding's
    metric inside its window that is outside its band but didn't pass on its own (P1's week of
    12 Oct). Listed on the Test tab, never under "Also seen by": that is for passed tests."""
    if f["detector"] == "forecast_breach" or f.get("breakdown_value"):
        return []
    start, end = (dt.date.fromisoformat(x) for x in f["window"])
    out = []
    for r in rv.results:
        sl = r.get("slots") or {}
        if (
            r["detector"] != "forecast_breach"
            or r["passed"]
            or r["metric"] != f["metric"]
            or sl.get("grain") != "week"
            or r.get("money") is None
            or not (start <= dt.date.fromisoformat(r["window"][0]) <= end)
            or (r.get("reason") or "").startswith("suppressed by ")
        ):
            continue
        q = Decimal(str(r["q_value"])) if r.get("q_value") is not None else None
        side = "under" if r["direction"] == "down" else "over"
        out.append(
            f"Forecast breach · week of {fmt.day_month(dt.date.fromisoformat(sl['period']))}: "
            f"{sweep_text.money(sl['actual'])} against its 80% band "
            f"{sweep_text.money(sl['lower'])} – {sweep_text.money(sl['upper'])}, "
            f"{sweep_text.money(r['money'])} {side}"
            + (f" (q = {fmt.num(q, decimals=3)}, not significant on its own)" if q else "")
        )
    return out


def _result(rv: RunView, f: dict[str, Any]) -> dict[str, Any] | None:
    """The stored test result behind a finding (its gates' values)."""
    return next(
        (
            r
            for r in rv.results
            if r["passed"] or r.get("reason", "").startswith(("snoozed", "muted"))
            if r["detector"] == f["detector"]
            and r["series"] == f["series"]
            and sorted(r["entities"]) == f["entities"]
        ),
        None,
    )


def _basis(text: str, spec: dict[str, Any]) -> str:
    """A stored money basis in the owner's words: metric keys become their names, full dates
    "10 Aug 2026", months "Sep 2026"; the first letter up (M15.3)."""
    import re

    names = {k: str(m.get("name") or k) for k, m in spec.get("metrics", {}).items()}
    text = re.sub(
        r"\b(\d{4})-(\d{2})-(\d{2})\b",
        lambda m: fmt.day_month_year(dt.date(int(m[1]), int(m[2]), int(m[3]))),
        text,
    )
    text = re.sub(r"\b[a-z][a-z0-9_]*_[a-z0-9_]+\b", lambda m: names.get(m[0], m[0]).lower(), text)
    return _first_up(_months(text))


def _first_up(text: str) -> str:
    """'the fall in South, Sep 2026 …' → 'The fall in South, …': names keep their case."""
    return text[:1].upper() + text[1:]


def _small(v: Any) -> str:
    """p and q values: 4 places, or "below 0.0001"."""
    d = Decimal(str(v))
    return (
        "below 0.0001" if d < Decimal("0.0001") else fmt.num(d, decimals=4).rstrip("0").rstrip(".")
    )


def gates(rv: RunView, f: dict[str, Any], result: dict[str, Any] | None) -> list[dict[str, str]]:
    """The gates the finding passed (detect._gate's order), each with its value against its
    threshold: the detector's own (net hiding gross's offset ratio) or the raw signal, the floor,
    the false-discovery correction across the night's tests, no known pattern (M15.3)."""
    from othor.engine.detect import RAW_P

    spec = rv.compiled
    params = (result or {}).get("params", {})
    out: list[dict[str, str]] = []
    if (
        f["detector"] == "net_hiding_gross"
        and result is not None
        and result.get("statistic") is not None
    ):
        need = Decimal(str(params.get("offset_ratio", "3")))
        out.append(
            {
                "name": "The parts offset each other",
                "why": "Moves up and down, against the net change · needs "
                f"{fmt.num(need)}× or more",
                "value": f"{fmt.num(Decimal(str(result['statistic'])), decimals=1)}×",
            }
        )
    elif f.get("p_value") is not None or (result or {}).get("p_value") is not None:
        p = f.get("p_value") or (result or {}).get("p_value")
        out.append(
            {
                "name": "It stands out",
                "why": f"How unusual it is on its own · needs p under {_small(RAW_P)}",
                "value": f"p {_small(p)}",
            }
        )
    floor = Decimal(str(spec.get("detectors", {}).get("materiality_inr", "500000")))
    if f.get("money"):
        out.append(
            {
                "name": "It is material",
                "why": f"{_basis(f.get('money_basis', ''), spec)} · your floor is "
                f"{sweep_text.money(floor)}",
                "value": sweep_text.money(f["money"]),
            }
        )
    if f.get("q_value") is not None:
        fdr = Decimal(str(params.get("fdr_q", spec.get("detectors", {}).get("fdr_q", "0.01"))))
        tests = params.get("tests")
        keep = params.get("keep_q")  # #142: the looser bar an open finding stays open under
        out.append(
            {
                "name": "It isn’t chance",
                "why": (
                    f"After correcting all {fmt.num(tests)} tests of the night"
                    if tests
                    else "After correcting the night’s tests"
                )
                + f" · needs under {_small(fdr)}"
                + (f" to open, under {_small(keep)} to stay open" if keep else ""),
                "value": f"q {_small(f['q_value'])}",
            }
        )
    n = len(spec.get("suppressions", []) or [])
    out.append(
        {
            "name": "No known pattern explains it",
            "why": f"Checked against your {fmt.num(n)} known pattern{'s' if n != 1 else ''}",
            "value": "None match",
        }
    )
    return out


def moved(rv: RunView, f: dict[str, Any]) -> dict[str, Any] | None:
    """Net hiding gross's "How the total moved" (M15.3): the previous total, each part's move,
    this total, as a waterfall ChartSpec built from the stored contributions and the run's series;
    an "Other" part when the breakdown doesn't add up to the total; the numbers it used."""
    from othor.charts.spec import Axis, Category, ChartSpec
    from othor.engine.finding_charts import fold_below, money_unit

    sl = f.get("slots") or {}
    if f["detector"] != "net_hiding_gross" or not sl.get("contributions"):
        return None
    series = series_of(rv)
    metric, b = f["metric"], f.get("breakdown") or ""
    prev_p, now_p = sl["previous"], sl["period"]
    contrib = {k: Decimal(str(v)) for k, v in sl["contributions"].items()}
    parts = series.values_of(metric, "month", b)
    level = {
        (s.breakdown_value or ""): {
            p.isoformat(): Decimal(str(v))
            for p, v in zip(s.periods, s.values, strict=True)
            if v == v
        }
        for s in parts
    }
    total = series.get(metric, "month")
    tot = (
        {
            p.isoformat(): Decimal(str(v))
            for p, v in zip(total.periods, total.values, strict=True)
            if v == v
        }
        if total is not None
        else {}
    )
    before = tot.get(
        prev_p, sum((level.get(k, {}).get(prev_p, Decimal(0)) for k in contrib), Decimal(0))
    )
    after = tot.get(
        now_p, sum((level.get(k, {}).get(now_p, Decimal(0)) for k in contrib), Decimal(0))
    )
    order = sorted(contrib, key=lambda k: (-contrib[k], k))
    # parts that moved under a fifth of the floor (₹1 L of ₹5 L) fold into "Other", with whatever
    # the breakdown doesn't cover (M15.6: the floor is the owner's, in their currency)
    fold = fold_below(rv.compiled)
    shown = [k for k in order if abs(contrib[k]) >= fold]
    other = after - before - sum(contrib[k] for k in shown)
    unit, div = money_unit()  # ₹ crore or $ millions (M15.6)
    cr = Decimal(str(div))
    pm, nm = (dt.date.fromisoformat(x).strftime("%b") for x in (prev_p, now_p))
    cats = [Category(f"{pm} total", float(round(before / cr, 2)), "neutral", total=True)]
    cats += [
        Category(
            k or "—", float(round(contrib[k] / cr, 2)), "negative" if contrib[k] < 0 else "positive"
        )
        for k in shown
    ]
    if abs(other) >= fold:
        cats.append(Category("Other", float(round(other / cr, 2)), "muted"))
    cats.append(Category(f"{nm} total", float(round(after / cr, 2)), "neutral", total=True))
    name = str(rv.compiled.get("metrics", {}).get(metric, {}).get("name") or metric)
    spec = ChartSpec(
        id=f"test:{f['fingerprint']}",
        kind="waterfall",
        title=f"How the total moved · {name} by {b}, {pm} → {nm} {now_p[:4]}",
        unit=unit,
        x=Axis("category"),
        categories=tuple(cats),
        alt_text=f"{name}: {pm} total {sweep_text.money(before)}, "
        + ", ".join(
            f"{c.key or '—'} {fmt.inr(contrib[c.key], sign=True)}"
            for c in cats[1:-1]
            if c.key in contrib
        )
        + f", {nm} total {sweep_text.money(after)}.",
        provenance={"finding": f["fingerprint"], "metric_id": metric},
    )
    net, gross = after - before, sum(abs(v) for v in contrib.values())
    rise = [k for k in order if contrib[k] > 0][:1]
    fall = [k for k in reversed(order) if contrib[k] < 0][:1]
    said = ", ".join(
        [f"{k} up {sweep_text.money(contrib[k])}" for k in rise]
        + [f"{k} down {sweep_text.money(-contrib[k])}" for k in fall]
    )
    line = (
        f"The total {'fell' if net < 0 else 'rose'} {sweep_text.money(abs(net))}. Underneath, "
        f"{sweep_text.money(gross)} moved: {said}."
    )
    keys = order
    prev_col = sweep_text.money_col(
        [level.get(k, {}).get(prev_p, Decimal(0)) for k in keys] + [before]
    )
    now_col = sweep_text.money_col(
        [level.get(k, {}).get(now_p, Decimal(0)) for k in keys] + [after]
    )
    rows = [
        [k or "—", prev_col[i], now_col[i], fmt.inr(contrib[k], sign=True)]
        for i, k in enumerate(keys)
    ]
    rows.append(["Total", prev_col[-1], now_col[-1], fmt.inr(net, sign=True)])
    stake = None
    fl = (sl.get("fall") or {}).get("name")
    if fl is not None and fl in level:
        a, z = level[fl].get(prev_p), level[fl].get(now_p)
        if a is not None and z is not None:
            stake = f"= {fl}’s fall, {pm} {sweep_text.money(a)} − {nm} {sweep_text.money(z)}."
    return {
        "chart": spec,
        "line": line,
        "headers": [b.capitalize() or "Part", pm, nm, "Change"],
        "rows": rows,
        "stake": stake,
        "breakdown": f"{b.capitalize()} · {fmt.num(len(contrib))} values" if b else None,
        "window": f"{pm} {prev_p[:4]} → {nm} {now_p[:4]}, complete months",
    }


HISTORY_NIGHTS = 30


def history(s: Session, f: dict[str, Any]) -> list[dict[str, Any]]:
    """A finding's dated History (board 4g, M15.3): the nights it was found, from what the runs
    already store (its row in each run, the briefs that carried it, the owner's holds), newest
    first. No engine change: nights its test ran without passing aren't stored per finding."""
    from othor.briefs.models import Brief, BriefIssue
    from othor.engine.models import Mute, Snooze

    fp = f["fingerprint"]
    found = s.execute(
        sa.select(Finding, Run.number, Run.run_date)
        .join(Run, Run.id == Finding.run_id)
        .where(Finding.fingerprint == fp)
        .order_by(Run.number.desc())
        .limit(HISTORY_NIGHTS)
    ).all()
    rows: list[dict[str, Any]] = []
    run_ids = []
    for row, number, day in found:
        run_ids.append(row.run_id)
        money = sweep_text.money(row.money) if row.money else ""
        if row.status == "resolved":
            what = "Resolved"
        elif row.change == "New":
            what = "Found"
        elif row.change in ("Worse", "Better"):
            what = row.change
        else:
            what = row.change or "Open"
        rows.append(
            {"on": day, "run": number, "what": what + (f" · {money}" if money else ""), "order": 2}
        )
    if run_ids:
        issues = s.execute(
            sa.select(BriefIssue, Brief.name)
            .join(Brief, Brief.id == BriefIssue.brief_id)
            .where(BriefIssue.run_id.in_(run_ids))
        ).all()
        seen: set[tuple[Any, str]] = set()
        numbers = {row.run_id: (number, day) for row, number, day in found}
        for issue, name in issues:
            if any(i.get("fingerprint") == fp for i in issue.body.get("items") or []):
                key = (issue.run_id, name)
                if key in seen:
                    continue
                seen.add(key)
                number, day = numbers[issue.run_id]
                label = name if name.lower().endswith("brief") else f"{name} brief"
                rows.append({"on": day, "run": number, "what": f"In the {label}", "order": 1})
    for sn in s.scalars(sa.select(Snooze).where(Snooze.fingerprint == fp)):
        rows.append(
            {
                "on": fmt.local(sn.created_at).date(),
                "run": None,
                "what": f"Snoozed until {fmt.day_month(sn.until)}",
                "order": 0,
            }
        )
        if sn.ended_at:
            rows.append(
                {
                    "on": fmt.local(sn.ended_at).date(),
                    "run": None,
                    "what": "Snooze undone",
                    "order": 0,
                }
            )
    for mu in s.scalars(sa.select(Mute).where(Mute.fingerprint == fp)):
        rows.append(
            {"on": fmt.local(mu.created_at).date(), "run": None, "what": "Muted", "order": 0}
        )
        if mu.ended_at:
            rows.append(
                {"on": fmt.local(mu.ended_at).date(), "run": None, "what": "Unmuted", "order": 0}
            )
    rows.sort(key=lambda r: (r["on"], -(r["run"] or 0), r["order"]), reverse=True)
    return rows


STATE_NOTE = {
    "measured": "Measured: a computed gap, no claim about why.",
    "needs_confirmation": "Needs confirmation: two systems disagree; check the source before "
    "acting.",
    "not_determinable": "Not determinable: the gap is measured; why it happened can’t be told "
    "from the data.",
}


def at_risk_setting(params: dict[str, Any], spec: dict[str, Any]) -> tuple[str, str] | None:
    """#138: how a change in a number with no ₹ of its own priced its linked ₹ (the linked ₹,
    the share of it at risk and how that share was read, the cap), from the result's parameters;
    None for any other money (and for a run sealed before #138)."""
    if params.get("linked_money") is None:
        return None
    from othor.engine.detectors.common import share_text

    key = str(params["linked_metric"])
    linked = str(spec.get("metrics", {}).get(key, {}).get("name") or key)
    of = f"{linked} ({sweep_text.money(params['linked_money'])} since the change)"
    if params.get("at_risk_scaling") == "from_zero":
        return ("Money at risk", f"Can't be sized: the number started from 0 · linked to {of}")
    if params.get("at_risk_fraction_used") is None:  # a move the way its goal wants
        return ("Money at risk", f"None: it moved the way its goal wants · linked to {of}")
    how = (
        "the move in points of the whole"
        if params.get("at_risk_scaling") == "share"
        else "the move as a share of where it was"
    )
    return (
        "Money at risk",
        f"{share_text(float(params['at_risk_fraction_used']))} of {of}: {how}, "
        f"never more than {share_text(float(params['at_risk_cap']))}",
    )


def test_tab(rv: RunView, s: Session | None, f: dict[str, Any]) -> dict[str, Any]:
    """The finding panel's Test tab, "How is this calculated?" (M15.3, the v0.15 design): what it
    tested, how it moved, the gates it passed with their values, the money and its arithmetic,
    the numbers it used, method and settings, and whether the run replays."""
    spec = rv.compiled
    det = REGISTRY.get(f["detector"])
    result = _result(rv, f)
    params = (result or {}).get("params", {})
    start, end = (dt.date.fromisoformat(x) for x in f["window"])
    floor = Decimal(str(spec.get("detectors", {}).get("materiality_inr", "500000")))
    name = str(spec.get("metrics", {}).get(f["metric"], {}).get("name") or f["metric"])
    sl = f.get("slots") or {}
    period = dt.date.fromisoformat(sl["period"]).strftime("%B") if sl.get("period") else ""
    tested = sweep_text.TESTED.get(f["detector"], "").format(
        metric=lower_first(name),
        breakdown=(f.get("breakdown") or "part"),
        period=period,
    )
    mv = moved(rv, f)
    g = gates(rv, f, result)
    fdr = Decimal(str(params.get("fdr_q", spec.get("detectors", {}).get("fdr_q", "0.01"))))
    settings: list[tuple[str, str]] = [
        ("Method", METHOD.get(f["detector"], "")),
        (
            "Window",
            mv["window"] if mv else f"{fmt.day_month(start)} – {fmt.day_month(end)} {end.year}",
        ),
    ]
    if mv and mv["breakdown"]:
        settings.append(("Breakdown", mv["breakdown"]))
    if f["detector"] == "net_hiding_gross" and params.get("offset_ratio"):
        settings.append(
            ("Offset needed", f"{fmt.num(Decimal(str(params['offset_ratio'])))}× the net change")
        )
    at_risk = at_risk_setting(params, spec)
    if at_risk is not None:
        settings.append(at_risk)
    settings += [
        ("Materiality floor", f"{sweep_text.money(floor)} · company setting"),
        (
            "False discoveries",
            f"{fmt.pct(fdr)} (Benjamini–Hochberg), across every test of the night"
            + (f" ({fmt.num(params['tests'])})" if params.get("tests") else ""),
        ),
        *(
            [
                (
                    "Stays open",
                    f"while q is under {_small(params['keep_q'])}, once open"
                    + (" · kept open tonight" if params.get("kept_open") else ""),
                )
            ]
            if params.get("keep_q")
            else []
        ),
        (
            "Run",
            f"#{rv.run.number:04d} · {fmt.date_short(rv.run.run_date)} · "
            f"Business Model v{rv.spec.ontology_version} · "
            f"{sweep_text.LABELS.get(f['detector'], f['detector'])} v"
            f"{f.get('version') or (det.version if det else '')}",
        ),
    ]
    rep = last_replay(s, rv.run.id) if s is not None else None
    also = sl.get("also_by") or []
    evidence = f.get("evidence") or []
    headers = sorted({k for e in evidence for k in e if not isinstance(e[k], dict | list)})[:6]
    return {
        "tested": tested,
        "moved": mv,
        "chart": mv["chart"] if mv else chart_of(f),
        "gates": g,
        "gates_title": f"{fmt.num(len(g))} gate{'s' if len(g) != 1 else ''}, all passed",
        "money": sweep_text.money(f["money"]) if f.get("money") else None,
        "stake": (mv or {}).get("stake")
        or (_basis(f.get("money_basis", ""), spec) + "." if f.get("money_basis") else None),
        "state_note": STATE_NOTE.get(f["state"])
        or (
            "Assessed: the evidence passes every gate; confidence "
            + (f.get("confidence") or "").lower()
            + "."
            if f["state"] == "assessed"
            else ""
        ),
        "settings": settings,
        "replays": bool(rep and rep.get("ok")),
        "state": f["state"],
        "confidence": f.get("confidence"),
        "reconstructed": f.get("reconstructed_only"),
        "allocated": sl.get("allocated"),
        "also": [sweep_text.LABELS.get(a, a) for a in also],
        "support": supporting(rv, f),
        "evidence": None
        if mv or not evidence
        else {
            "headers": headers,
            "rows": [[_cell(e.get(h), h) for h in headers] for e in evidence[:50]],
        },
    }


MONEY_KEYS = {"change", "value", "last_12m", "money", "amount"}


def _cell(v: Any, key: str = "") -> str:
    if v is None:
        return ""
    s = str(v)
    try:
        dt.date.fromisoformat(s)
        return fmt.day_month(dt.date.fromisoformat(s))
    except ValueError:
        pass
    try:
        d = Decimal(s)
    except ArithmeticError:  # not a number (decimal.InvalidOperation)
        return s
    if key in MONEY_KEYS or abs(d) >= 100000:
        return sweep_text.money(d)
    return fmt.num(d, decimals=2 if d % 1 else 0)


def _components(spec: dict[str, Any], key: str, seen: set[str] | None = None) -> list[str]:
    seen = seen or set()
    if key in seen:
        return []
    seen.add(key)
    out = [key]
    for c in spec["metrics"].get(key, {}).get("components") or []:
        out += _components(spec, c, seen)
    return out


def metric_tab(rv: RunView, s: Session, f: dict[str, Any]) -> dict[str, Any]:
    spec = rv.compiled
    m = spec["metrics"].get(f["metric"], {})
    parts = set(_components(spec, f["metric"]))
    rules: list[dict[str, str]] = []
    for r in spec.get("rules", []):
        row = {"text": r["text"] or r["key"], "kind": r["kind"].replace("_", " ")}
        # one rule per text: a join written once per part metric reads once (4f1, M12b)
        if parts & set(r.get("applies_to") or []) and row not in rules:
            rules.append(row)
    unit = {
        "inr": fmt.currency_symbol(),
        "count": "count",
        "days": "days",
        "percent": "%",
        "ratio": "ratio",
        "metres": "metres",
    }
    defs = [
        ("Kind", "a total per period" if m.get("kind") == "flow" else "a value as of each day"),
        ("Grain", str(m.get("grain") or "")),
        (
            "Breakdowns",
            " · ".join(
                spec.get("breakdowns", {}).get(b, {}).get("name", b)
                for b in m.get("breakdowns") or []
            ),
        ),
        ("Unit", unit.get(str(m.get("unit")), str(m.get("unit") or ""))),
    ]
    if m.get("components"):
        defs.insert(
            0,
            (
                "Built from",
                ", ".join(spec["metrics"].get(c, {}).get("name", c) for c in m["components"]),
            ),
        )
    source = [("Where it runs", str(m.get("run_location") or m.get("source_label") or ""))]
    srcs = m.get("sources") or []
    if srcs:
        source.insert(0, ("Book of record", ", ".join(srcs)))
    used = sorted(
        {
            sweep_text.LABELS.get(r["detector"], r["detector"])
            for r in rv.results
            if r.get("metric") == f["metric"]
        }
    )
    return {
        "name": m.get("name") or f["metric"],
        "description": m.get("description") or "",
        "definition": defs,
        "source": source,
        "rules": rules,
        "version": [
            (
                "Spec",
                f"v{rv.spec.ontology_version} · "
                f"compiled {fmt.day_month(rv.spec.created_at.date())}",
            ),
            ("Definition", str(m.get("definition_hash") or "")[:12]),
            (
                "Used in",
                f"{fmt.num(len(used))} detectors" + (f" · {', '.join(used)}" if used else ""),
            ),
        ],
    }


def _series_key(f: dict[str, Any]) -> tuple[str, str, str]:
    first = (f.get("series") or [f"{f['metric']}|month||"])[0]
    metric, grain, bd = ([*first.split("|"), "", "", ""])[:3]
    return metric, grain, bd


def sql_tab(rv: RunView, s: Session, f: dict[str, Any]) -> dict[str, Any]:
    """The statements the run executed for this finding's metric (and, for a derived metric,
    its parts), as `run_query` holds them, with where they ran."""
    spec = rv.compiled
    metric, grain, bd = _series_key(f)
    statements = []
    for part in _components(spec, metric):
        m = spec["metrics"].get(part, {})
        q = m.get("queries") or {}
        for key in (f"{grain}:{bd}" if bd else grain, grain, f"day:{bd}" if bd else "day"):
            if key in q:
                statements.append((part, key, q[key]))
                break
    fam = str(f.get("family") or "")
    probes = spec.get("probes") or {}
    if fam.startswith("reconcile:"):
        key = fam.split(":", 1)[1]
        for r in probes.get("reconcile", []):
            if r["key"] == key and r.get("kind") == "keys":
                statements += [
                    (r["left"]["label"], "reconcile", r["left"]["sql"]),
                    (r["right"]["label"], "reconcile", r["right"]["sql"]),
                ]
    if fam.startswith("entity:"):
        e = probes.get("entities", {}).get(fam.split(":", 1)[1])
        if e:
            statements.append((f"customers in {e['source']}", "entity", e["sql"]))
    if f["detector"] in ("process_artefact", "bimodal_average"):
        for r in [*probes.get("thresholds", []), *probes.get("distributions", [])]:
            if r.get("metric") == metric or r.get("key") == metric:
                statements.append((r.get("label") or metric, "probe", r["sql"]))
    # M15.1: only this finding's statements, by hash (sha256 of the SQL, as the query log keeps
    # it), not every statement of the run
    digests = {sql: hashlib.sha256(sql.encode()).hexdigest() for _p, _k, sql in statements}
    sent = (
        list(
            s.scalars(
                sa.select(RunQuery).where(
                    RunQuery.run_id == rv.run.id, RunQuery.hash.in_(set(digests.values()))
                )
            )
        )
        if digests
        else []
    )
    ids = {x.source_id for x in sent}
    names = (
        {x.id: x.name for x in s.scalars(sa.select(Source).where(Source.id.in_(ids)))}
        if ids
        else {}
    )
    out = []
    for part, key, sql in statements:
        hits = [x for x in sent if x.hash == digests[sql]]
        derived = spec["metrics"].get(part, {}).get("type") == "derived"
        out.append(
            {
                "metric": spec["metrics"].get(part, {}).get("name") or part,
                "key": key,
                # where it came from, as a comment the SQL keeps when copied (4f2, as on 6a2)
                "sql": f"-- {spec['metrics'].get(part, {}).get('name') or part} · {key} · "
                f"spec v{rv.spec.ontology_version}\n" + (hits[0].sql if hits else sql),
                "ran": bool(hits),
                "where": names.get(hits[0].source_id, "") if hits else (STORE if derived else ""),
                "rows": sum(x.rows for x in hits),
                "ms": sum(x.duration_ms for x in hits),
                "times": len(hits),
                "params": hits[0].params if hits else {},
            }
        )
    return {
        "statements": out,
        "download": f"/sweep/finding/{f['fingerprint']}/result.csv?run={rv.run.number}",
    }


def result_csv(rv: RunView, f: dict[str, Any]) -> str:
    """The run's stored values for the finding's series (aggregates, never source rows)."""
    metric, grain, bd = _series_key(f)
    from othor.web import run_cache

    lines = ["period,breakdown_value,value,reconstructed"]
    mine = run_cache.by_metric(rv.files.series).get(metric, ())
    for p in sorted(mine, key=lambda p: p.sort_key()):
        if p.metric == metric and p.grain == grain and p.breakdown == bd and p.complete:
            lines.append(
                f"{p.period.isoformat()},{p.breakdown_value or ''},"
                f"{'' if p.value is None else p.value},{str(p.reconstructed).lower()}"
            )
    return "\n".join(lines) + "\n"


def _phases(s: Session, run: Run) -> list[Any]:
    from othor.web.run_view import phases

    return phases(s, run)


def run_tab(rv: RunView, s: Session) -> dict[str, Any]:
    r = rv.run
    steps = list(
        s.scalars(sa.select(RunStep).where(RunStep.run_id == r.id).order_by(RunStep.ordinal))
    )
    sent = list(s.scalars(sa.select(RunQuery).where(RunQuery.run_id == r.id)))
    sources = {x.id: x for x in s.scalars(sa.select(Source))}
    manifest = read_json(rv.files.manifest) or {}
    read_rows = []
    for name, meta in sorted((manifest.get("sources") or {}).items()):
        src = next((x for x in sources.values() if x.name == name), None)
        mine = [q for q in sent if src is not None and q.source_id == src.id]
        read_rows.append(
            {
                "name": name,
                "status": meta.get("status", ""),
                "last_good": meta.get("last_good"),
                "queries": len(mine),
                "rows": sum(q.rows for q in mine),
                "kind": src.kind if src else "",
            }
        )
    took = (r.ended_at - r.started_at).total_seconds() if r.ended_at else None
    return {
        "number": f"#{r.number:04d}",
        "facts": [
            ("Run id", f"{str(r.id)[:8]}…{str(r.id)[-4:]}"),
            (
                "Run date",
                f"{fmt.day_month(r.run_date)} {r.run_date.year} · "
                f"data to {fmt.day_month(r.run_date - dt.timedelta(days=1))}",
            ),
            ("Took", f"{fmt.num(took, decimals=1)} s" if took is not None else ""),
            ("Spec", f"v{rv.spec.ontology_version} · {rv.spec.hash[:12]}"),
            ("Image", str(r.image_version)),
            ("Input hash", f"{(r.input_hash or '')[:12]} · {fmt.num(len(read_rows))} sources"),
            ("Run hash", (r.run_hash or "")[:12]),
        ],
        "sources": read_rows,
        "steps": [
            {
                "name": st.name,
                "status": st.status,
                "secs": (st.ended_at - st.started_at).total_seconds()
                if st.ended_at and st.started_at
                else None,
            }
            for st in steps
        ],
        "phases": _phases(s, r),
        "replay": last_replay(s, r.id),
        "log": f"/sweep/run/{r.number}/log.json",
    }


def last_replay(s: Session, run_id: uuid.UUID) -> dict[str, Any] | None:
    ev = s.scalars(
        sa.select(Event)
        .where(Event.action == "run.replayed", Event.object_id == str(run_id))
        .order_by(Event.created_at.desc())
    ).first()
    return dict(ev.after or {}, at=ev.created_at) if ev else None


def footer(rv: RunView, s: Session) -> str:
    r = rv.run
    parts = [
        f"Run #{r.number:04d}",
        f"{(r.run_hash or '')[:4]}…{(r.run_hash or '')[-2:]}",
        f"spec v{rv.spec.ontology_version}",
    ]
    rep = last_replay(s, r.id)
    if rep is not None:
        parts.append("replay identical" if rep.get("ok") else "replay differs")
    else:
        parts.append("replayable")  # as the brief's footer says (4g, M12b)
    return " · ".join(parts)


def ask_for(rv: RunView, f: dict[str, Any]) -> dict[str, Any] | None:
    """The question to ask about this finding, from the same rule the brief uses (0.33: the panel
    asked Leela "What changed for South customers in October?" while the Leadership brief asked
    Priya about Tessora EU and Ravira Exports). Shaped as the finding's own question."""
    from othor.briefs.title import ask_today

    own = (f.get("text") or {}).get("question")
    got = ask_today([f], rv.compiled.get("owners") or [], rv.compiled)
    if not got or not got.get("text"):
        return own
    to = [{"name": got["to"], "role": got.get("role", "")}] if got.get("to") else []
    if not to and own:
        to = own.get("to") or []
    return {**(own or {}), "text": got["text"], "to": to}


def watching_text(s: Session, f: dict[str, Any]) -> str:
    """ "alert above 8 · Priya Menon" when an active watch covers this finding (0.33). Uses
    `watches.covering` / `watches.label` (built with the brief side of 0.33)."""
    w = watches.covering(s, f)
    return watches.label(w) if w is not None else ""


def run_word(rv: RunView, s: Session) -> str:
    """The run line's plain promise (0.33): every run replays; a replay that differed says so."""
    rep = last_replay(s, rv.run.id) if s is not None else None
    return "same numbers on replay" if rep is None or rep.get("ok") else "the replay differed"


def run_detail(rv: RunView, s: Session | None = None) -> str:
    """What the run line's (i) holds (rule 5): the run, its fingerprint, the Business Model
    version, the app, and what the last replay showed."""
    r = rv.run
    h = r.run_hash or ""
    rep = last_replay(s, r.id) if s is not None else None
    replay = (
        "replayable" if rep is None else ("replay identical" if rep.get("ok") else "replay differs")
    )
    return (
        f"Run #{r.number:04d} · fingerprint {h[:4]}…{h[-2:]} · Business Model version "
        f"{rv.spec.ontology_version} · Othor AI {r.image_version} · {replay}"
    )


def run_log(rv: RunView, s: Session) -> dict[str, Any]:
    r = rv.run
    steps = s.scalars(sa.select(RunStep).where(RunStep.run_id == r.id).order_by(RunStep.ordinal))
    sent = s.scalars(sa.select(RunQuery).where(RunQuery.run_id == r.id).order_by(RunQuery.id))
    return {
        "run": {
            "number": r.number,
            "id": str(r.id),
            "kind": r.kind,
            "run_date": r.run_date.isoformat(),
            "input_hash": r.input_hash,
            "output_hash": r.output_hash,
            "run_hash": r.run_hash,
            "prev_run_hash": r.prev_run_hash,
            "spec": rv.spec.hash,
        },
        "steps": [{"name": st.name, "status": st.status, "detail": st.detail} for st in steps],
        "queries": [
            {"sql": q.sql, "params": q.params, "rows": q.rows, "ms": q.duration_ms, "hash": q.hash}
            for q in sent
        ],
    }


# --- Watch this (5d) --------------------------------------------------------------------------


@dataclass
class WatchDraft:
    fp: str
    number: str
    label: str
    indicator: dict[str, Any]
    threshold: str
    direction: str
    owner: str
    owner_note: str
    hint: str
    today: str | None
    breached: bool
    first_breach: dt.date | None
    days_ago: int | None
    chart: ChartSpec | None
    when: str
    unit: str = ""
    owners: list[str] = field(default_factory=list)
    today_text: str = ""
    # 0.33 (plan § E): the limit as the owner reads it (₹64 L, 2.4 days, 10%), the field's name
    # by direction, and the finding it was drafted from, by its title
    threshold_text: str = ""
    alert_label: str = ""
    source: str = ""

    @property
    def owner_options(self) -> list[tuple[str, str]]:
        return [(n, n) for n in self.owners] or [("", "No owner in your Business Model")]


def watch_draft(rv: RunView, f: dict[str, Any], threshold: str | None = None) -> WatchDraft | None:
    series = series_of(rv)
    spec = rv.compiled
    d = watches.draft(f, series, spec)
    if d is None:
        return None
    thr = threshold or d["threshold"]
    w = f.get("text") or {}
    who = w.get("owners") or []
    owner = who[0]["name"] if who else ""
    unit = d["unit"]
    end = rv.run.run_date - dt.timedelta(days=1)
    rep = watches.replay(d["indicator"], series, thr, d["direction"], end)
    today = watches.indicator(d["indicator"], series, [])
    if d["indicator"]["kind"] == "finding_slot":
        today = str((f.get("slots") or {}).get(d["indicator"]["slot"]))
    breached = today is not None and (
        Decimal(today) > Decimal(thr)
        if d["direction"] == "above"
        else Decimal(today) < Decimal(thr)
    )
    first = dt.date.fromisoformat(rep["first_breach"]) if rep["first_breach"] else None
    hint = ""
    h = d["hint"]
    if h["kind"] == "halfway":
        before = fmt.day_month(dt.date.fromisoformat(h["before"]))
        hint = (
            f"Halfway between its usual high ({_unit_value(h['value'], unit)}, before {before})"
            f" and today ({_unit_value(h['today'], unit)}). Change it if you want a different line."
        )
    elif h["kind"] == "never_passed":
        before = fmt.day_month(dt.date.fromisoformat(h["before"]))
        hint = f"Before {before} it never went past {_unit_value(h['value'], unit)}"
    elif h["kind"] == "level_before":
        hint = f"Where it was before the step: {_unit_value(h['value'], unit)}"
    elif h["kind"] == "today":
        hint = f"Today {_unit_value(h['value'], unit)}"
    first_name = owner.split(" ")[0] if owner else ""
    covers = _covers(spec, f)
    briefs = (
        (
            f" and in every brief that covers {covers}"
            if covers
            else " and in the briefs that carry it"
        )
        if features.BRIEFS
        else ""
    )
    naming = f", naming {first_name}" if first_name else ""
    stays = "below" if d["direction"] == "above" else "above"
    when = f"It shows on Home{briefs}, the next morning{naming}. Nothing while it stays {stays}."
    chart = _replay_chart(f, d, rep, thr, unit)
    return WatchDraft(
        fp=f["fingerprint"],
        number="",
        label=plain_label(d["label"]),
        indicator=d["indicator"],
        threshold=thr,
        direction=d["direction"],
        owner=owner,
        owner_note=_owner_note(spec, owner, f),
        hint=hint,
        today=today,
        breached=breached,
        first_breach=first,
        days_ago=(end - first).days if first else None,
        chart=chart,
        when=when,
        unit=unit,
        owners=sorted(o["name"] for o in spec.get("owners", [])),
        today_text=_unit_value(today, unit) if today is not None else "",
        threshold_text=threshold_text(thr, unit),
        alert_label=f"Tell me when it goes {d['direction']}",
        source=str(w.get("title") or ""),
    )


def plain_label(label: str) -> str:
    """A watch's name in plain words (rule 10); the measure's detail stays in the provenance."""
    label = label.replace(" · p90 − p10 days, 7-day window", ", last 7 days")
    return re.sub(r" · weekly$", ", each week", label)


def _covers(spec: dict[str, Any], f: dict[str, Any]) -> str:
    """What the briefs that would carry the alert cover, by the number's name and its part
    ("overdue amount, over 90 days"); empty when the finding isn't a number's (a reconcile)."""
    m = spec.get("metrics", {}).get(f["metric"])
    if not m:
        return ""
    name = str(m.get("name") or f["metric"])
    name = lower_first(name)
    part = f.get("breakdown_value")
    return f"{name}, {part}" if part else name


def _owner_note(spec: dict[str, Any], owner: str, f: dict[str, Any] | None = None) -> str:
    """What the owner looks after, as names (the areas this finding falls in first)."""
    areas = [a for o in spec.get("owners", []) if o["name"] == owner for a in o.get("areas", [])]
    if not areas:
        return ""
    if f is not None:
        keys = {f.get("metric"), f.get("family"), f.get("breakdown_value"), f.get("detector")}
        keys |= set(f.get("entities") or [])
        keys |= {k for a in areas for k in [a] if str(f.get("metric", "")).startswith(f"{a}_")}
        mine = [a for a in areas if a in keys]
        areas = mine or areas[:3]
    names = list(dict.fromkeys(_area_name(spec, a) for a in areas))
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"Looks after {joined} in your Business Model"


def _area_name(spec: dict[str, Any], key: str) -> str:
    m = spec.get("metrics", {}).get(key)
    if m and m.get("name"):
        n = str(m["name"])
        return lower_first(n)
    if key and key[:1].isupper():  # a place or a customer, as written
        return key
    label = sweep_text.LABELS.get(key)
    return str(label or key.replace("_", " ")).lower()


def _unit_value(v: Any, unit: str) -> str:
    if v is None:
        return "—"
    d = Decimal(str(v))
    if unit == "days":
        return f"{fmt.num(d, decimals=1)} days"
    if unit == "percent":
        return fmt.pct(d, sign=True)
    if unit == "inr":
        return sweep_text.money(d)
    return fmt.num(d)


def threshold_text(v: Any, unit: str) -> str:
    """The limit as the field shows it: ₹64 L, 2.4 days, 10%, 14 (never 6.4E+6)."""
    try:
        d = Decimal(str(v))
    except (ArithmeticError, ValueError):
        return str(v)
    if unit == "percent":
        return fmt.pct(d)
    return _unit_value(d, unit)


def parse_threshold(text: str | None, unit: str) -> str | None:
    """The limit the owner typed, as a plain decimal: "₹64 L", "64,00,000", "6400000",
    "1.2 Cr", "$2.5M", "10%", "2.4 days" (0.33). None when it isn't a number."""
    if text is None:
        return None
    t = text.strip().lower().replace(",", "").replace("₹", "").replace("$", "").replace(" ", "")
    t = re.sub(r"(days?|d)$", "", t)
    mult = Decimal(1)
    for suffix, m in (("crore", 7), ("cr", 7), ("lakh", 5), ("l", 5), ("k", 3), ("m", 6), ("b", 9)):
        if t.endswith(suffix) and unit == "inr":
            t, mult = t[: -len(suffix)], Decimal(10) ** m
            break
    pct = t.endswith("%")
    t = t.rstrip("%").lstrip("+")
    try:
        d = Decimal(t) * mult
    except (ArithmeticError, ValueError):
        return None
    if not d.is_finite():
        return None
    if unit == "percent" and (pct or abs(d) > 1):
        d = d / 100
    return str(d.normalize()) if d != d.to_integral() else str(d.quantize(Decimal(1)))


def watch_threshold(rv: RunView, f: dict[str, Any], text: str | None) -> str | None:
    """What a typed limit means for this finding's watch (its unit from the draft)."""
    d = watches.draft(f, series_of(rv), rv.compiled)
    return parse_threshold(text, d["unit"] if d else "count")


def _replay_chart(
    f: dict[str, Any], d: dict[str, Any], rep: dict[str, Any], thr: str, unit: str
) -> ChartSpec | None:
    raw = [(day, float(v)) for day, v in rep["points"]]
    if len(raw) < 7:
        return None
    u, div = _watch_unit(unit, [v for _, v in raw] + [float(thr)])
    pts = [(day, v / div) for day, v in raw]
    side = "above" if d["direction"] == "above" else "below"
    limit = _unit_value(thr, unit)
    ann = [Annotation("threshold", value=float(thr) / div, label=f"alert {side} {limit}")]
    first = dt.date.fromisoformat(rep["first_breach"]) if rep["first_breach"] else None
    if first is not None:
        ann.append(
            Annotation(
                "vline",
                at=rep["first_breach"],
                label=f"{fmt.day_month(first)} · would have alerted",
                role="negative",
                style="dashed",
            )
        )
    ends = (
        f": it would first have gone {side} on {fmt.day_month(first)}."
        if first
        else f": it never went {side}."
    )
    label = plain_label(d["label"])
    return ChartSpec(
        id=f"watch:{f['fingerprint']}",
        kind="line",
        title=f"{label} · last {fmt.num(len(pts))} days, against this limit",
        unit=u,
        period=Period("day", pts[0][0], pts[-1][0]),
        series=(Series("v", "Value", "neutral", tuple(pts)),),
        annotations=tuple(ann),
        provenance={"finding": f["fingerprint"]},
        alt_text=f"{label} over the last {fmt.num(len(pts))} days against the limit {limit}" + ends,
    )


def _watch_unit(unit: str, values: list[float]) -> tuple[Unit, float]:
    """The chart's unit and divisor: money in crore (or $ millions) so its axis labels fit the
    gutter, lakh-sized money too (0.33: the axis showed 0,000 and raw 76,38,304)."""
    if unit == "inr":
        from othor.engine.finding_charts import money_unit

        top = max((abs(v) for v in values), default=0.0)
        big, div = money_unit()
        return (Unit(big.type, big.code, big.scale, 2 if top / div >= 0.1 else 3), div)
    if unit == "days":
        return Unit("days", decimals=1), 1.0
    if unit in ("percent", "ratio"):
        return Unit("percent", decimals=0), 1.0
    return Unit("count"), 1.0


# --- Sweep history (0.28.1, owner 19 Nov 2026): every night since the first -----------------
# All from what each night stored: its summary (Run.summary["sweep"]), its finding rows (the
# open ones' amounts, summed by code) and the replay events. No source read, no model.


HISTORY_CHART_MIN = 7  # nights before the history chart shows


def _history_runs(s: Session) -> list[Run]:
    return list(
        s.scalars(
            sa.select(Run)
            .where(Run.kind.in_(DETECTING_KINDS), Run.status.in_(("done", "partial", "failed")))
            .order_by(Run.number.desc())
        )
    )


def run_by_number(s: Session, number: int) -> Run | None:
    """Any detecting run by its number, however old (Sweep's last-7 menu is only a shortcut)."""
    return s.scalars(
        sa.select(Run).where(
            Run.number == number,
            Run.kind.in_(DETECTING_KINDS),
            Run.status.in_(("done", "partial")),
        )
    ).first()


def _sweep_counts(r: Run) -> dict[str, Any]:
    return dict((r.summary or {}).get("sweep") or {})


def history_chart(s: Session) -> ChartSpec | None:
    """One bar per night since the first: the findings open that night."""
    runs = [r for r in reversed(_history_runs(s)) if r.status != "failed"]
    if not runs:
        return None
    days: dict[dt.date, int] = {}
    for r in runs:  # a rerun replaces the night it reran
        days[r.run_date] = int(_sweep_counts(r).get("findings") or 0)
    pts = sorted(days.items())
    if len(pts) < HISTORY_CHART_MIN:
        return None  # a few bars fill the frame and say little: the table says it until then
    busiest = max(pts, key=lambda x: (x[1], x[0]))
    alt = f"Findings open each night, {fmt.day_month(pts[0][0])} to {fmt.day_month(pts[-1][0])}"
    if busiest[1]:
        alt += f"; the most on {fmt.day_month(busiest[0])} ({fmt.num(busiest[1])})"
    return ChartSpec(
        id="sweep-history",
        kind="sparkline",
        title="Findings open each night",
        alt_text=alt,
        series=(
            Series(
                "open",
                "Open findings",
                "positive",
                tuple((d.isoformat(), n) for d, n in pts),
                style="bar",
            ),
        ),
    )


HISTORY_FIRST = 10  # nights shown first (owner, 19 Nov 2026: the last 10, then more in place)
HISTORY_MORE = 30  # nights each "Show older nights" adds


def history_nights(s: Session, shown: int = HISTORY_FIRST) -> dict[str, Any]:
    """The newest `shown` nights, newest first, how many older ones remain, and one line over
    every night since the first."""
    every = _history_runs(s)
    shown = max(HISTORY_FIRST, shown)
    runs = every[:shown]
    ids = [r.id for r in runs]
    money: dict[uuid.UUID, Decimal] = {}
    if ids:
        for run_id, body in s.execute(
            sa.select(Finding.run_id, Finding.body).where(
                Finding.run_id.in_(ids), Finding.status == "open"
            )
        ):
            try:
                money[run_id] = money.get(run_id, Decimal(0)) + Decimal(
                    str((body or {}).get("money") or 0)
                )
            except ArithmeticError:
                continue
    replays: dict[str, bool] = {}
    if ids:
        for ev in s.scalars(
            sa.select(Event)
            .where(Event.action == "run.replayed", Event.object_id.in_([str(i) for i in ids]))
            .order_by(Event.created_at)
        ):
            replays[ev.object_id] = bool((ev.after or {}).get("ok"))  # the latest replay wins
    rows: list[dict[str, Any]] = []
    for r in runs:
        c = _sweep_counts(r)
        done = r.status != "failed"
        rp = replays.get(str(r.id))
        total = money.get(r.id, Decimal(0))
        rows.append(
            {
                "number": r.number,
                "date": r.run_date,
                "kind": RUN_KIND.get(r.kind, ""),
                "done": done,
                "tests": int(c.get("tests") or 0),
                "new": int(c.get("new") or 0),
                "open": int(c.get("findings") or 0),
                "resolved": int(c.get("resolved") or 0),
                "money": sweep_text.money(total) if done and total else "–",
                "replays": "Same bytes" if rp is True else "Differs" if rp is False else "–",
                "href": f"/sweep?run={r.number}" if done else None,
            }
        )
    done_all = [(r.run_date, _sweep_counts(r)) for r in every if r.status != "failed"]
    line = ""
    if done_all:
        nights = len({d for d, _ in done_all})
        new = sum(int(c.get("new") or 0) for _, c in done_all)
        resolved = sum(int(c.get("resolved") or 0) for _, c in done_all)
        bd, bc = max(done_all, key=lambda x: (int(x[1].get("new") or 0), x[0]))
        busy = {"date": bd, "new": int(bc.get("new") or 0)}
        parts = [
            f"{fmt.num(nights)} night{'s' if nights != 1 else ''}",
            f"{fmt.num(new)} new finding{'s' if new != 1 else ''}",
            f"{fmt.num(resolved)} resolved",
        ]
        if busy["new"]:
            parts.append(
                f"busiest night {fmt.day_month(busy['date'])} ({fmt.num(busy['new'])} new)"
            )
        line = " · ".join(parts)
    more = len(every) - len(runs)
    return {
        "rows": rows,
        "line": line,
        "more": more,
        "next": min(more, HISTORY_MORE),
        "shown": len(runs),
    }


def history_first(s: Session) -> dt.date | None:
    runs = _history_runs(s)
    return runs[-1].run_date if runs else None
