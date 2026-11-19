# SPDX-License-Identifier: Apache-2.0
"""Sweep history (0.28.1, owner 19 Nov 2026): every night since the first, a month at a time,
from what each night stored (its summary, its findings' amounts, the replay events); any old run
opens again at /sweep?run=N."""

from __future__ import annotations

import html
from decimal import Decimal

import sqlalchemy as sa

from othor.core.events import record_event
from othor.core.tenancy import tenant_scope
from othor.engine.models import Finding, Run
from othor.web import sweep_view as sv
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def _runs(svc, cid):  # type: ignore[no-untyped-def]
    with svc.sf() as s, tenant_scope(cid):
        return [
            (r.number, r.run_date, r.kind, dict(r.summary or {}), r.id)
            for r in s.scalars(sa.select(Run).order_by(Run.number))
        ]


def test_the_page_lists_each_night_with_its_counts_money_and_replay(night):  # noqa: F811
    env, h, cid, rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        run = s.get(Run, rid)
        sweep = dict((run.summary or {}).get("sweep") or {})
        total = sum(
            Decimal(str((b or {}).get("money") or 0))
            for b in s.scalars(
                sa.select(Finding.body).where(Finding.run_id == rid, Finding.status == "open")
            )
        )
        record_event(
            s,
            actor="system",
            action="run.replayed",
            object_type="run",
            object_id=str(rid),
            after={"ok": True},
        )
        s.commit()
        number = run.number
        month = sv.history_month(s, run.run_date.replace(day=1))
    row = next(r for r in month["rows"] if r["number"] == number)
    assert row["open"] == sweep["findings"] and row["new"] == sweep["new"]
    assert row["tests"] == sweep["tests"] and row["replays"] == "Same bytes"
    assert total > 0 and row["money"] != "–"
    assert f"{sweep['new']} new findings" in month["line"]
    page = html.unescape(env.client.get(h + "/sweep/history").text)
    assert "Sweep history" in page and "Every night since" in page
    assert "starts after a week of nights" in page and "November 2026" in page  # one night
    assert f"/sweep?run={number}" in page and "Same bytes" in page


def test_a_bad_month_is_not_found_and_sweep_links_to_history(night):  # noqa: F811
    env, h, *_ = night
    assert env.client.get(h + "/sweep/history?month=2026-13").status_code == 404
    page = env.client.get(h + "/sweep").text
    assert 'href="/sweep/history"' in page and "All runs →" in page


def test_an_old_run_opens_however_far_back(night, monkeypatch):  # noqa: F811
    env, h, cid, _rid, svc = night
    detecting = [r for r in _runs(svc, cid) if r[2] in ("nightly", "dry", "rerun")]
    oldest = detecting[0][0]
    monkeypatch.setattr(sv, "RUNS_SHOWN", 1)  # the menu shows only the latest now
    with svc.sf() as s, tenant_scope(cid):
        assert sv.run_by_number(s, oldest) is not None
    r = env.client.get(h + f"/sweep?run={oldest}")
    assert r.status_code == 200 and "There’s no run" not in r.text


def test_the_chart_shows_from_a_week_of_nights(night, monkeypatch):  # noqa: F811
    env, h, cid, _rid, svc = night
    monkeypatch.setattr(sv, "HISTORY_CHART_MIN", 1)
    with svc.sf() as s, tenant_scope(cid):
        spec = sv.history_chart(s)
    assert spec is not None and spec.series[0].style == "bar"
    assert spec.alt_text.startswith("Findings open each night")
    page = html.unescape(env.client.get(h + "/sweep/history").text)
    assert "Findings open each night" in page
