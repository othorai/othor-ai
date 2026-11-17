# SPDX-License-Identifier: Apache-2.0
"""The monthly AI cap (open sign-up, owner 16 Nov 2026): past it, no new call reaches the
provider until the 1st (in the company's own months); cached answers still return; a company's
own cap overrides the install's; 0 means no cap; other companies' usage never counts."""

from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from othor.ai import usage
from othor.ai.gateway import ModelGateway, ProviderReply
from othor.ai.models import ModelCall
from othor.core.models import Company
from othor.core.tenancy import system_scope, tenant_scope

NOW = dt.datetime(2026, 11, 20, 6, 0, tzinfo=dt.UTC)


class Counting:
    name = "fake"
    configured = True

    def __init__(self) -> None:
        self.calls = 0

    def call(self, system, messages, schema, max_tokens=0):
        self.calls += 1
        return ProviderReply(json.dumps({"lines": []}), "fake-model", 400, 100)


def _used(sf, cid, tokens, at=NOW):
    with sf() as s, tenant_scope(cid):
        s.add(
            ModelCall(
                prompt_id="brief_rewrite",
                prompt_version="v1",
                provider="fake",
                model_id="m",
                input_hash="old",
                input={},
                output="x",
                tokens_in=tokens,
                tokens_out=0,
                latency_ms=1,
                guard_result="passed",
                created_at=at,
            )
        )
        s.commit()


def _gw(sf, p, cap):
    return ModelGateway(sf, p, clock=lambda: NOW, monthly_tokens=cap)


def test_under_the_cap_calls_go_through(session_factory, two_companies):
    a, _ = two_companies
    _used(session_factory, a, 400_000)
    p = Counting()
    r = _gw(session_factory, p, 500_000).complete(a, "brief_rewrite", "v1", {"x": 1})
    assert r.guard == "passed" and p.calls == 1


def test_at_the_cap_no_call_reaches_the_provider(session_factory, two_companies):
    a, b = two_companies
    _used(session_factory, a, 500_000)
    p = Counting()
    r = _gw(session_factory, p, 500_000).complete(a, "brief_rewrite", "v1", {"x": 2})
    assert r.guard == "capped" and r.output is None and p.calls == 0
    # the other company's month is its own
    r = _gw(session_factory, p, 500_000).complete(b, "brief_rewrite", "v1", {"x": 2})
    assert r.guard == "passed" and p.calls == 1


def test_last_month_does_not_count_and_zero_means_no_cap(session_factory, two_companies):
    a, _ = two_companies
    _used(session_factory, a, 900_000, at=dt.datetime(2026, 10, 30, 6, 0, tzinfo=dt.UTC))
    p = Counting()
    assert (
        _gw(session_factory, p, 500_000).complete(a, "brief_rewrite", "v1", {"x": 3}).guard
        == "passed"
    )
    _used(session_factory, a, 900_000)
    r = _gw(session_factory, p, 0).complete(a, "brief_rewrite", "v1", {"x": 4})
    assert r.guard == "passed"


def test_a_companys_own_cap_overrides_and_cached_answers_still_return(
    session_factory, two_companies
):
    a, _ = two_companies
    p = Counting()
    first = _gw(session_factory, p, 500_000).complete(a, "brief_rewrite", "v1", {"x": 5})
    assert first.guard == "passed" and p.calls == 1
    with session_factory() as s, system_scope("test"):
        s.execute(sa.update(Company).where(Company.id == a).values(ai_cap_tokens=100))
        s.commit()
    again = _gw(session_factory, p, 500_000).complete(a, "brief_rewrite", "v1", {"x": 5})
    assert again.guard == "passed" and again.cached and p.calls == 1  # cached: costs nothing
    new = _gw(session_factory, p, 500_000).complete(a, "brief_rewrite", "v1", {"x": 6})
    assert new.guard == "capped" and p.calls == 1


def test_resumes_on_the_first_of_next_month_in_the_companys_zone():
    ist = ZoneInfo("Asia/Kolkata")
    assert usage.resumes_on(ist, NOW) == dt.date(2026, 12, 1)
    # 31 Dec 8:00 PM UTC is already 1 Jan in India
    assert usage.resumes_on(ist, dt.datetime(2026, 12, 31, 20, 0, tzinfo=dt.UTC)) == dt.date(
        2027, 2, 1
    )
