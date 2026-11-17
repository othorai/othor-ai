# SPDX-License-Identifier: Apache-2.0
"""Home (3a, 3aa, 3b, 10k; M10a) on the steady-state night of Sun 1 Nov 2026.

The header and cards read the last run; the Sweep card's counts are Sweep's (one overlay);
proposals have Accept, Decline and Undo; questions are answered one at a time with Undo; a source
the run couldn't read and a recipient who left show as banners first; with nothing to review the
page is "All caught up". Every action writes an event; another company's id is a 404.
Needs `make dev && make seed`.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid

import sqlalchemy as sa

from othor.briefs.models import Recipient
from othor.core.events import record_event
from othor.core.models import Event
from othor.core.tenancy import tenant_scope
from othor.engine.models import Proposal, Run, SeriesMeta
from othor.ontology import service
from othor.ontology.models import Question
from tests.web.conftest import Env
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def _post(env: Env, h: str, path: str, data: dict | None = None):
    tok = env.csrf(h + "/home")
    return env.client.post(h + path, data={"csrf": tok, **(data or {})}, follow_redirects=False)


def _events(svc, cid, action):
    with svc.sf() as s, tenant_scope(cid):
        return s.scalars(sa.select(Event).where(Event.action == action)).all()


def test_the_page_reads_the_last_run_and_counts_agree_with_sweep(night):  # noqa: F811
    env, h, cid, rid, svc = night
    page = env.client.get(h + "/home").text
    with svc.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        sw = run.summary["sweep"]
        version = service.current_number(s)
        open_q = s.scalar(
            sa.select(sa.func.count()).select_from(Question).where(Question.status == "open")
        )
    # design B2 (11 Nov 2026): the header line (Ontology version, run number) is gone; the
    # Sweep box's counts are Sweep's own. The weather's "How this was picked" names its run (v0.12).
    head = re.sub(r'<section class="weather .*?</section>', "", page, flags=re.S)
    assert f"Business Model v{version}" not in head and f"run #{run.number:04d}" not in head
    assert f"Questions for you · {open_q}" in page
    assert "Leadership brief" in page and "Open brief →" in page
    box = page.split('aria-label="Sweep"', 1)[1]
    assert f"{sw['findings']} found" in box and "Open Sweep →" in box
    # P10's question (needs confirmation) comes first
    assert "Which system is right about these 37 orders?" in page
    assert (
        "Answer questions" in page and "One at a time · answers go into the Business Model" in page
    )


def test_a_goal_proposal_is_accepted_and_undone(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        p = s.scalars(sa.select(Proposal).where(Proposal.kind == "goal")).one()
        pid, n = str(p.id), service.current_number(s)
    page = env.client.get(h + "/home").text
    # owner, 14 Nov 2026 (M16.1): proposals also come from the Ontology
    assert ">Proposals for the Business Model<" in page
    assert "Net revenue FY27 target ₹60 Cr → tracking ₹57.2 Cr" in page
    assert "Morning Brief · forecast" in page
    r = _post(env, h, f"/home/proposals/{pid}/accept")
    assert r.status_code == 303 and r.headers["location"] == f"/home?done={pid}"
    page = env.client.get(h + r.headers["location"]).text
    assert (
        "Accepted · applies from tonight’s run" in page or "Accepted · applies from tonight" in page
    )
    with svc.sf() as s, tenant_scope(cid):
        assert service.current_number(s) == n + 1
        goal = next(x for x in service.active(s, ["goal"]) if x.key == "fy27_net_revenue")
        assert goal.body["at_risk_since"] == "2026-11-01"
    r = _post(env, h, f"/home/proposals/{pid}/undo")
    with svc.sf() as s, tenant_scope(cid):
        p = s.scalars(sa.select(Proposal).where(Proposal.id == uuid.UUID(pid))).one()
        assert p.status == "open" and service.current_number(s) == n + 2
    assert _events(svc, cid, "proposal.accepted") and _events(svc, cid, "proposal.undone")
    r = _post(env, h, f"/home/proposals/{pid}/decline")
    page = env.client.get(h + r.headers["location"]).text
    assert "Declined · it won’t be proposed again" in page


def test_a_stale_source_and_an_unsubscribe_show_first(night):  # noqa: F811
    env, h, cid, rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        run.status = "partial"
        run.summary = {**(run.summary or {}), "stale_sources": ["erp_db"]}
        m = s.scalars(sa.select(SeriesMeta).where(SeriesMeta.metric == "net_revenue")).first()
        m.stale, m.last_run_id, m.last_good_at = True, rid, dt.date(2026, 10, 30)
        rcp = s.scalars(sa.select(Recipient)).first()
        rcp.status = "unsubscribed"
        record_event(
            s,
            actor="recipient",
            action="recipient.unsubscribed",
            object_type="recipient",
            object_id=rcp.id,
        )
        s.commit()
    page = env.client.get(h + "/home").text
    assert "erp_db wasn’t read last night" in page and "Reconnect →" in page
    assert "Net revenue" in page and "from Fri 30 Oct" in page
    assert page.index("wasn’t read last night") < page.index("<h1>Indigo Thread Co.</h1>")
    assert "unsubscribed from" in page and "Delivery →" in page
    (ev,) = _events(svc, cid, "recipient.unsubscribed")
    _post(env, h, f"/home/notices/{ev.id}/dismiss")
    assert "unsubscribed from" not in env.client.get(h + "/home").text


def test_all_caught_up_lists_what_was_accepted(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        goal = s.scalars(sa.select(Proposal).where(Proposal.kind == "goal")).one()
        gid = str(goal.id)
        for p in s.scalars(sa.select(Proposal).where(Proposal.id != goal.id)):
            p.status = "declined"
        for q in s.scalars(sa.select(Question)):
            q.status = "skipped"
            q.skipped_until = dt.date(2026, 12, 31)
        s.commit()
    _post(env, h, f"/home/proposals/{gid}/accept")
    page = env.client.get(h + "/home").text
    assert "All caught up" in page
    assert "Recently accepted" in page and "Goal" in page


def test_another_companys_ids_are_404(night):  # noqa: F811
    env, h, _cid, _rid, _svc = night
    fake = str(uuid.uuid4())
    assert _post(env, h, f"/home/proposals/{fake}/accept").status_code == 404
    assert _post(env, h, f"/home/questions/{fake}/reply", {"choice": "x"}).status_code == 404
    assert env.client.get(h + f"/home?panel=proposal&p={fake}").status_code == 404


def test_the_checks_strip_leads_home(night):  # noqa: F811
    """Owner's design, 11 Nov 2026: the strip (how many checks ran last night, and why the rest
    didn't) is the first block on Home, above the cards, from the same run as Sweep."""
    env, h, *_ = night
    page = env.client.get(h + "/home").text
    assert 'aria-label="Checks last night"' in page
    assert page.index('aria-label="Checks last night"') < page.index('class="brief-cards home"')
    assert re.search(r"At \d+% Intelligence\.", page)
    # the (i) opens what it means and the checks by group, in place (v0.11, owner 12 Nov)
    assert 'x-data="popover"' in page and "% Intelligence means " in page
    assert "Waiting on you" in page and "data-pop-close" in page


def test_the_top_bar_and_the_title_row(night):  # noqa: F811
    """M19.5 (owner, 15 Nov 2026): "Updated …" moved to the top bar, left of a ⋯ menu that holds
    History and Run again now; Big Picture Report sits in the title row where "Updated" was (#40
    put it there first). The title is the company's name (#61)."""
    env, h, *_ = night
    page = env.client.get(h + "/home").text
    row = page[page.index('<div class="page-title">') :][:1200]
    assert "<h1>Indigo Thread Co.</h1>" in row and 'class="btn ai"' in row, row
    # System Health left of Big Picture (owner, 16 Nov 2026)
    assert row.index('class="btn health') < row.index('class="btn ai"'), row
    assert "Updated " not in row
    bar = page[page.index('class="hint">Updated ') :][:1200]
    assert 'class="use top-more"' in bar and ">History</a>" in bar and "Run again now" in bar
    assert "<h1>Home</h1>" not in page
