# SPDX-License-Identifier: Apache-2.0
"""Sweep history (0.28.1, owner 19 Nov 2026): every night since the first, the last 10 first and
older ones added in place (0.28.4),
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
        month = sv.history_nights(s)
    row = next(r for r in month["rows"] if r["number"] == number)
    assert row["open"] == sweep["findings"] and row["new"] == sweep["new"]
    assert row["tests"] == sweep["tests"] and row["replays"] == "Same bytes"
    assert total > 0 and row["money"] != "–"
    assert f"{sweep['new']} new findings" in month["line"]
    page = html.unescape(env.client.get(h + "/sweep/history").text)
    assert "Sweep history" in page and "Every night since" in page
    assert "starts after a week of nights" in page and "Show older nights" not in page
    assert f"/sweep?run={number}" in page and "Same bytes" in page


def test_sweep_has_one_history_button_and_older_nights_load_in_place(night, monkeypatch):  # noqa: F811
    """0.28.4 (owner, 19 Nov 2026): one "History" button on Sweep (no "Last 7 runs" menu); the
    page shows the last 10 nights, each dated as Sweep dates it and saying its kind (#39), and
    "Show older nights" swaps the list for a longer one in place."""
    from othor.core import fmt

    env, h, cid, _rid, svc = night
    page = env.client.get(h + "/sweep").text
    assert 'href="/sweep/history"' in page and "Last 7 runs" not in page
    monkeypatch.setattr(sv, "HISTORY_FIRST", 1)
    monkeypatch.setattr(sv, "HISTORY_MORE", 1)
    real = sv._history_runs
    monkeypatch.setattr(sv, "_history_runs", lambda s: real(s) * 2)  # the sample has one night
    with svc.sf() as s, tenant_scope(cid):
        runs = sv._history_runs(s)
        first = sv.history_nights(s, 1)
        assert len(first["rows"]) == 1 and first["more"] == len(runs) - 1 and first["next"] == 1
        dates = [(fmt.date_short(r.run_date), sv.RUN_KIND.get(r.kind, "")) for r in runs]
    page = html.unescape(env.client.get(h + "/sweep/history").text)
    assert "Show older nights" in page and 'hx-get="/sweep/history?shown=2"' in page
    assert 'hx-select="#history-body"' in page and 'id="history-body"' in page
    page = html.unescape(env.client.get(h + "/sweep/history?shown=2").text)
    for d, kind in dates[:2]:
        assert d in page and kind in page


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
