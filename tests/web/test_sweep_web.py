# SPDX-License-Identifier: Apache-2.0
"""Sweep (5a–5f, 10i) and the finding panel (5b, 5c, 4f–4f3, 10p) on the steady-state fixture:
the run of Sun 1 Nov 2026 on top of earlier nightly snapshots (a fixture run on 31 Oct).

Counts agree with the run's summary (one run_id); cards sorted by money; the panel's five tabs;
Watch with its 90-day replay; snooze → Suppressed at once (the overlay) → Undo, with events;
another company's fingerprint is a 404. Needs `make dev && make seed`.
"""

from __future__ import annotations

import copy
import datetime as dt
import html
import re

import pytest
import sqlalchemy as sa

from othor.connectors.models import RunQuery
from othor.core.models import Company, Event
from othor.core.tenancy import system_scope, tenant_scope
from othor.engine import orchestrator, sweep_text
from othor.engine.context import EngineContext
from othor.engine.models import Finding, Mute, Run, Snooze, Watch
from othor.engine.store import RunFiles, read_json
from othor.web import sweep_view as sv
from tests.fixtures.sample_company.history import STEADY_RUN, prior_snapshots
from tests.fixtures.sample_company.load import write_sheets
from tests.integration.run_world import confirm_sample
from tests.web.conftest import Env, make_env, signup, snapshot
from tests.web.test_chat_web import need_sources, sample  # noqa: F401


def build_night(env: Env, sample) -> tuple[object, object]:  # noqa: F811
    """Sign up, confirm the sample company and run 31 Oct (fixture) and 1 Nov (nightly)."""
    env.clock.t = dt.datetime(2026, 11, 1, 3, 0, tzinfo=dt.UTC)  # 8:30 AM IST on Sun 1 Nov
    signup(env)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, system_scope("test"):
        cid = s.scalars(sa.select(Company.id).where(Company.slug == "indigothread")).one()
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    write_sheets(sample, ctx.settings.sample_dir / "sheets")
    confirm_sample(ctx, cid, sample, with_mis=True, watches=True, surat_answered=True)
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        f = orchestrator.start_fixture(
            ctx,
            s,
            c,
            run_date=STEADY_RUN - dt.timedelta(days=1),
            points={"erp_db": prior_snapshots(sample, STEADY_RUN)},
            image_version="0.1.0",
        )
        s.commit()
        fid = f.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, fid) == "done"
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        r = orchestrator.start(
            s, c, kind="nightly", run_date=STEADY_RUN, now=ctx.clock(), image_version="0.1.0"
        )
        s.commit()
        rid = r.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid) == "done"
    return cid, rid


_NIGHT: dict[str, object] = {}  # built once per test process, copied for each test (v0.11)


def _det_card(page: str, key: str) -> str:
    """One detector's card on the strip (M15.7b)."""
    m = re.search(
        rf'<div class="det-card[^"]*" role="tooltip" id="det-{key}">(.*?)</div></li>', page, re.S
    )
    assert m, key
    return m.group(1)


@pytest.fixture
def night(sample, need_sources, tmp_path, tmp_path_factory):  # noqa: F811
    """The 1 Nov night, built once per process (about a minute) and copied into each test's own
    folder, so every test starts from the same bytes and changes nothing for the next."""
    if "root" not in _NIGHT:
        root = tmp_path_factory.mktemp("night-template")
        with make_env(root) as e:
            cid, rid = build_night(e, sample)
            cookies = [copy.copy(c) for c in e.client.cookies.jar]
            at = e.clock.t
        _NIGHT.update(root=root, cid=cid, rid=rid, cookies=cookies, at=at)
    snapshot(_NIGHT["root"], tmp_path)  # type: ignore[arg-type]
    with make_env(tmp_path, at=_NIGHT["at"]) as env:  # type: ignore[arg-type]
        for c in _NIGHT["cookies"]:  # type: ignore[attr-defined]
            env.client.cookies.jar.set_cookie(copy.copy(c))
        svc = env.app.state.services  # type: ignore[attr-defined]
        yield env, env.host("indigothread"), _NIGHT["cid"], _NIGHT["rid"], svc


def _post(env: Env, h: str, path: str, data: dict | None = None):
    tok = env.csrf(h + "/sweep")
    return env.client.post(h + path, data={"csrf": tok, **(data or {})}, follow_redirects=False)


def _fp(svc, cid, rid, detector, value=None):
    with svc.sf() as s, tenant_scope(cid):
        rows = s.scalars(sa.select(Finding).where(Finding.run_id == rid)).all()
        return next(
            r.fingerprint for r in rows if r.detector == detector and r.breakdown_value == value
        )


def test_the_overview_reads_one_run(night):
    """Sweep, simplified (M15.2): the strip, three numbers, the tabs; every open finding in the
    brief ranking's order, then the detectors table. Every count is the run's."""
    from othor.briefs.rank import score

    env, h, cid, rid, svc = night
    page = env.client.get(h + "/sweep").text
    with svc.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        sw = run.summary["sweep"]
        rv = sv.load(s, svc.settings, run, run.run_date)
        assert rv is not None
        cov = [c for c in rv.doc["coverage"] if c["detector"] in sv.shipped()]
        rows = sv.detector_rows(rv, s)
    assert f"Findings · {sw['findings']}" in page and f"Watching · {sw['watching']}" in page
    assert f"Suppressed · {sw['suppressed']}" in page and f"Resolved · {sw['resolved']}" in page
    # the strip: activated + inactive = every detector; suppressed is the tab's count
    on = sum(1 for c in cov if c["status"] != "could_not_run")
    off = len(cov) - on
    assert (
        f"Total {len(cov)} detectors. {on} activated. "
        f'<span class="off">{off} inactive.</span> '
        f'<span class="held">{sw["suppressed"]} suppressed.</span>'
    ) in page
    # M15.7b: one square bar per detector, its short name under it, and a card behind each
    bars = re.findall(r'<li class="det (on|partly|off)(?: end)?"', page)
    assert bars == [r["kind"] for r in rows] and len(bars) == len(cov)
    names = re.findall(r'<span class="det-name">([^<]+)</span>', page)
    assert names == [sweep_text.SHORT[r["key"]] for r in rows]
    for r in rows:
        card = _det_card(page, r["key"])
        assert f'aria-controls="det-{r["key"]}"' in page
        assert f'aria-label="{r["label"]}: {r["status"]}"' in page
        assert html.escape(sweep_text.CATCHES[r["key"]], quote=False) in card
        assert r["status"] in card
        if r["kind"] == "on":
            assert f"Today: {html.escape(r['result'], quote=False)}" in card
    # the three numbers
    assert "Metrics with enough history" in page and f"{sw['tests']}" in page
    assert "Findings today" in page and f"{sw['new']} new</span>" in page
    assert f"{sw['findings']} open in total" in page
    # every open finding, one column, in the brief ranking's order (money, then urgency)
    assert sw["findings"] == 8 and page.count('<article class="finding-row"') == 8
    titles = re.findall(r'class="finding-title" href="[^"]+">([^<]+)<', page)
    live = [f for f in rv.shown if f["status"] == "open"]
    live.sort(key=lambda f: (-score(f, rv.compiled, run.run_date), f["fingerprint"]))
    assert titles == [html.escape(f["text"]["title"], quote=False) for f in live]
    assert page.count("In Leadership brief") == 4  # the brief's items say where they are
    assert page.count("More info →") == 8
    # the detectors table, in plain words; nothing of the old layout
    assert page.count("<tr class=") == len(cov) and 'id="detectors"' in page
    for gone in ("Also in today’s briefs", "Checked, nothing found", "Coverage</p>"):
        assert gone not in page, gone
    # the last seven runs, and another run by its number
    assert "Last " in page and "/sweep?run=" in page
    assert env.client.get(h + "/sweep?run=9999").status_code == 404


def test_a_check_waiting_on_the_owner_is_answered_in_place(night):
    """ "Answer 1 question →" opens the Othor AI chat on the questions over Sweep; its actions
    return to Sweep (owner, 13 Nov 2026; one chat from 0.26)."""
    env, h, cid, _rid, svc = night
    with svc.sf() as s, tenant_scope(cid):
        asked = sv.open_needs(s)
        number = sv.detecting_runs(s)[0].number
    assert asked, "the sample night asks for a pair (Lead/lag could not run)"
    q = next(iter(asked.values()))
    page = env.client.get(h + "/sweep").text
    link = f"/sweep?run={number}&amp;panel=othor&amp;subject=questions"
    assert f'href="{link}">Answer 1 question →</a>' in page
    # M15.7b: the same link in the Lag card on the strip, with what it needs
    card = _det_card(page, "lead_lag")
    assert f'href="{link}">Answer 1 question →</a>' in card
    assert "Needs two numbers that move together" in card
    # 0.26: the one Othor AI chat opens over Sweep, and its actions come back to Sweep
    panel = env.client.get(h + link.replace("&amp;", "&")).text
    assert "Questions for you" in panel and q.text in panel
    assert f'name="back" value="/sweep?run={number}&amp;panel=othor&amp;subject=questions"' in panel
    from othor.web import home_view as hv

    for bad in ("https://evil.example", "/admin", "sweep-1/../x", "sweep-"):
        assert hv.panel_page(bad) == "/home", bad
    assert hv.panel_url_id("x", None) == "/home?panel=othor&subject=questions"


def test_the_strips_info_says_what_answering_would_turn_on(night):
    env, h, _cid, _rid, _svc = night
    page = env.client.get(h + "/sweep").text
    assert "Each detector is one way Sweep tests your numbers every night." in page
    assert "Answer 1 question to turn on up to 1 more." in page


def test_the_panel_has_five_tabs_from_the_run(night):
    env, h, cid, rid, svc = night
    p10 = _fp(svc, cid, rid, "cross_source")
    base = f"{h}/sweep/finding/{p10}"
    page = env.client.get(base).text
    assert "Needs confirmation" in page and "Check in the source system before acting" in page
    assert "3 possible reasons · the data can’t tell which" in page
    assert "Orders · top 5 of 37" in page and "Which system is right about these 37 orders?" in page
    test = env.client.get(base + "?tab=test").text
    # M15.3: what it tested, the gates with their values, method and settings
    assert "What it tested" in test and "gates, all passed" in test and "It isn’t chance" in test
    assert "Method and settings" in test and "Materiality floor" in test
    metric = env.client.get(base + "?tab=metric").text
    assert "Rules from your Business Model" in metric and "Version" in metric
    sql = env.client.get(base + "?tab=sql").text
    assert "Generated from the metric spec, not written by AI" in sql
    run = env.client.get(base + "?tab=run").text
    assert "Sources read" in run and "Steps" in run and "Replay this run" in run
    # the SQL tab shows a statement the run sent, as run_query holds it
    p1 = _fp(svc, cid, rid, "net_hiding_gross")
    sql = env.client.get(f"{h}/sweep/finding/{p1}?tab=sql").text
    with svc.sf() as s, tenant_scope(cid):
        sent = {q.sql for q in s.scalars(sa.select(RunQuery).where(RunQuery.run_id == rid))}
    shown = re.findall(r'<pre class="code-block"[^>]*>(.*?)</pre>', sql, re.S)
    import html

    # each statement opens with its "-- metric · key · spec vN" comment (4f2, M12b); the SQL after
    # it is exactly what ran
    shown = [html.unescape(x).split("\n", 1)[-1] for x in shown]
    assert shown and any(
        x in sent for x in shown
    )  # the statements sent appear exactly as run_query holds them
    assert "analytics store, from the run’s stored series" in sql  # the derived step
    csv = env.client.get(f"{h}/sweep/finding/{p1}/result.csv")
    assert csv.status_code == 200 and csv.text.startswith("period,breakdown_value,value")
    # replay on demand writes only an event
    r = _post(env, h, f"/sweep/run/{_number(svc, cid, rid)}/replay?fp={p1}")
    assert r.status_code == 303
    with svc.sf() as s, tenant_scope(cid):
        (ev,) = s.scalars(sa.select(Event).where(Event.action == "run.replayed")).all()
        assert ev.after["ok"] is True
    assert "replay identical" in env.client.get(f"{h}/sweep/finding/{p1}?tab=run").text
    # Not determinable (10p): what is measured, the reasons with the GSTIN check, settle it
    p4 = _fp(svc, cid, rid, "missing_records", "Ravira Exports")
    nd = env.client.get(f"{h}/sweep/finding/{p4}").text
    assert "Not determinable" in nd and "What is measured" in nd and "Ruled out" in nd
    assert "To settle it" in nd and "Add a known pause" in nd


def _number(svc, cid, rid) -> int:
    with svc.sf() as s, tenant_scope(cid):
        return s.scalars(sa.select(Run.number).where(Run.id == rid)).one()


def test_snooze_moves_a_finding_to_suppressed_at_once_and_undo_brings_it_back(night):
    env, h, cid, rid, svc = night
    fp = _fp(svc, cid, rid, "missing_records")
    r = _post(env, h, f"/sweep/finding/{fp}/snooze")
    assert r.status_code == 303 and "tab=suppressed" in r.headers["location"]
    page = env.client.get(h + "/sweep?tab=suppressed").text
    assert "Findings · 7" in page and "Snoozed until 8 Nov" in page
    with svc.sf() as s, tenant_scope(cid):
        assert s.scalars(sa.select(Snooze)).one().fingerprint == fp
        assert s.scalars(sa.select(Event).where(Event.action == "finding.snoozed")).one()
        # the stored run is never rewritten
        assert (
            s.scalars(sa.select(Finding).where(Finding.fingerprint == fp, Finding.run_id == rid))
            .one()
            .status
            == "open"
        )
    files = RunFiles(svc.settings.runs_dir(cid), rid)
    assert read_json(files.outputs_json)["sweep"]["counts"]["findings"] == 8
    _post(env, h, f"/sweep/finding/{fp}/undo")
    assert "Findings · 8" in env.client.get(h + "/sweep").text
    _post(env, h, f"/sweep/finding/{fp}/mute")
    with svc.sf() as s, tenant_scope(cid):
        assert s.scalars(sa.select(Mute)).one().ended_at is None
    assert "Muted" in env.client.get(h + "/sweep?tab=suppressed").text


def test_watch_this_drafts_replays_and_starts(night):
    env, h, cid, rid, svc = night
    fp = _fp(svc, cid, rid, "rising_variance", "Coimbatore")
    page = env.client.get(f"{h}/sweep/watch/{fp}").text
    assert "Watch this" in page and "Drafted from the finding" in page
    assert "p90 − p10 days, 7-day window" in page and "Last 90 days, replayed" in page
    assert "Checked every night · change or stop any time" in page
    thr = re.search(r'name="threshold"[^>]*value="([^"]+)"', page).group(1)
    r = _post(env, h, f"/sweep/watch/{fp}/start", {"threshold": thr, "owner": "Aditya Naik"})
    assert r.status_code == 303 and "tab=watching" in r.headers["location"]
    with svc.sf() as s, tenant_scope(cid):
        w = s.scalars(sa.select(Watch).where(Watch.created_from_finding == fp)).one()
        assert w.owner_name == "Aditya Naik" and w.threshold == thr
        assert s.scalars(sa.select(Event).where(Event.action == "watch.started")).all()
    watching = env.client.get(h + "/sweep?tab=watching").text
    assert "Watching since" in watching and "Breached" in watching
    # stop one of the run's watches: it leaves the tab at once
    with svc.sf() as s, tenant_scope(cid):
        first = s.scalars(sa.select(Watch).order_by(Watch.created_at)).first()
    _post(env, h, f"/sweep/watching/{first.id}/stop")
    assert first.label not in env.client.get(h + "/sweep?tab=watching").text


def test_another_companys_finding_is_not_found(night):
    env, _h, cid, rid, svc = night
    fp = _fp(svc, cid, rid, "cross_source")
    signup(env, email="ops@other.in", company="Other Co", slug="other")
    assert env.client.get(env.host("other") + f"/sweep/finding/{fp}").status_code == 404


def test_the_quiet_night_lists_what_each_detector_checked(night):
    env, h, cid, _rid, svc = night
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        r = orchestrator.start(
            s,
            c,
            kind="nightly",
            run_date=STEADY_RUN + dt.timedelta(days=1),
            now=ctx.clock(),
            image_version="0.1.0",
        )
        s.commit()
        rid2 = r.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid2) == "done"
    env.clock.t += dt.timedelta(days=1)
    page = env.client.get(h + "/sweep").text
    # one page for every night (M15.2): the open findings, then what each detector did
    assert " open in total" in page and "None new</span>" in page and "Open · day 2" in page
    assert "Forecast breach" in page and 'id="detectors"' in page  # M7


def test_first_days(env: Env, sample, need_sources):  # noqa: F811
    env.clock.t = dt.datetime(2026, 11, 2, 3, 0, tzinfo=dt.UTC)
    signup(env)
    svc = env.app.state.services  # type: ignore[attr-defined]
    with svc.sf() as s, system_scope("test"):
        cid = s.scalars(sa.select(Company.id).where(Company.slug == "indigothread")).one()
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    confirm_sample(ctx, cid, sample)
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        r = orchestrator.start(
            s,
            c,
            kind="nightly",
            run_date=dt.date(2026, 11, 2),
            now=ctx.clock(),
            image_version="0.1.0",
        )
        s.commit()
        rid = r.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid) == "done"
    page = env.client.get(env.host("indigothread") + "/sweep").text
    # the history note while detectors run partly; the table says when each turns on fully
    assert "<b>Day 1 of 28.</b>" in page and "Full coverage from" in page
    assert "still building history · turns on fully around" in page
    # M15.7b: a partly running detector's card says what it covered and what it is waiting on
    partly = re.findall(r'<li class="det partly(?: end)?"[^>]*data-key="([a-z_]+)"', page)
    cards = [_det_card(page, k) for k in partly]
    assert cards and all("Partly" in c and "Today: " in c and " of " in c for c in cards)
    assert any("Still building history" in c or "still building history" in c for c in cards)
    assert (
        "Metrics with enough history" in page
        and "Tests today" in page
        and "at most 1 in 100 could be chance" in page
    )
    assert "False discovery rate 1%" in page  # the term, behind the (i)
    assert "Partly" in page and "Running" in page


def test_a_known_pause_is_an_ontology_record_and_shows_in_coverage_that_night(night):
    from othor.ontology.models import OntologyRecord, OntologyVersion

    env, h, cid, rid, svc = night
    fp = _fp(svc, cid, rid, "missing_records", "Ravira Exports")
    with svc.sf() as s, tenant_scope(cid):
        versions = s.scalar(sa.select(sa.func.count()).select_from(OntologyVersion))
    r = _post(env, h, f"/sweep/finding/{fp}/pause", {"until": "2026-12-15", "reason": "EU audit"})
    assert r.status_code == 303
    with svc.sf() as s, tenant_scope(cid):
        assert s.scalar(sa.select(sa.func.count()).select_from(OntologyVersion)) == versions + 1
        rec = s.scalars(
            sa.select(OntologyRecord).where(
                OntologyRecord.type == "known_pattern", OntologyRecord.status == "active"
            )
        ).all()
        assert any(x.body.get("entity") == "Ravira Exports" for x in rec)
        assert s.scalars(sa.select(Event).where(Event.object_type == "ontology_version")).all()
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    with svc.sf() as s, tenant_scope(cid):
        c = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        nxt = orchestrator.start(
            s,
            c,
            kind="nightly",
            run_date=STEADY_RUN + dt.timedelta(days=1),
            now=ctx.clock(),
            image_version="0.1.0",
        )
        s.commit()
        rid2 = nxt.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid2) == "done"
    doc = read_json(RunFiles(svc.settings.runs_dir(cid), rid2).outputs_json)["sweep"]
    (mr,) = [c for c in doc["coverage"] if c["detector"] == "missing_records"]
    assert any(n.startswith("Ravira Exports · paused until 15 Dec") for n in mr["notes"]), mr[
        "notes"
    ]


def test_suppressed_lists_one_item_per_pattern_with_its_metrics(night):
    env, h, *_ = night
    page = env.client.get(h + "/sweep?tab=suppressed").text
    assert page.count("<summary") >= 2
    diwali = page[page.index("Diwali week") :]
    assert "known pattern" in diwali and "net revenue (week of 19 Oct)" in diwali
    surat = page[page.index("Surat plant shutdown") :]
    assert "catch-up week of 24 Aug" in surat and "confirmed 28 Oct" in surat


def test_the_runs_menu_dates_each_run_as_the_page_does_and_says_its_kind(night):
    """#39: the menu showed each run's last day of data (run date − 1) while the page header shows
    the run date, and two runs of one day (the first run and the night) read the same: each item
    now carries the header's date and what kind of run it was."""
    from othor.core import fmt

    env, h, cid, _rid, svc = night
    page = env.client.get(h + "/sweep").text
    menu = page[
        page.index('class="use-menu"') : page.index("</details>", page.index('class="use-menu"'))
    ]
    with svc.sf() as s, tenant_scope(cid):
        runs = s.scalars(sa.select(Run).where(Run.status.in_(("done", "partial")))).all()
    for r in runs:
        if f"?run={r.number}" not in menu:
            continue
        item = menu[menu.index(f"?run={r.number}") :][:400]
        assert fmt.date_short(r.run_date) in item, (r.number, item)
        assert {"dry": "first run", "nightly": "nightly"}.get(r.kind, "") in item, (r.kind, item)


def test_the_sql_tab_loads_only_its_own_statements(night):
    """M15.1: the SQL tab loaded every statement of the run (about 154, with their SQL and
    params) to match a handful by text; it now asks for those by hash."""
    env, h, cid, rid, svc = night
    fp = re.search(r"/sweep/finding/([0-9a-f]{8,})", env.client.get(h + "/sweep").text).group(1)
    loaded: list[object] = []

    def seen(target, _ctx):
        loaded.append(target)

    sa.event.listen(RunQuery, "load", seen)
    try:
        page = env.client.get(h + f"/sweep/finding/{fp}?tab=sql").text
    finally:
        sa.event.remove(RunQuery, "load", seen)
    shown = page.count("<pre")
    with svc.sf() as s, tenant_scope(cid):
        total = s.scalar(
            sa.select(sa.func.count()).select_from(RunQuery).where(RunQuery.run_id == rid)
        )
    assert shown and len(loaded) <= max(shown * 2, 4) < total, (len(loaded), shown, total)


def test_how_is_this_calculated_for_net_hiding_gross(night):
    """M15.3, the Test tab: what it tested, the waterfall from the stored contributions, the gates
    with the stored result's values, the money's arithmetic, the numbers it used."""
    from decimal import Decimal

    from othor.engine import sweep_text

    env, h, cid, rid, svc = night
    fp = _fp(svc, cid, rid, "net_hiding_gross")
    with svc.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        rv = sv.load(s, svc.settings, run, run.run_date)
        f = sv.finding(rv, fp)
        body = sv.panel(rv, s, f, "test")["body"]
        result = sv._result(rv, f)
    assert body["tested"].startswith("Is October’s flat total hiding regions")
    # the waterfall: first total + the shown parts + "Other" = the last total (to 0.01 Cr)
    cats = body["moved"]["chart"].categories
    assert cats[0].total and cats[-1].total and cats[0].key == "Sep total"
    assert abs(cats[0].value + sum(c.value for c in cats[1:-1]) - cats[-1].value) < 0.02
    contrib = {k: Decimal(v) for k, v in f["slots"]["contributions"].items()}
    for c in cats[1:-1]:
        if c.key != "Other":
            assert c.value == float(round(contrib[c.key] / Decimal(10) ** 7, 2))
    # the gates carry the stored values
    g = {x["name"]: x for x in body["gates"]}
    assert body["gates_title"] == "4 gates, all passed"
    stat = Decimal(str(result["statistic"]))
    assert g["The parts offset each other"]["value"] == f"{stat:.1f}×"
    assert g["It is material"]["value"] == sweep_text.money(f["money"])
    assert "your floor is ₹5.0 L" in g["It is material"]["why"]
    assert f"all {result['params']['tests']} tests" in g["It isn’t chance"]["why"]
    assert body["stake"].startswith("= South’s fall, Sep ")
    assert body["moved"]["rows"][-1][0] == "Total"
    page = env.client.get(f"{h}/sweep/finding/{fp}?tab=test").text
    assert "<svg" in page and "Open the metric" in page and "See the SQL" in page


def test_history_and_add_to_a_brief_as_a_dialog(night):
    """4g: the nights it was found; Add to a brief opens a dialog over the panel and the panel
    stays after adding (M15.3)."""
    env, h, cid, rid, svc = night
    fp = _fp(svc, cid, rid, "changepoint")
    page = env.client.get(f"{h}/sweep/finding/{fp}").text
    assert ">History<" in page and "Found · " in page
    assert 'id="add-brief"' in page and 'data-dialog-open="add-brief"' in page
    with svc.sf() as s, tenant_scope(cid):
        from othor.briefs.models import Brief

        bid = s.scalars(sa.select(Brief.id)).first()
        number = sv.detecting_runs(s)[0].number
    r = _post(env, h, f"/sweep/finding/{fp}/brief?run={number}&back=panel", {"brief": str(bid)})
    assert r.status_code == 303
    assert r.headers["location"] == f"/sweep/finding/{fp}?run={number}&added={bid}"
