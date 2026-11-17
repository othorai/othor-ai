# SPDX-License-Identifier: Apache-2.0
"""AI usage (M15.8, owner 13–14 Nov 2026; docs/plans/ai-usage.md): the company's model calls,
counted by code from the `model_call` log, in the company's own months. Tokens only, no cost.

The log gets a row when a call reaches the provider (and when it errors there); a template
fallback, an unconfigured provider or a refused input writes none, so the page shows what the
log proves. A row's input and output are never read here: the page shows the task and the
counts only. Callers hold a `tenant_scope`; the loader criteria keep other companies out."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.ai.models import ModelCall
from othor.charts.spec import ChartSpec, Series

PAGE = 50

# What each prompt does, in the owner's words: one for every prompt directory (a test keeps
# them in step); the website's measurements read the same words (scripts/measure_site.py).
LABEL = {
    "read_upload": "Read the documents you uploaded",
    "scan_table_label": "Describe the tables it found",
    "business_draft": "Draft how your business works",
    "draft_metrics": "Draft your metrics",
    "document_changes": "Read changes from a document",
    "metric_edit": "Change a metric in your words",
    "tab_fix": "Fix a sheet tab",
    "brief_rewrite": "Smoother brief sentences",
    "brief_draft": "Draft a new brief",
    "brief_edit": "Change a brief in your words",
    "brief_suggest": "Suggest briefs",
    "home_answer": "Answer a question for you",
    "conflict_chat": "Talk through a decision",
    "proposal_chat": "Talk through a proposal",
    "ontology_change": "Change the Ontology in your words",
    "metric_connect": "Connect a metric to its data",
    "metric_where": "Find where a metric lives",
    "suggest_pairs": "Suggest numbers that move together",
    "metric_areas": "Group your numbers into areas",
    "big_picture": "Big Picture report",
}


def result_words(guard: str) -> str:
    if guard == "passed":
        return "Used"
    if guard.startswith("failed"):
        return "Not used · failed the check"
    if guard == "refused":
        return "Not used · refused"
    return "Didn’t reach the model"


@dataclass(frozen=True)
class Totals:
    calls: int
    tokens_in: int
    tokens_out: int

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out


@dataclass(frozen=True)
class Row:
    when: dt.datetime  # in the company's time zone
    task: str
    prompt: str  # "draft_metrics v2", for support
    model: str
    tokens_in: int
    tokens_out: int
    seconds: float
    result: str


def _bounds(month: dt.date, tz: ZoneInfo) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime(month.year, month.month, 1, tzinfo=tz)
    nxt = dt.datetime(month.year + month.month // 12, month.month % 12 + 1, 1, tzinfo=tz)
    return start.astimezone(dt.UTC), nxt.astimezone(dt.UTC)


def _in(month: dt.date, tz: ZoneInfo) -> list[sa.ColumnElement[bool]]:
    a, b = _bounds(month, tz)
    return [ModelCall.created_at >= a, ModelCall.created_at < b]


def _aware(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)  # SQLite hands back naive UTC


def totals(s: Session, month: dt.date, tz: ZoneInfo) -> Totals:
    calls, tin, tout = s.execute(
        sa.select(
            sa.func.count(),
            sa.func.coalesce(sa.func.sum(ModelCall.tokens_in), 0),
            sa.func.coalesce(sa.func.sum(ModelCall.tokens_out), 0),
        ).where(*_in(month, tz))
    ).one()
    return Totals(int(calls), int(tin), int(tout))


def months(s: Session, tz: ZoneInfo) -> list[dt.date]:
    """The company's months that have calls, newest first."""
    lo, hi = s.execute(
        sa.select(sa.func.min(ModelCall.created_at), sa.func.max(ModelCall.created_at))
    ).one()
    if lo is None:
        return []
    m = _aware(lo).astimezone(tz).date().replace(day=1)
    last = _aware(hi).astimezone(tz).date().replace(day=1)
    out = []
    while m <= last:
        if totals(s, m, tz).calls:
            out.append(m)
        m = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out[::-1]


def tasks(s: Session, month: dt.date, tz: ZoneInfo) -> list[tuple[str, str]]:
    """(prompt id, words) for the month's tasks, most calls first: the Task filter."""
    got = s.execute(
        sa.select(ModelCall.prompt_id, sa.func.count().label("n"))
        .where(*_in(month, tz))
        .group_by(ModelCall.prompt_id)
        .order_by(sa.desc("n"), ModelCall.prompt_id)
    ).all()
    return [(p, LABEL.get(p, p)) for p, _n in got]


def daily(s: Session, tz: ZoneInfo, today: dt.date, days: int = 7) -> list[tuple[dt.date, int]]:
    """Tokens (in + out) on each of the last `days` days to `today`, in the company's days;
    a day with no calls is 0. The side menu's card."""
    first = today - dt.timedelta(days=days - 1)
    a = dt.datetime.combine(first, dt.time(), tzinfo=tz).astimezone(dt.UTC)
    b = dt.datetime.combine(today + dt.timedelta(days=1), dt.time(), tzinfo=tz).astimezone(dt.UTC)
    got = s.execute(
        sa.select(ModelCall.created_at, ModelCall.tokens_in, ModelCall.tokens_out).where(
            ModelCall.created_at >= a, ModelCall.created_at < b
        )
    ).all()
    per = {first + dt.timedelta(days=i): 0 for i in range(days)}
    for at, tin, tout in got:
        d = _aware(at).astimezone(tz).date()
        if d in per:
            per[d] += int(tin) + int(tout)
    return sorted(per.items())


def card_spec(days: list[tuple[dt.date, int]], *, colour: bool = False) -> ChartSpec:
    """Tokens per day as bars (a ChartSpec, rendered by render_svg): grey in the side menu's
    card, in colour as the AI usage page's first section (owner, 14 Nov 2026)."""
    from othor.core import fmt

    total = sum(t for _, t in days)
    busiest = max(days, key=lambda x: (x[1], x[0])) if days else None
    alt = f"{fmt.num(total)} tokens in the last {fmt.num(len(days))} days"
    if busiest and busiest[1]:
        alt += f"; the busiest day {fmt.day_month(busiest[0])}"
    return ChartSpec(
        id=f"ai-usage-{len(days)}d",
        kind="sparkline",
        title=f"AI usage, last {len(days)} days",
        alt_text=alt,
        series=(
            Series(
                "tokens",
                "Tokens",
                "positive" if colour else "muted",
                tuple((d.isoformat(), t) for d, t in days),
                style="bar",
            ),
        ),
    )


def log(
    s: Session, month: dt.date, tz: ZoneInfo, task: str | None = None, page: int = 1
) -> tuple[list[Row], bool]:
    """One page of the month's calls, newest first, and whether there are more. Only the
    counting columns are read: never the input or the output."""
    q = sa.select(
        ModelCall.created_at,
        ModelCall.prompt_id,
        ModelCall.prompt_version,
        ModelCall.model_id,
        ModelCall.tokens_in,
        ModelCall.tokens_out,
        ModelCall.latency_ms,
        ModelCall.guard_result,
    ).where(*_in(month, tz))
    if task:
        q = q.where(ModelCall.prompt_id == task)
    page = max(int(page), 1)
    got = s.execute(
        q.order_by(ModelCall.created_at.desc(), ModelCall.id.desc())
        .offset((page - 1) * PAGE)
        .limit(PAGE + 1)
    ).all()
    rows = [
        Row(
            when=_aware(r.created_at).astimezone(tz),
            task=LABEL.get(r.prompt_id, r.prompt_id),
            prompt=f"{r.prompt_id} {r.prompt_version}",
            model=r.model_id or "–",
            tokens_in=int(r.tokens_in),
            tokens_out=int(r.tokens_out),
            seconds=round(int(r.latency_ms) / 1000, 1),
            result=result_words(r.guard_result),
        )
        for r in got[:PAGE]
    ]
    return rows, len(got) > PAGE


# --- the monthly cap (open sign-up, owner 16 Nov 2026) ----------------------------------------


def cap_tokens(company_cap: int | None, default: int) -> int:
    """The company's monthly token cap: its own, else the install's; 0 = no cap."""
    return company_cap if company_cap is not None else default


def used_this_month(s: Session, tz: ZoneInfo, now: dt.datetime) -> int:
    """Tokens this company used this month (its own time zone), from the call log."""
    month = _aware(now).astimezone(tz).date().replace(day=1)
    return totals(s, month, tz).tokens


def over_cap(
    s: Session, company_cap: int | None, default: int, tz: ZoneInfo, now: dt.datetime
) -> bool:
    cap = cap_tokens(company_cap, default)
    return cap > 0 and used_this_month(s, tz, now) >= cap


def resumes_on(tz: ZoneInfo, now: dt.datetime) -> dt.date:
    """The 1st of next month in the company's zone: when a capped company's drafting resumes."""
    d = _aware(now).astimezone(tz).date()
    return dt.date(d.year + d.month // 12, d.month % 12 + 1, 1)
