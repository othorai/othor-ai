# SPDX-License-Identifier: Apache-2.0
"""The ceiling on metrics (M15.1): at most `max_metrics` in use per company, checked in apply()."""

from __future__ import annotations

import pytest

from othor.core.tenancy import tenant_scope
from othor.ontology import service
from tests.fixtures.sample_company.ontology import metrics

BODY = next(c.body for c in metrics() if c.body.get("formula") is None)


def _put(key: str, **extra: object) -> service.Change:
    return service.put("metric", key, {**BODY, "name": key, **extra})


@pytest.fixture
def two(monkeypatch):
    monkeypatch.setattr(service, "max_metrics", lambda: 2)


def test_the_metric_past_the_limit_is_refused(session_factory, two_companies, two):
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        service.apply(s, [_put("a"), _put("b")], actor="u", reason="setup")
        service.confirm(s, actor="u")
        with pytest.raises(service.MetricCeiling, match="2 metrics"):
            service.apply(s, [_put("c")], actor="u", reason="one more")
        # suggested and declined ones don't count; a swap keeps the count
        service.apply(s, [_put("d", status="suggested")], actor="u", reason="idea")
        service.apply(s, [service.retire("metric", "a"), _put("c")], actor="u", reason="swap")
        assert {r.key for r in service.active(s, ["metric"])} == {"b", "c", "d"}


def test_draft_setup_is_held_to_the_limit_too(session_factory, two_companies, two):
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid), pytest.raises(service.MetricCeiling):
        service.apply(s, [_put("a"), _put("b"), _put("c")], actor="u", reason="setup")


def test_a_company_already_over_the_limit_can_still_edit(
    session_factory, two_companies, monkeypatch
):
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        service.apply(s, [_put("a"), _put("b"), _put("c")], actor="u", reason="setup")
        service.confirm(s, actor="u")
        monkeypatch.setattr(service, "max_metrics", lambda: 2)
        assert service.apply(s, [_put("a", unit="count")], actor="u", reason="edit") is not None
        assert service.apply(s, [service.retire("metric", "c")], actor="u", reason="drop")
        with pytest.raises(service.MetricCeiling):
            service.apply(s, [_put("c")], actor="u", reason="back")


def test_the_default_is_60():
    assert service.max_metrics() == 60
