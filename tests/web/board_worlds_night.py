# SPDX-License-Identifier: Apache-2.0
"""Board-state builders for variants of the `night` world (M12b). See tests/web/board_worlds.py.

Each builder reuses the code path of the test that reaches its state (named in its docstring),
through the app's routes and services. The `first_days` builder starts from the `setup` world."""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import uuid
from typing import Any

import pytest
import sqlalchemy as sa

from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from othor.engine import orchestrator
from othor.engine.context import EngineContext
from tests.fixtures.sample_company.history import STEADY_RUN
from tests.web.board_worlds import World, prep

NEXT_MORNING = dt.datetime(2026, 11, 2, 3, 0, tzinfo=dt.UTC)  # 8:30 AM IST on Mon 2 Nov
SIX = dt.datetime(2026, 11, 1, 0, 30, tzinfo=dt.UTC)  # 6:00 AM IST on Sun 1 Nov


# --- helpers -------------------------------------------------------------------------------------


def _ctx(w: World) -> EngineContext:
    svc = w.svc
    return EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: w.env.clock.t)


def _run(w: World, run_date: dt.date, kind: str = "nightly") -> Any:
    """Start and execute one run (as tests/web/test_home_nights.py `_next_night`)."""
    ctx = _ctx(w)
    with w.svc.sf() as s, tenant_scope(w.cid):
        c = s.scalars(sa.select(Company).where(Company.id == w.cid)).one()
        r = orchestrator.start(
            s, c, kind=kind, run_date=run_date, now=ctx.clock(), image_version="0.1.0"
        )
        s.commit()
        rid = r.id
    with tenant_scope(w.cid):
        assert orchestrator.execute(ctx, rid) == "done"
    return rid


def _next_night(w: World) -> Any:
    """The run of Mon 2 Nov on top of the steady night, read at 8:30 AM IST that morning."""
    w.env.clock.t = NEXT_MORNING
    rid = _run(w, STEADY_RUN + dt.timedelta(days=1))
    w.rid = rid
    return rid


def _jobs(w: World) -> None:
    from othor.jobs.runner import WorkerContext, run_pending

    svc = w.svc
    run_pending(WorkerContext(svc.settings, svc.sf, svc.key, clock=svc.clock))


def _draft_of(page: str) -> dict[str, Any]:
    m = re.search(r'name="draft" value="([^"]*)"', page)
    assert m, "no draft on the page"
    return json.loads(html.unescape(m.group(1)))


def _new_brief(w: World, question: str, **change: Any) -> str:
    """A brief made through New brief (4c → 4c1 → Create; tests/web/test_brief_web.py): the
    question drafts (the model is off: the default numbers with the question), the draft is
    changed as the owner would in the chips, then Create. Returns the new brief's id."""
    r = w.post("/brief/new", {"question": question}, page="/brief")
    assert r.status_code == 200, r.status_code
    draft = {**_draft_of(r.text), **change}
    r = w.post("/brief/new/create", {"draft": json.dumps(draft)}, page="/brief")
    assert r.status_code == 303 and "created=" in r.headers["location"], r.headers
    return r.headers["location"].split("created=")[1]


def _add_recipient(w: World, bid: str, name: str, email: str) -> None:
    r = w.post(f"/brief/{bid}/recipients", {"r_name": name, "r_email": email}, page="/brief")
    assert r.status_code == 303


def _leadership(w: World) -> str:
    from othor.briefs.models import Brief

    with w.svc.sf() as s, tenant_scope(w.cid):
        return str(s.scalars(sa.select(Brief.id).where(Brief.is_default.is_(True))).one())


def _sources(w: World) -> dict[str, Any]:
    from othor.connectors.models import Source

    with w.svc.sf() as s, tenant_scope(w.cid):
        return {x.name: x.id for x in s.scalars(sa.select(Source))}


# --- Home ----------------------------------------------------------------------------------------


@prep
def all_caught_up(w: World) -> None:
    """3b: nothing left to review; the goal accepted (test_home_web `all_caught_up`)."""
    from othor.engine.models import Proposal
    from othor.ontology.models import Question

    with w.svc.sf() as s, tenant_scope(w.cid):
        goal = s.scalars(sa.select(Proposal).where(Proposal.kind == "goal")).one()
        gid = str(goal.id)
        for p in s.scalars(sa.select(Proposal).where(Proposal.id != goal.id)):
            p.status = "declined"
        for q in s.scalars(sa.select(Question)):
            q.status = "skipped"
            q.skipped_until = dt.date(2026, 12, 31)
        s.commit()
    assert w.post(f"/home/proposals/{gid}/accept", page="/home").status_code == 303


# --- Morning Brief -------------------------------------------------------------------------------

SUGGESTED = (
    (
        "Export receivables",
        "Which export customers are paying late?",
        ["receivables_over_60", "dso", "export_share"],
        "Finance",
        ["Board deck", "Sweep", "Metrics"],
        ["Export invoices are a large share of revenue", "DSO has a target in your MIS"],
    ),
    (
        "Plant operations",
        "How do Surat and Coimbatore compare on margin per metre and output?",
        ["gross_margin_per_metre", "metres_dispatched", "dispatch_time"],
        "Plants",
        ["MIS", "Ontology"],
        ["Two plants with separate targets in your MIS", "The Surat shutdown is a known pattern"],
    ),
    (
        "Key accounts",
        "Are the customers that hold most of the order book still buying?",
        ["order_book", "orders_value", "active_customers"],
        "Sales",
        ["Sweep", "your message"],
        ["Named as main customers in your business message"],
    ),
)


@prep
def several_briefs(w: World) -> None:
    """4b, 4c1: Leadership, Sales and Finance, each with an issue from the next night's run,
    and three suggested briefs (test_brief_web: new brief, suggested briefs)."""
    from othor.briefs.models import SuggestedBrief

    sales = _new_brief(
        w,
        "Where is revenue moving by region and segment, and what is in the order book?",
        name="Sales",
        headline=["net_revenue", "order_book", "orders_value"],
        scope={"metrics": ["net_revenue", "order_book", "orders_value"], "exclude": {}},
    )
    finance = _new_brief(
        w,
        "Are receivables and cash under control?",
        name="Finance",
        headline=["receivables_share_over_60", "dso", "cash_balance"],
        scope={"metrics": ["receivables_share_over_60", "dso", "cash_balance"], "exclude": {}},
    )
    _add_recipient(w, sales, "Priya Menon", "priya@indigothread.example")
    _add_recipient(w, finance, "Leela Tandon", "leela@indigothread.example")
    with w.svc.sf() as s, tenant_scope(w.cid):
        for name, question, head, area, frm, why in SUGGESTED:
            s.add(
                SuggestedBrief(
                    question=question,
                    name=name,
                    draft={"headline": head, "area": area, "from": frm},
                    evidence=why,
                )
            )
        s.commit()
    _next_night(w)


@prep
def brief_created(w: World) -> dict[str, str]:
    """4c2: several briefs, then Export receivables created from its suggestion (toast)."""
    from othor.briefs.models import SuggestedBrief

    several_briefs(w)
    with w.svc.sf() as s, tenant_scope(w.cid):
        sug = s.scalars(
            sa.select(SuggestedBrief).where(SuggestedBrief.name == "Export receivables")
        ).one()
        sid, question = str(sug.id), sug.question
    r = w.post("/brief/new", {"question": question, "suggestion": sid}, page="/brief")
    draft = _draft_of(r.text)
    r = w.post("/brief/new/create", {"draft": json.dumps(draft), "suggestion": sid}, page="/brief")
    assert r.status_code == 303
    return {"new": r.headers["location"].split("created=")[1]}


@prep
def quiet_night(w: World) -> None:
    """5f: the next night finds nothing new (test_sweep_web `the_quiet_night`)."""
    _next_night(w)


@prep
def quiet_day(w: World) -> dict[str, str]:
    """4d0: a Cash brief (DSO, cash and collections) on the next night: its numbers carry the
    morning, nothing in its scope needs the owner."""
    head = ["dso", "cash_balance", "collections"]
    bid = _new_brief(
        w,
        "Is cash coming in on time?",
        name="Cash",
        headline=head,
        scope={"metrics": head, "exclude": {}},
    )
    _next_night(w)
    return {"quiet": bid}


@prep
def failed_night(w: World) -> dict[str, str]:
    """4e1: the next night's run stops at `derive` (test_rerun `_fail_night`)."""
    from othor.engine.models import Run

    ctx = _ctx(w)
    real = orchestrator._step

    def broken(c, info, spec, name, status):  # type: ignore[no-untyped-def]
        if name == "derive":
            raise RuntimeError("derive stopped")
        return real(c, info, spec, name, status)

    w.env.clock.t = dt.datetime(2026, 11, 1, 21, 0, tzinfo=dt.UTC)  # 2:30 AM IST, Mon 2 Nov
    with w.svc.sf() as s, tenant_scope(w.cid):
        c = s.scalars(sa.select(Company).where(Company.id == w.cid)).one()
        r = orchestrator.start(
            s,
            c,
            kind="nightly",
            run_date=STEADY_RUN + dt.timedelta(days=1),
            now=ctx.clock(),
            image_version="0.1.0",
        )
        s.commit()
        rid = r.id
    orchestrator._step = broken  # type: ignore[assignment]
    try:
        with tenant_scope(w.cid), pytest.raises(RuntimeError):
            orchestrator.execute(ctx, rid)
    finally:
        orchestrator._step = real  # type: ignore[assignment]
    w.env.clock.t = NEXT_MORNING
    with w.svc.sf() as s, tenant_scope(w.cid):
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        assert run.status == "failed"
        return {"failed": str(run.number)}


# --- Recipients' links (10d–10f) ------------------------------------------------------------------


def _delivered(w: World) -> tuple[str, str]:
    """The Leadership brief of 1 Nov sent at 06:00 to the owner and Priya (test_links_web
    `sent`, on the real night); the recipient has no session. Returns Priya's open and
    unsubscribe paths."""
    from othor.mail import delivery

    bid = _leadership(w)
    _add_recipient(w, bid, "Priya Menon", "priya@indigothread.example")
    w.env.clock.t = SIX
    with w.svc.sf() as s, tenant_scope(w.cid):
        out = delivery.deliver_brief(
            s,
            w.svc.settings,
            w.svc.key,
            w.svc.relay,
            s.get(Company, w.cid),
            uuid.UUID(bid),
            STEADY_RUN,
            SIX,
        )
    assert out.sent == 2, out
    m = next(m for m in reversed(w.env.relay.outbox) if m.to == "priya@indigothread.example")
    o = re.search(r"https://indigothread\.[\w.]+(/l/[\w-]+)", m.text).group(1)  # type: ignore[union-attr]
    u = re.search(r"https://indigothread\.[\w.]+(/u/[\w-]+)", m.text).group(1)  # type: ignore[union-attr]
    return o, u


@prep
def link_opened(w: World) -> dict[str, str]:
    """10d: Priya opens the brief from her email's signed link, signed out."""
    o, _ = _delivered(w)
    w.env.clock.t = SIX + dt.timedelta(hours=2)
    w.env.client.cookies.clear()
    return {"link": o}


@prep
def link_expired(w: World) -> dict[str, str]:
    """10e: the same link eight days later (links work for 7 days)."""
    o, _ = _delivered(w)
    w.env.clock.t = SIX + dt.timedelta(days=8)
    w.env.client.cookies.clear()
    return {"link": o}


@prep
def link_unsubscribe(w: World) -> dict[str, str]:
    """10f: Priya's one-click unsubscribe from the email; she also gets the Sales brief."""
    sales = _new_brief(
        w,
        "Where is revenue moving by region and segment?",
        name="Sales",
        headline=["net_revenue", "order_book"],
    )
    _add_recipient(w, sales, "Priya Menon", "priya@indigothread.example")
    _, u = _delivered(w)
    w.env.clock.t = SIX + dt.timedelta(hours=2)
    w.env.client.cookies.clear()
    return {"link": u}


# --- the first days (from the `setup` world) -----------------------------------------------------


@prep
def first_days(w: World) -> dict[str, str]:
    """10i, 10j: the first nightly run (Mon 2 Nov) with no history before it
    (test_sweep_web `first_days`)."""
    from tests.integration.run_world import confirm_sample

    w.env.clock.t = NEXT_MORNING
    confirm_sample(_ctx(w), w.cid, w.sample)
    w.rid = _run(w, dt.date(2026, 11, 2))
    return {"bid": _leadership(w)}


# --- a disconnected source (10k–10m) -------------------------------------------------------------


@prep
def sheets_revoked(w: World) -> dict[str, str]:
    """10k, 10l, 10m: Google access to MIS-Weekly is revoked before the next night; the run
    goes on, its numbers stay at their last good day (test_run_sheets `revoked_token`)."""
    auth = w.svc.settings.sample_dir / "sheets" / "auth.json"
    auth.write_text(
        json.dumps({**json.loads(auth.read_text()), "revoked_at": "2026-11-01T20:30:00"})
    )
    w.env.clock.t = NEXT_MORNING
    rid = _run_partial(w)
    w.rid = rid
    return {"mis": str(_sources(w)["MIS-Weekly"])}


def _run_partial(w: World) -> Any:
    ctx = _ctx(w)
    with w.svc.sf() as s, tenant_scope(w.cid):
        c = s.scalars(sa.select(Company).where(Company.id == w.cid)).one()
        r = orchestrator.start(
            s,
            c,
            kind="nightly",
            run_date=STEADY_RUN + dt.timedelta(days=1),
            now=ctx.clock(),
            image_version="0.1.0",
        )
        s.commit()
        rid = r.id
    with tenant_scope(w.cid):
        assert orchestrator.execute(ctx, rid) in ("done", "partial")
    return rid


# --- Sweep ---------------------------------------------------------------------------------------


@prep
def not_determinable(w: World) -> dict[str, str]:
    """10p: a finding of tonight's run whose reason can't be told from the data."""
    from othor.engine.models import Finding

    with w.svc.sf() as s, tenant_scope(w.cid):
        rows = s.scalars(sa.select(Finding).where(Finding.run_id == w.rid)).all()
        nd = sorted(
            (r for r in rows if r.state == "not_determinable"),
            key=lambda r: (r.breakdown_value is not None, r.fingerprint),
        )
        assert nd, "no not-determinable finding tonight"
        return {"fp_nd": nd[0].fingerprint}


# --- Sources: connect the Order tracker after setup (6eb, 6f–6i) ---------------------------------


def _signed_in_google(w: World) -> str:
    r = w.post("/sources/google/start", page="/sources")
    assert r.status_code == 303, r.status_code
    return r.headers["location"].split("s=")[1]


@prep
def tracker_tabs_listed(w: World) -> dict[str, str]:
    """6eb, 6f: signed in with Google, the Order tracker chosen, its tabs read and listed
    (test_chat_web `after_setup_review_confirm_and_fix_chat`)."""
    sid = _signed_in_google(w)
    w.post(f"/sources/{sid}/files", {"file": "sample-order-tracker-fy27"}, page="/sources")
    _jobs(w)
    return {"new": sid}


@prep
def tracker_scanning(w: World) -> dict[str, str]:
    """6g: the Order tracker's scan half-way: its tabs read, then put back as the scan was
    part-way through (the last two tabs reading and queued; as board_worlds_setup `_mid_scan`)."""
    from othor.connectors.models import Scan, SourceTable

    sid = tracker_tabs_listed(w)["new"]
    src = uuid.UUID(sid)
    with w.svc.sf() as s, tenant_scope(w.cid):
        tabs = s.scalars(
            sa.select(SourceTable).where(SourceTable.source_id == src).order_by(SourceTable.name)
        ).all()
        left = [x for x in tabs if x.use != "excluded"][-2:]
        for i, x in enumerate(left):
            x.state = "reading" if i == 0 else "queued"
        sc = s.scalars(
            sa.select(Scan).where(Scan.source_id == src).order_by(Scan.created_at.desc())
        ).first()
        assert sc is not None
        sc.status, sc.finished_at = "running", None
        prog = dict(sc.progress or {})
        prog["scanned"] = int(prog.get("total", len(tabs))) - len(left)
        prog["eta_s"] = 60
        sc.progress = prog
        s.commit()
    return {"new": sid}


@prep
def tracker_review(w: World) -> dict[str, str]:
    """6h, 6ha: Open orders and Deals checked and confirmed; the review lists what the
    Order tracker adds, each with today's value."""
    from othor.connectors.models import SheetTab

    sid = tracker_tabs_listed(w)["new"]
    with w.svc.sf() as s, tenant_scope(w.cid):
        tabs = {
            t.tab_name: str(t.id)
            for t in s.scalars(sa.select(SheetTab).where(SheetTab.source_id == uuid.UUID(sid)))
        }
    r = w.post(
        f"/sources/{sid}/tabs", {"tab": [tabs["Open orders"], tabs["Deals"]]}, page="/sources"
    )
    loc = r.headers["location"]
    while "/check/" in loc:
        r = w.post(loc + "/confirm", page="/sources")
        loc = r.headers["location"]
    _jobs(w)
    page = w.env.client.get(f"{w.host}/sources?panel=review&s={sid}").text
    keys = re.findall(r'name="key" value="([a-z0-9_]+)"', page)
    fix = re.findall(r"panel=chat&amp;s=[0-9a-f-]+&amp;key=([a-z0-9_]+)&amp;purpose=fix", page)
    return {"new": sid, "fix": (fix or keys or [""])[-1]}


@prep
def tracker_confirmed(w: World) -> dict[str, str]:
    """6i: the review's metrics confirmed (one Ontology version); first read tonight."""
    sid = tracker_review(w)["new"]
    page = w.env.client.get(f"{w.host}/sources?panel=review&s={sid}").text
    keys = re.findall(r'name="key" value="([a-z0-9_]+)"[^>]*checked', page)
    r = w.post(f"/sources/{sid}/confirm-metrics", {"key": keys}, page="/sources")
    assert r.status_code == 303
    return {"new": sid}
