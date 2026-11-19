# SPDX-License-Identifier: Apache-2.0
"""0.28 (M28.3): a goal drafted from a file's quoted target shows in the Business Model draft
with its source, in place of the quote; Confirm writes it as a goal record, unless its number
was skipped."""

from __future__ import annotations

import html

import sqlalchemy as sa

from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from othor.ontology import service
from othor.ontology.types import validate_body
from othor.setup import confirm, goal_drafts
from othor.setup.models import SetupDraft
from tests.integration import setup_world as w
from tests.web.test_chat_web import _drafted, need_sources, sample  # noqa: F401

QUOTE = "Net revenue: ₹60 Cr in FY27, by 31 Mar 2027."
CITE = "Board-review-Q2-FY27 · slide 4"


def _goal(metric: str, target: str) -> dict:  # type: ignore[type-arg]
    return validate_body(
        "goal",
        {
            "name": metric,
            "metric": metric,
            "target": target,
            "direction": "at_least",
            "by": "2027-03-31",
            "aggregate": "fiscal_year_total",
        },
    )


def test_a_drafted_goal_shows_with_its_source_and_confirm_writes_it(
    env,
    sample,  # noqa: F811
    need_sources,  # noqa: F811
    tmp_path,
):
    h, cid = _drafted(env, sample, tmp_path)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, tenant_scope(cid):
        metrics = {
            r.key: r for r in s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "metric"))
        }
        assert "net_revenue" in metrics
        other = next(
            k for k in metrics if k != "net_revenue" and metrics[k].confidence != "conflict"
        )
        metrics[other].state = "skipped"  # its goal isn't written
        goal_drafts.store(
            s,
            [
                ("net_revenue_by_2027_03_31", _goal("net_revenue", "600000000"), CITE, QUOTE),
                (f"{other}_by_2027_03_31", _goal(other, "1"), CITE, "Other target 1"),
            ],
        )
        s.commit()
    page = html.unescape(env.client.get(h + "/setup/metrics?panel=ontology").text)
    goals = page.split("2 · Goals", 1)[1].split("3 · Numbers", 1)[0]  # its own section
    assert "Net revenue at least ₹60 Cr for the year to 31 Mar 2027" in goals
    assert CITE in page and QUOTE not in page  # the goal stands in for its quote
    with svc.sf() as s, tenant_scope(cid):
        for r in s.scalars(sa.select(SetupDraft).where(SetupDraft.kind == "metric")):
            if r.confidence == "conflict":
                confirm.choose(s, r, "source", actor=w.OWNER)
        confirm.confirm_setup(s, s.get(Company, cid), actor=w.OWNER, today=w.NOW.date())
        s.commit()
        goals = {r.key: r.body for r in service.active(s, ["goal"])}
    assert set(goals) == {"net_revenue_by_2027_03_31"}
    assert goals["net_revenue_by_2027_03_31"]["target"] == "600000000"


def test_the_draft_job_asks_once_and_keeps_only_the_quotes_figure(
    env,
    sample,  # noqa: F811
    need_sources,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    import datetime as dt
    from types import SimpleNamespace

    from othor.jobs import draft_job
    from othor.jobs.runner import WorkerContext
    from othor.setup.parse import Ref

    _h, cid = _drafted(env, sample, tmp_path)
    svc = env.app.state.services  # type: ignore[attr-defined]
    quotes = [
        ("Board-review-Q2-FY27.pdf", Ref("target", "slide 4", QUOTE)),
        ("Board-review-Q2-FY27.pdf", Ref("target", "slide 4", "DSO under 45 days by 31 Mar 2027.")),
    ]
    monkeypatch.setattr(goal_drafts, "quotes", lambda refs: quotes)
    calls = []

    class Gateway:
        def complete(self, company_id, prompt, version, inputs, **kw):  # type: ignore[no-untyped-def]
            calls.append((prompt, version, inputs))
            assert "rows" not in inputs  # names and the quotes only
            return SimpleNamespace(
                ok=True,
                output={
                    "goals": [
                        {
                            "quote": 0,
                            "metric": "net_revenue",
                            "target": "60 Cr",
                            "by": None,
                            "direction": None,
                            "aggregate": None,
                        },
                        {
                            "quote": 1,
                            "metric": "dso",
                            "target": "40 days",
                            "by": None,
                            "direction": "at_most",
                            "aggregate": "value",
                        },  # not the quote's 45
                    ]
                },
            )

    ctx = WorkerContext(svc.settings, svc.sf, svc.key, clock=svc.clock)
    ctx.gateway = Gateway()
    draft_job._goals(ctx, cid, True, dt.date(2026, 11, 1))
    assert [c[:2] for c in calls] == [("goal_from_targets", "v1")]
    with svc.sf() as s, tenant_scope(cid):
        rows = {r.key: r for r in goal_drafts.shown(s)}
    assert set(rows) == {"net_revenue_by_2027_03_31"}
    assert rows["net_revenue_by_2027_03_31"].body["target"] == "600000000"
    assert rows["net_revenue_by_2027_03_31"].origin["cite"] == CITE
    # without a model: nothing asked, the drafts stay as they were
    calls.clear()
    draft_job._goals(ctx, cid, False, dt.date(2026, 11, 1))
    assert calls == []
