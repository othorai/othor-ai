# SPDX-License-Identifier: Apache-2.0
"""Summary 1.1 on the sample company's 1 Nov night (owner, 21 Nov 2026): the summary card read
from the run's stored Sweep results, the lines' marks, the numbers waiting for data folded into
one line, and the look-alike numbers' merge draft (Home's same-number proposal, in the panel)."""

from __future__ import annotations

import datetime as dt
import re

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from othor.engine.models import Proposal
from othor.web import summary_view as sv
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

TODAY = dt.date(2026, 11, 1)


def _post(env, h, path, data):
    tok = env.csrf(h + "/summary")
    return env.client.post(h + path, data={"csrf": tok, **data}, follow_redirects=False)


def _card(page: str) -> str:
    return page.split('<section class="db-summary sum-card"', 1)[1].split("</section>", 1)[0]


def test_the_card_reads_the_stored_run(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        out = sv.page(s, svc.settings, TODAY, expanded=True)
    c = out["card"]
    assert c["kicker"] == "At a glance · 1 Quarter"
    # 21 Nov 2026: the week of 12 Oct was unusually low for Metres dispatched and Net revenue;
    # the rebound is said as one (never "+377.6%"), and no change reads as a percent over 100
    # or with a decimal at 10% or more
    assert c["lead"].startswith("Metres dispatched bounced back to ")
    assert c["lead"].endswith(").") and "after an unusually low week (" in c["lead"]
    assert "Net revenue bounced back after an unusually low week." in (c["text"] or "")
    for t in (c["lead"], c["text"] or ""):
        assert not re.search(r"\d{3,}(\.\d)?%|\d{2,}\.\d%", t), t
    with svc.sf() as s, tenant_scope(cid):
        rows = sv.page(s, svc.settings, TODAY)["rows"]
    for r in rows:
        assert not re.search(r"\d{3,}(\.\d)?%|\d{2,}\.\d%", r["change_text"]), r["change_text"]
    assert [col["label"] for col in c["cols"]] == [
        "Between the numbers",
        "When lines shifted",
        "Same numbers twice",
    ]
    together = c["cols"][0]["lines"]
    # the planted freight break (docs/11): Sweep's broken relationship passed on 1 Nov
    assert together[0].startswith(
        "Freight expense and Metres dispatched moved together until the week of 14 Sep."
    )
    shifted = c["cols"][1]["lines"]
    assert shifted[0].startswith("Receivables over 60 days stepped up in the week of 10 Aug")
    alikes = c["cols"][2]["alikes"]
    assert {(a["keep"], a["drop"]) for a in alikes} == {
        ("invoiced_value", "line_revenue"),
        ("orders", "orders_segment"),
    }
    assert all(a["proposal"] for a in alikes)  # the night drafted both
    # plain words on the card; the technical ones behind the (i)
    from tests.guards.test_plain_words import _texts, offenders

    # (the numbers' own names are the owner's, from the Business Model: "DSO" stays theirs)
    with svc.sf() as s, tenant_scope(cid):
        from othor.web.ontology_view import run_data

        names = [
            str(m.get("name") or k) for k, m in run_data(s, svc.settings).spec["metrics"].items()
        ]
    texts = []
    for k, t in _texts(c):
        for n in sorted(names, key=len, reverse=True):
            t = t.replace(n, "")
        texts.append((k, t))
    assert offenders(texts) == []
    assert "Chow" in c["cols"][0]["info"]

    p = env.client.get(h + "/summary").text
    card = _card(p)
    assert "At a glance · 1 Quarter" in card and "Between the numbers" in card
    assert 'class="info"' in card  # the technical word is one click away
    assert card.count("Merge in the Business Model") == 2
    assert 'href="/summary?panel=othor&amp;subject=proposal:' in card and "data-panel" in card


def test_the_numbers_waiting_for_data_fold_into_one_line(night):  # noqa: F811
    env, h, *_ = night
    p = env.client.get(h + "/summary").text
    assert p.count('<details class="sum-wait">') == 1
    fold = p.split('<details class="sum-wait">', 1)[1].split("</details>", 1)[0]
    assert "2 numbers waiting for data" in fold
    assert fold.count("Needs a source") == 2
    assert "No data yet" not in p.split('<details class="sum-wait">', 1)[0]


def test_a_line_marks_the_week_it_shifted_and_its_high_and_low(night):  # noqa: F811
    env, h, *_ = night
    _post(
        env, h, "/summary/view", {"window": "13w", "show": "actual", "split": "", "rows": "numbers"}
    )
    p = env.client.get(h + "/summary").text
    row = p.split('id="num-receivables_over_60"', 1)[1].split("</details>", 1)[0]
    assert ">10 Aug</text>" in row  # the turning point, dated
    assert re.search(r">High [^<]+</text>", row) and re.search(r">Low [^<]+</text>", row)
    assert "<title>" in row  # hover values


def test_merge_opens_the_draft_and_changes_nothing(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        pid = s.scalars(
            sa.select(Proposal.id).where(Proposal.fingerprint == "duplicate:orders:orders_segment")
        ).one()
    p = env.client.get(h + f"/summary?panel=othor&subject=proposal:{pid}").text
    assert "Orders by segment gives the same numbers as Orders" in p and "Apply" in p
    # no draft yet (the night before the rule): the button drafts it, then opens it
    with svc.sf() as s, tenant_scope(cid):
        s.execute(sa.delete(Proposal).where(Proposal.kind == "duplicate_metric"))
        s.commit()
    card = _card(env.client.get(h + "/summary").text)
    assert 'action="/summary/merge"' in card
    r = _post(env, h, "/summary/merge", {"keep": "orders", "drop": "orders_segment"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/summary?panel=othor&subject=proposal:")
    with svc.sf() as s, tenant_scope(cid):
        made = s.scalars(
            sa.select(Proposal).where(Proposal.fingerprint == "duplicate:orders:orders_segment")
        ).one()
        assert made.status == "open"  # a draft: nothing applied
        made.status = "declined"  # the owner kept both
        s.commit()
    card = _card(env.client.get(h + "/summary").text)
    assert "Orders by segment" not in card
    r = _post(env, h, "/summary/merge", {"keep": "orders", "drop": "net_revenue"})
    assert r.status_code == 400 and r.headers.get("X-Othor-Toast") == "1"
