# SPDX-License-Identifier: Apache-2.0
"""Owner emails (docs/09): run failed / source not read (8c), Ontology waiting (8d), weekly
digest (8e). Built from stored rows and passed-in data only; the job that sends one gathers it
(8d's drafts live in othor.setup, which othor.mail doesn't import).

Copy follows the boards, with what v0.1 knows: there is no "your setting: send nothing" (a fixed
rule, not a setting), no members to name in 8d, and no "marked not useful" in 8e (next6).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from othor.core import fmt
from othor.core.config import Settings
from othor.core.localtime import local_now
from othor.core.models import Company
from othor.engine.ledger import step_label
from othor.mail.messages import owner_email
from othor.mail.relay import Message


def _clock(company: Company, t: dt.time | str) -> str:
    """A time of day in the company's zone, with the zone (fmt.clock): "06:00 IST"."""
    with fmt.display_tz(company.timezone):
        return fmt.clock(t)


def _when(company: Company, at: dt.datetime | None, d: dt.date) -> str:
    if at is None:
        return fmt.date_short(d)
    lt = local_now(company, at)
    return f"{fmt.date_short(lt.date())} {_clock(company, lt.time())}"


@dataclass(frozen=True)
class FailedRun:
    number: int
    run_date: dt.date
    ended_at: dt.datetime | None
    step: str  # the run step that failed (read:sales_dw), '' if none recorded
    error: str  # the step's error (a type or a short reason; never row values)
    source_id: str | None  # the source behind a read step, for "Fix the source"
    others_ok: bool  # every other source was read


def run_failed(
    settings: Settings, company: Company, run: FailedRun, *, to: str, brief_time: str
) -> Message:
    bt = _clock(company, brief_time)
    src = run.step.split(":", 1)[1] if run.step.startswith("read:") else ""
    actions: list[tuple[str, str, bool]] = []
    if run.source_id:
        actions.append(
            (
                "Fix the source",
                settings.instance_url(company.slug, f"/sources/{run.source_id}"),
                True,
            )
        )
    actions.append(
        (
            "Re-run now",  # the owner's own archive panel, signed in (M11a)
            settings.instance_url(company.slug, f"/brief/archive?failed={run.number}"),
            not run.source_id,
        )
    )
    what = f"{src} couldn’t be read" if src else "no brief this morning"
    return owner_email(
        "run_failed",
        to=to,
        subject=f"[Othor AI] Last night’s run failed · {what}",
        preheader=f"No brief will be sent at {bt}. Fix the source and re-run.",
        company_name=company.name,
        slug=company.slug,
        settings=settings,
        kicker=f"Run failed · #{run.number:04d} · {_when(company, run.ended_at, run.run_date)}",
        lead="The nightly run stopped at " if run.step else "The nightly run stopped.",
        step=step_label(run.step) if run.step else "",
        after=(f"No brief was made, so none goes out at {bt}. Yesterday’s brief stays available."),
        error=(f"{src} · {run.error}" if src and run.error else run.error),
        others=("Other sources were read normally." if src and run.others_ok else ""),
        actions=actions,
    )


def source_stale(
    settings: Settings,
    company: Company,
    *,
    to: str,
    source: str,
    source_id: str | None,
    reason: str,
    run_number: int,
    run_date: dt.date,
    ended_at: dt.datetime | None,
    brief_time: str,
) -> Message:
    bt = _clock(company, brief_time)
    actions: list[tuple[str, str, bool]] = []
    if source_id:
        actions.append(
            ("Fix the source", settings.instance_url(company.slug, f"/sources/{source_id}"), True)
        )
    actions.append(("Open the brief", settings.instance_url(company.slug, "/brief"), not source_id))
    return owner_email(
        "run_failed",
        to=to,
        subject=f"[Othor AI] {source} couldn’t be read last night",
        preheader=(f"The {bt} brief marks the numbers from {source} with their last good date."),
        company_name=company.name,
        slug=company.slug,
        settings=settings,
        kicker=f"Source not read · {source} · {_when(company, ended_at, run_date)}",
        lead=f"Last night’s run #{run_number:04d} couldn’t read ",
        step=source,
        after=(
            f"The run went on. The {bt} brief still goes out, and the numbers that use "
            f"{source} say which day they are from."
        ),
        error=reason,
        others="",
        actions=actions,
    )


def run_late(
    settings: Settings,
    company: Company,
    *,
    to: str,
    run_number: int | None,
    run_date: dt.date,
    now: dt.datetime,
    brief_time: str,
) -> Message:
    bt = _clock(company, brief_time)
    at = local_now(company, now)
    at_t = _clock(company, at.time())
    num = f" · #{run_number:04d}" if run_number else ""
    return owner_email(
        "run_failed",
        to=to,
        subject="[Othor AI] Last night’s run didn’t finish · no brief this morning",
        preheader=f"The {bt} brief wasn’t sent.",
        company_name=company.name,
        slug=company.slug,
        settings=settings,
        kicker=f"Run not finished{num} · {fmt.date_short(at.date())} {at_t}",
        lead=(
            f"The nightly run for {fmt.date_short(run_date)} hadn’t finished by {at_t}, "
            f"so the {bt} brief wasn’t sent."
        ),
        step="",
        after="Yesterday’s brief stays available.",
        error="",
        others="",
        actions=[("See the runs", settings.instance_url(company.slug, "/brief/archive"), True)],
    )


def ontology_waiting(
    settings: Settings,
    company: Company,
    *,
    to: str,
    rows: list[dict[str, str]],
    total: int,
    drafted_on: dt.date,
) -> Message:
    n = fmt.num(total)
    noun = "metric is" if total == 1 else "metrics are"
    return owner_email(
        "ontology_waiting",
        to=to,
        subject=f"{n} {noun} waiting for your confirmation",
        preheader="Nothing runs until you confirm.",
        company_name=company.name,
        slug=company.slug,
        settings=settings,
        lead=(
            f"Othor AI drafted {n} metric{'s' if total != 1 else ''} from your sources on "
            f"{fmt.day_month(drafted_on)}. Nothing runs and no brief is made until you confirm "
            "them."
        ),
        rows=rows,
        more=max(total - len(rows), 0),
        review_url=settings.instance_url(company.slug, "/setup/metrics"),
    )


def weekly_digest(
    settings: Settings,
    company: Company,
    *,
    to: str,
    first: dt.date,
    last: dt.date,
    stats: dict[str, int],
    open_items: list[tuple[str, str]],
    not_in_brief: list[tuple[str, str]],
) -> Message:
    tests, fired, in_brief = stats["tests"], stats["fired"], stats["in_brief"]
    return owner_email(
        "weekly_digest",
        to=to,
        subject=(
            f"This week in Sweep: {fmt.num(tests)} tests run, {fmt.num(fired)} findings, "
            f"{fmt.num(in_brief)} reached a brief"
        ),
        preheader=(
            f"Plus {fmt.num(len(not_in_brief))} finding{'s' if len(not_in_brief) != 1 else ''} "
            "that didn’t reach a brief."
            if not_in_brief
            else "Every finding this week reached a brief."
        ),
        company_name=company.name,
        slug=company.slug,
        settings=settings,
        week=fmt.day_range(first, last),
        stats=[
            ("Tests run", fmt.num(tests)),
            ("Findings", fmt.num(fired)),
            ("In a brief", fmt.num(in_brief)),
            ("Still open, all findings", fmt.num(stats["open"])),
        ],
        sections=[("Open in a brief", open_items), ("Fired but not in a brief", not_in_brief)],
        sweep_url=settings.instance_url(company.slug, "/sweep"),
    )


def digest_rows(findings: list[Any]) -> list[tuple[str, str]]:
    """(title, owner · day N) for open items in the briefs."""
    out = []
    for f in findings:
        title = ((f.body or {}).get("text") or {}).get("title") or f.metric
        days = f"day {fmt.num(f.days_open)}" if f.days_open else "new"
        out.append((title, f"{f.owner_name} · {days}" if f.owner_name else days))
    return out


def export_ready(
    settings: Settings, company: Company, job_id: Any, until: dt.datetime, *, to: str
) -> Message:
    """M18.1: a large company's export, built by a job: a link that needs sign-in, kept 48 h."""
    from zoneinfo import ZoneInfo

    local = until.astimezone(ZoneInfo(company.timezone))
    when = f"{fmt.day_month(local.date())}, {fmt.clock(local.time())}"
    return owner_email(
        "export_ready",
        to=to,
        subject="[Othor AI] Your export is ready",
        preheader=f"Everything Othor AI computed for {company.name}, in one zip · until {when}",
        company_name=company.name,
        slug=company.slug,
        settings=settings,
        kicker="Export ready",
        lead=(
            f"Your export of {company.name} is ready: your Business Model, briefs, findings, "
            "forecasts, numbers, settings and history, in one zip. No passwords, tokens or "
            "source rows."
        ),
        after=f"Sign in to download it. It is kept until {when}, then deleted.",
        actions=[
            (
                "Download the zip",
                settings.instance_url(company.slug, f"/settings/export/{job_id}"),
                True,
            )
        ],
    )
