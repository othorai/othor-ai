# SPDX-License-Identifier: Apache-2.0
"""The AI usage page's counts (M15.8): every figure counted by code from the model call log, one
company's calls only, in the company's own months; the result in words; a label for every
prompt."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from othor.ai import usage
from othor.ai.models import ModelCall
from othor.core.tenancy import tenant_scope

IST = ZoneInfo("Asia/Kolkata")
PROMPTS = Path(__file__).resolve().parents[3] / "othor" / "ai" / "prompts"


def _call(at: dt.datetime, prompt="draft_metrics", tin=1000, tout=200, guard="passed", ms=900):
    return ModelCall(
        prompt_id=prompt,
        prompt_version="v2",
        provider="fake",
        model_id="claude-x",
        input_hash="h",
        input={"secret": "ROW VALUE 1234"},
        output="MODEL OUTPUT TEXT",
        tokens_in=tin,
        tokens_out=tout,
        latency_ms=ms,
        guard_result=guard,
        created_at=at,
    )


def _seed(sf, a, b):
    nov = dt.datetime(2026, 11, 10, 6, 0, tzinfo=dt.UTC)
    with sf() as s, tenant_scope(a):
        s.add_all(
            [
                _call(nov),
                _call(nov + dt.timedelta(hours=1), "brief_rewrite", 300, 80, "failed:numbers"),
                _call(nov + dt.timedelta(hours=2), "home_answer", 0, 0, "error"),
                # 31 Oct 8:00 PM UTC is 1 Nov 01:30 in India: November, in the company's months
                _call(dt.datetime(2026, 10, 31, 20, 0, tzinfo=dt.UTC), tin=50, tout=5),
                _call(dt.datetime(2026, 10, 5, 6, 0, tzinfo=dt.UTC), tin=7, tout=1),
            ]
        )
        s.commit()
    with sf() as s, tenant_scope(b):
        s.add(_call(nov, tin=99999, tout=99999))
        s.commit()


def test_totals_count_one_companys_calls_in_its_own_month(session_factory, two_companies):
    a, b = two_companies
    _seed(session_factory, a, b)
    with session_factory() as s, tenant_scope(a):
        t = usage.totals(s, dt.date(2026, 11, 1), IST)
        assert (t.calls, t.tokens_in, t.tokens_out) == (4, 1350, 285)
        assert t.tokens == 1635
        oct_ = usage.totals(s, dt.date(2026, 10, 1), IST)
        assert (oct_.calls, oct_.tokens_in) == (1, 7)
        assert usage.months(s, IST) == [dt.date(2026, 11, 1), dt.date(2026, 10, 1)]


def test_the_log_newest_first_in_words_with_no_input_or_output(session_factory, two_companies):
    a, b = two_companies
    _seed(session_factory, a, b)
    with session_factory() as s, tenant_scope(a):
        rows, more = usage.log(s, dt.date(2026, 11, 1), IST)
    assert not more and [r.task for r in rows] == [
        "Answer a question for you",
        "Smoother brief sentences",
        "Draft your metrics",
        "Draft your metrics",
    ]
    assert [r.result for r in rows[:2]] == ["Didn’t reach the model", "Not used · failed the check"]
    assert rows[2].when == dt.datetime(2026, 11, 10, 11, 30, tzinfo=IST)  # the company's time
    assert rows[2].prompt == "draft_metrics v2"
    for r in rows:  # the row carries counts only: never the input or the output
        assert "ROW VALUE" not in repr(r) and "MODEL OUTPUT" not in repr(r)


def test_the_log_filters_by_task_and_pages(session_factory, two_companies):
    a, _b = two_companies
    with session_factory() as s, tenant_scope(a):
        start = dt.datetime(2026, 11, 2, 6, 0, tzinfo=dt.UTC)
        s.add_all([_call(start + dt.timedelta(minutes=i)) for i in range(55)])
        s.add(_call(start, "brief_rewrite"))
        s.commit()
    with session_factory() as s, tenant_scope(a):
        first, more = usage.log(s, dt.date(2026, 11, 1), IST)
        assert len(first) == usage.PAGE == 50 and more
        second, more = usage.log(s, dt.date(2026, 11, 1), IST, page=2)
        assert len(second) == 6 and not more
        only, _ = usage.log(s, dt.date(2026, 11, 1), IST, task="brief_rewrite")
        assert [r.task for r in only] == ["Smoother brief sentences"]
        assert usage.tasks(s, dt.date(2026, 11, 1), IST) == [
            ("draft_metrics", "Draft your metrics"),
            ("brief_rewrite", "Smoother brief sentences"),
        ]


def test_every_prompt_has_a_label_in_the_owners_words():
    ids = sorted(p.name for p in PROMPTS.iterdir() if p.is_dir() and not p.name.startswith("_"))
    assert ids and [i for i in ids if i not in usage.LABEL] == []


def test_each_result_in_words():
    assert usage.result_words("passed") == "Used"
    assert usage.result_words("failed:not-json") == "Not used · failed the check"
    assert usage.result_words("refused") == "Not used · refused"
    assert usage.result_words("error") == "Didn’t reach the model"


def test_the_last_seven_days_for_the_menu_card(session_factory, two_companies):
    a, b = two_companies
    with session_factory() as s, tenant_scope(a):
        s.add_all(
            [
                _call(dt.datetime(2026, 11, 12, 6, 0, tzinfo=dt.UTC), tin=1000, tout=200),
                _call(dt.datetime(2026, 11, 12, 9, 0, tzinfo=dt.UTC), tin=500, tout=100),
                _call(dt.datetime(2026, 11, 14, 4, 0, tzinfo=dt.UTC), tin=300, tout=0),
                _call(dt.datetime(2026, 11, 1, 4, 0, tzinfo=dt.UTC), tin=99999),  # too old
            ]
        )
        s.commit()
    with session_factory() as s, tenant_scope(b):
        s.add(_call(dt.datetime(2026, 11, 13, 4, 0, tzinfo=dt.UTC), tin=77777))
        s.commit()
    with session_factory() as s, tenant_scope(a):
        days = usage.daily(s, IST, dt.date(2026, 11, 14), 7)
    assert [d for d, _ in days] == [dt.date(2026, 11, 8 + i) for i in range(7)]
    assert [t for _, t in days] == [0, 0, 0, 0, 1800, 0, 300]
    spec = usage.card_spec(days)
    assert spec.kind == "sparkline" and spec.series[0].style == "bar"
    assert spec.series[0].role == "muted"  # grey in the side menu (owner, 14 Nov 2026)
    month = usage.card_spec(days, colour=True)
    assert month.series[0].role == "positive"  # in colour on the page
    assert [v for _, v in spec.series[0].points] == [0, 0, 0, 0, 1800, 0, 300]
    assert "2,100 tokens in the last 7 days" in spec.alt_text
