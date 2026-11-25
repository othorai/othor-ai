# SPDX-License-Identifier: Apache-2.0
"""Numbers suggested for the company's size and goals (#76) on the sample's 1 Nov night: the band
(₹10–100 Cr) and the suggestions with their computed figures, in To review on the Business Model
page (Accept writes the number as a new version, applied from tonight's run), the top one on
Home's Suggested by Othor AI (Not now in place, with Undo), the levels checklist, and a replay of
the night that writes the same `size`. Needs `make dev && make seed`."""

from __future__ import annotations

import html
import json
import re
from decimal import Decimal

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from othor.engine.context import EngineContext
from othor.engine.models import Proposal, Run
from othor.engine.replay import replay
from othor.engine.store import RunFiles
from othor.ontology import service, size_numbers
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def _size(svc, cid, rid) -> dict:
    files = RunFiles(svc.settings.runs_dir(cid), rid)
    return json.loads(files.outputs_json.read_bytes())["size"]


def _open(svc, cid) -> list[Proposal]:
    with svc.sf() as s, tenant_scope(cid):
        rows = size_numbers.open_rows(s)
        s.expunge_all()
        return rows


def _post(env, h, path, page="/ontology"):
    tok = env.csrf(h + page)
    return env.client.post(h + path, data={"csrf": tok}, follow_redirects=False)


def _text(page: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", page)))


def test_the_sample_is_a_10_to_100_crore_company_with_its_suggestions(night) -> None:  # noqa: F811
    _env, _h, cid, rid, svc = night
    sz = _size(svc, cid, rid)
    assert sz["band"] == "10_100" and sz["metric"] == "net_revenue"
    assert sz["months"] == 12 and sz["through"] == "2026-10-01"
    assert Decimal(sz["crore"]) == Decimal("56.44")
    f = sz["figures"]
    # the 10 largest named customers (the cap’s “Other” is never one of them): 35%
    assert round(Decimal(f["top10_share"]["value"]) * 100) == 35
    assert f["top10_share"]["count"] == 50 and f["top10_share"]["more"]
    assert round(Decimal(f["gross_margin_pct"]["value"]) * 100) == 36
    rows = _open(svc, cid)
    keys = [p.evidence["key"] for p in rows]
    assert keys[:3] == ["top10_share", "gross_margin_pct", "revenue_per_active_customer"]
    assert all(p.evidence["status"] == "now" for p in rows[:3])
    top = rows[0].evidence
    assert top["figure_parts"][1] == "35%"
    assert top["goal"]["key"] == "fy27_net_revenue"


def test_the_business_model_page_lists_them_first_with_the_size(night) -> None:  # noqa: F811
    env, h, _cid, _rid, _svc = night
    page = env.client.get(h + "/ontology").text
    t = _text(page)
    assert "Net revenue in the 12 months to Oct 2026: ₹56.4 Cr." in t
    assert "Othor AI suggests the numbers that matter most to a ₹10–100 Cr company" in t
    review = page[page.index('id="proposals"') :]
    first = review.index("Add “Top 10 customers’ share of net revenue”")
    assert (
        first
        < review.index("Add “Gross margin %”")
        < review.index("Add “Net revenue per active customer”")
    )
    assert "<b>35%</b>" in review
    assert "New number" in review and "Othor AI · for your size" in review


def test_accept_writes_the_number_and_it_compiles(night) -> None:  # noqa: F811
    env, h, cid, _rid, svc = night
    top = _open(svc, cid)[0]
    with svc.sf() as s, tenant_scope(cid):
        before = service.current_number(s)
    r = _post(env, h, f"/home/proposals/{top.id}/accept?back=ontology")
    assert r.status_code == 303 and f"decided={top.id}" in r.headers["location"]
    with svc.sf() as s, tenant_scope(cid):
        assert service.current_number(s) == before + 1
        rec = next(r for r in service.active(s, ["metric"]) if r.key == "top10_share")
        assert rec.body["formula"]["fn"] == "top_share"
        from othor.ontology.models import Spec

        spec = s.scalars(sa.select(Spec).order_by(Spec.created_at.desc())).first()
        assert spec.compiled["metrics"]["top10_share"]["type"] == "derived"
        assert spec.compiled["metrics"]["top10_share"]["queries"]
    page = env.client.get(h + r.headers["location"]).text
    assert "Accepted · applies from tonight’s run" in _text(page)


def test_home_shows_the_top_one_and_not_now_sets_it_aside(night) -> None:  # noqa: F811
    env, h, cid, _rid, svc = night
    top = _open(svc, cid)[0]
    page = env.client.get(h + "/home").text
    card = page[page.index(f'data-row="size:{top.id}"') :]
    t = _text(card[:3000])
    assert "Watch how much your 10 largest customers carry" in t
    assert "35%" in t and "Review" in t and "Not now" in t
    r = _post(env, h, f"/home/proposals/{top.id}/decline?back=home", page="/home")
    assert r.status_code == 303 and r.headers["location"] == f"/home?done={top.id}"
    page = env.client.get(h + r.headers["location"]).text
    assert "Not now · Othor AI suggests it again when your numbers change" in _text(page)
    assert f'data-row="size:{top.id}"' not in page  # the next one leads now
    with svc.sf() as s, tenant_scope(cid):
        assert s.get(Proposal, top.id).status == "declined"
    r = _post(env, h, f"/home/proposals/{top.id}/undo?back=home", page="/home")
    with svc.sf() as s, tenant_scope(cid):
        assert s.get(Proposal, top.id).status == "open"


def test_a_replay_of_the_night_writes_the_same_size(night) -> None:  # noqa: F811
    env, _h, cid, rid, svc = night
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    with tenant_scope(cid):
        got = replay(ctx, rid)
    assert got.ok, got.diff
    with svc.sf() as s, tenant_scope(cid):
        assert s.scalars(sa.select(Run.output_hash).where(Run.id == rid)).one() == got.replayed_hash
