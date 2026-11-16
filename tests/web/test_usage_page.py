# SPDX-License-Identifier: Apache-2.0
"""AI usage (M15.8, owner 13–14 Nov 2026): the side-menu link above the account menu and the
page: tiles, the log in the owner's words, the month and task filters in place, Show more, the
empty state; never a prompt's input or a model's output."""

from __future__ import annotations

import datetime as dt
import re

import sqlalchemy as sa

from othor.ai.models import ModelCall
from othor.core.models import Company
from othor.core.tenancy import system_scope, tenant_scope
from tests.web.conftest import Env, signup

SECRET_IN, SECRET_OUT = "ROW VALUE 9917", "MODEL OUTPUT ZEBRA"


def _seed(env: Env, n: int = 3, at: dt.datetime | None = None, prompt: str = "draft_metrics"):
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, system_scope("test"):
        cid = s.scalars(sa.select(Company.id).where(Company.slug == "indigothread")).one()
    start = at or dt.datetime(2026, 11, 2, 6, 0, tzinfo=dt.UTC)
    with svc.sf() as s, tenant_scope(cid):
        for i in range(n):
            s.add(
                ModelCall(
                    prompt_id=prompt,
                    prompt_version="v2",
                    provider="fake",
                    model_id="claude-opus-5-5",
                    input_hash=f"h{i}",
                    input={"line": SECRET_IN},
                    output=SECRET_OUT,
                    tokens_in=1000,
                    tokens_out=250,
                    latency_ms=1400,
                    guard_result="passed",
                    created_at=start + dt.timedelta(minutes=i),
                )
            )
        s.commit()


def test_the_link_sits_above_the_account_menu(env: Env) -> None:
    signup(env)
    home = env.client.get(env.host("indigothread") + "/home").text
    link = home.index('href="/usage"')
    assert link < home.index('class="account"')
    assert re.search(r'<a class="nav-usage" href="/usage">.*?AI usage</span>', home, re.S)
    assert 'hx-get="/usage/card" hx-trigger="load"' in home  # filled after the page loads
    page = env.client.get(env.host("indigothread") + "/usage").text
    assert '<a class="nav-usage is-on" href="/usage" aria-current="page">' in page


def test_the_card_shows_seven_days_of_tokens(env: Env) -> None:
    """Owner, 14 Nov 2026: a number and the last 7 days, a bar a day (the clock is 4 Nov)."""
    signup(env)
    _seed(env, 3, dt.datetime(2026, 11, 2, 6, 0, tzinfo=dt.UTC))  # 3 × 1,250 on 2 Nov
    _seed(env, 1, dt.datetime(2026, 10, 20, 6, 0, tzinfo=dt.UTC))  # outside the 7 days
    card = env.client.get(env.host("indigothread") + "/usage/card").text
    assert '<span class="nav-usage-num">3.8k</span>' in card
    assert card.count("<rect") == 7 and "tokens · last 7 days" not in card  # no caption
    assert 'aria-label="3,750 tokens in the last 7 days; the busiest day 2 Nov"' in card
    assert SECRET_IN not in card and SECRET_OUT not in card


def test_the_page_counts_the_month_in_the_owners_words(env: Env) -> None:
    signup(env)
    signup_calls = _count(env)
    _seed(env, 3)
    _seed(env, 1, dt.datetime(2026, 10, 5, 6, 0, tzinfo=dt.UTC))  # last month
    page = env.client.get(env.host("indigothread") + "/usage").text
    assert "<h1>AI usage</h1>" in page
    n = 3 + signup_calls["nov"]
    tokens = 3 * 1250 + signup_calls["nov_tokens"]
    assert f'<span class="v">{tokens:,}</span>' in page
    assert f'<span class="v">{n}</span><span class="l">calls' in page
    assert "Draft your metrics" in page and "claude-opus-5-5" in page and "1.4 s" in page
    # the first section: tokens per day for the last 30 days, a bar a day, in colour
    first = page.index('class="use-trend"')
    assert first < page.index('class="use-stats"')
    trend = page[first : page.index("</section>", first)]
    assert trend.count("<rect") == 30 and "Last 30 days" in trend
    assert "Numbers in your briefs never come from a model." in page
    assert SECRET_IN not in page and SECRET_OUT not in page  # never the input or the output
    oct_ = env.client.get(env.host("indigothread") + "/usage?month=2026-10").text
    assert '<option value="2026-10" selected>October 2026</option>' in oct_
    assert f'<span class="v">{1250 + signup_calls["oct_tokens"]:,}</span>' in oct_


def test_the_task_filter_and_show_more(env: Env) -> None:
    signup(env)
    _seed(env, 60)
    _seed(env, 2, prompt="brief_rewrite")
    h = env.host("indigothread")
    page = env.client.get(h + "/usage").text
    assert 'hx-get="/usage"' in page and "Show more" in page
    more = env.client.get(h + "/usage?upto=2", headers={"HX-Request": "true"}).text
    assert more.count('<tr class="use-row"') > page.count('<tr class="use-row"')
    only = env.client.get(h + "/usage?task=brief_rewrite").text
    assert only.count('<tr class="use-row"') == 2 and "Smoother brief sentences" in only
    assert env.client.get(h + "/usage?month=nope").status_code == 404


def test_no_ai_use_this_month_is_said(env: Env) -> None:
    signup(env)
    page = env.client.get(env.host("indigothread") + "/usage?month=2026-12").text
    assert "No AI use this month" in page


def _count(env: Env) -> dict[str, int]:
    """Calls the sign-up itself may have made (none with the fake gateway, but count them)."""
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, system_scope("test"):
        rows = s.scalars(sa.select(ModelCall)).all()
    nov = [r for r in rows if r.created_at.month == 11]
    oct_ = [r for r in rows if r.created_at.month == 10]
    return {
        "nov": len(nov),
        "nov_tokens": sum(r.tokens_in + r.tokens_out for r in nov),
        "oct_tokens": sum(r.tokens_in + r.tokens_out for r in oct_),
    }
