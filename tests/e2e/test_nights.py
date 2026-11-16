# SPDX-License-Identifier: Apache-2.0
"""Ten real nights, 23 Oct – 1 Nov 2026, then the quiet night of 4 Nov (M6a acceptance, decided
5 Nov 2026). Each night the sources are loaded as of that night from the generator into the e2e
databases, and the nightly run reads them the real way: read, materialise, probes, derive,
detect, seal. On top: the earlier nightly snapshots (a fixture run on 22 Oct), the Skarfab
answer given on 30 Oct, and the 5e watches on the dates they were started.

Asserts the change tags across nights; P16 open on 31 Oct and Resolved on 1 Nov with the same
fingerprint (its re-read in `run_query`); the 1 Nov counts Findings 8 · Watching 4 · Resolved 2;
replay of every run; and the quiet night: no new findings, P7's watch still breached.
Needs `make dev` (the sample MySQL and Postgres).
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest
import sqlalchemy as sa

from othor.connectors.models import RunQuery
from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from othor.engine import orchestrator
from othor.engine.replay import replay
from othor.engine.store import RunFiles, read_json
from othor.ontology import service
from tests.fixtures.sample_company import e2e
from tests.fixtures.sample_company.history import prior_snapshots
from tests.fixtures.sample_company.ontology import known_patterns, surat_answer
from tests.fixtures.sample_company.picture import check, picture
from tests.integration.run_world import add_watches, confirm_sample, engine_ctx, require_sources
from tests.integration.setup_world import OWNER
from tests.integration.test_seed import DW_ADMIN, ERP_ADMIN

D = dt.date
# 23 Oct – 1 Nov; the weekly history job runs about 100 nights to 1 Nov (OTHOR_E2E_NIGHTS)
COUNT = int(os.environ.get("OTHOR_E2E_NIGHTS", "10"))
NIGHTS = [D(2026, 11, 1) - dt.timedelta(days=COUNT - 1 - i) for i in range(COUNT)]
QUIET = D(2026, 11, 4)
ANSWERED = D(2026, 10, 30)  # Skarfab's pause, answered on Home
SURAT_ANSWERED = D(2026, 10, 28)  # the Surat shutdown confirmed on Home


def _clock(night: dt.date):
    # 2:05 AM IST on the night's date
    at = dt.datetime.combine(night - dt.timedelta(days=1), dt.time(20, 35), tzinfo=dt.UTC)
    return lambda: at


@pytest.fixture(scope="module")
def nights(tmp_path_factory):
    """Its own SQLite metadata (module scope: the eleven nights run once for every test)."""
    from othor.core.db import make_engine, make_session_factory
    from othor.core.tenancy import system_scope
    from othor.migrations.runner import upgrade

    require_sources()
    tmp = tmp_path_factory.mktemp("nights")
    url = f"sqlite:///{tmp / 'meta.db'}"
    upgrade(url)
    sf = make_session_factory(make_engine(url))
    with sf() as s, system_scope("e2e"):
        co = Company(slug="indigothread", name="Indigo Thread Co.")
        s.add(co)
        s.commit()
        cid = co.id
    erp_url, dw_url = e2e.ensure_databases(ERP_ADMIN, DW_ADMIN)
    first = NIGHTS[0] - dt.timedelta(days=1)
    ctx = engine_ctx(sf, tmp, clock=_clock(first))
    c = e2e.as_of(first)
    e2e.load_night(c, erp_url, dw_url, ctx.settings.sample_dir / "sheets")
    confirm_sample(ctx, cid, c, with_mis=True, e2e=True, without=("skarfab_pause",))
    with ctx.sf() as s, tenant_scope(cid):
        # the watches started before the first night; each night adds its own (100 nights reach
        # back past 16 Aug, so it must not be added twice: the 100-night check, 13 Nov 2026)
        add_watches(s, before=NIGHTS[0])
        company = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        f = orchestrator.start_fixture(
            ctx,
            s,
            company,
            run_date=first,
            points={"erp_db": prior_snapshots(c, NIGHTS[0])},
            image_version="0.1.0",
        )
        s.commit()
        fid = f.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, fid) == "done"
    runs = {first: fid}
    for night in [*NIGHTS, QUIET]:
        ctx = engine_ctx(sf, tmp, clock=_clock(night))
        e2e.load_night(e2e.as_of(night), erp_url, dw_url, ctx.settings.sample_dir / "sheets")
        with ctx.sf() as s, tenant_scope(cid):
            if night == ANSWERED:  # the owner answers Skarfab's question on Home (M10a)
                answer_on_home(s, night)
            if night == SURAT_ANSWERED:  # "same dates every year" (M7)
                service.apply(
                    s, e2e.retarget(surat_answer()), actor=OWNER, reason="Answered on Home"
                )
            add_watches(s, on=night)
            company = s.scalars(sa.select(Company).where(Company.id == cid)).one()
            r = orchestrator.start(
                s, company, kind="nightly", run_date=night, now=ctx.clock(), image_version="0.1.0"
            )
            s.commit()
            rid = r.id
        with tenant_scope(cid):
            assert orchestrator.execute(ctx, rid) == "done", night
        runs[night] = rid
    return ctx, cid, runs


def answer_on_home(s, night: dt.date) -> None:
    """The Skarfab question the earlier nights raised, answered in the owner's words on Home;
    the model's draft (stubbed) is the fixture's pause, validated and guarded like any other."""
    from othor.home import answers
    from othor.ontology.models import Question

    (pause,) = [p for p in known_patterns() if p.key == "skarfab_pause"]
    (q,) = [
        q
        for q in s.scalars(sa.select(Question).where(Question.status == "open"))
        if (q.subject or {}).get("entity") == "Skarfab"
    ]
    body = {k: v for k, v in pause.body.items() if k != "answered_on"}
    at = dt.datetime.combine(night, dt.time(4, 0), tzinfo=dt.UTC)
    out = answers.free_text(
        s,
        q,
        "Yes, Skarfab paused orders until their EU audit, back in December",
        actor=OWNER,
        now=at,
        today=night,
        draft=lambda inputs: {"record": {"type": "known_pattern", "body": body}, "reply": "Saved"},
    )
    # a typed answer is a draft until the owner applies it (non-negotiable 6; v0.11, 12 Nov 2026)
    assert out.drafted and not out.saved, out
    assert answers.apply_draft(s, q, actor=OWNER, now=at)


def _out(ctx, cid, rid, name):
    return read_json(RunFiles(ctx.settings.runs_dir(cid), rid).outputs / name)


def _p16(found):
    return [
        f
        for f in found
        if f["detector"] == "cross_source" and f["family"].startswith("reconcile:mis")
    ]


def test_p16_opens_on_31_oct_and_is_cleared_by_data_on_1_nov(nights):
    ctx, cid, runs = nights
    (before,) = _p16(_out(ctx, cid, runs[D(2026, 10, 31)], "findings.json"))
    assert before["status"] == "open" and before["state"] == "needs_confirmation"
    (after,) = _p16(_out(ctx, cid, runs[D(2026, 11, 1)], "findings.json"))
    assert after["fingerprint"] == before["fingerprint"]
    assert after["status"] == "resolved" and after["change"] == "Resolved"
    assert after["resolved"]["reason"].startswith("Cleared by data")
    # the look-back re-read the week of 19 Oct from erp_db on 1 Nov
    with ctx.sf() as s, tenant_scope(cid):
        sent = s.scalars(sa.select(RunQuery).where(RunQuery.run_id == runs[D(2026, 11, 1)]))
        reread = [
            q
            for q in sent
            if "credit_notes" in q.sql and str((q.params or {}).get("from", "9999")) <= "2026-10-19"
        ]
    assert reread


def test_the_counts_of_1_nov(nights):
    ctx, cid, runs = nights
    doc = _out(ctx, cid, runs[D(2026, 11, 1)], "outputs.json")["sweep"]
    assert doc["counts"]["findings"] == 8, doc["counts"]
    assert doc["counts"]["watching"] == 4, doc["counts"]
    assert doc["counts"]["resolved"] == 2, doc["counts"]
    assert doc["counts"]["suppressed"] == 2, doc["counts"]  # M7: Diwali week, Surat catch-up
    found = _out(ctx, cid, runs[D(2026, 11, 1)], "findings.json")
    resolved = sorted(f["resolved"]["reason"] for f in found if f["status"] == "resolved")
    assert resolved[0].startswith("Cleared by data")
    assert resolved[1].startswith("Explained by your answer")
    assert "answered 30 Oct" in resolved[1]


def test_the_picture_of_1_nov_is_golden(nights):
    """Every item of 1 Nov (pattern, detector, state, money to ₹1 L) against golden_1nov.json."""
    ctx, cid, runs = nights
    rid = runs[D(2026, 11, 1)]
    got = picture(
        _out(ctx, cid, rid, "findings.json"),
        _out(ctx, cid, rid, "detection.json"),
        _out(ctx, cid, rid, "watches.json"),
    )
    check(got, "nights")


def test_change_tags_across_nights(nights):
    ctx, cid, runs = nights
    seen: dict[str, dt.date] = {}
    for night in NIGHTS:
        for f in _out(ctx, cid, runs[night], "findings.json"):
            if f["status"] != "open":
                continue
            if f["fingerprint"] in seen:
                assert f["change"] != "New", (night, f["detector"], f["metric"])
                assert f["first_seen"]["date"] == seen[f["fingerprint"]].isoformat()
                assert f["days_open"] == (night - seen[f["fingerprint"]]).days + 1
            else:
                assert f["change"] == "New", (night, f["detector"], f["metric"], f["change"])
                seen[f["fingerprint"]] = night


def test_the_quiet_night(nights):
    ctx, cid, runs = nights
    doc = _out(ctx, cid, runs[QUIET], "outputs.json")["sweep"]
    assert doc["counts"]["new"] == 0, doc["counts"]
    ws = {w["label"]: w for w in _out(ctx, cid, runs[QUIET], "watches.json")}
    p7 = ws["Dispatch time spread, Coimbatore · 7 days"]
    assert p7["status"] == "breached"


def test_every_night_replays(nights):
    ctx, cid, runs = nights
    with tenant_scope(cid):
        for night, rid in sorted(runs.items()):
            assert replay(ctx, rid).ok, night


def test_delivery_each_morning_and_an_unsubscribe(nights):
    """M9 acceptance on real nights: 31 Oct goes to both recipients; Priya unsubscribes from that
    email; 1 Nov and 4 Nov go to the owner only, and each email passes the client lint. (4 Nov is
    quiet in Sweep, not in the Leadership brief: its open items still rank.) Delivery reads the
    stored issues only: every run still replays (test_every_night_replays)."""
    from othor.briefs.models import Brief, Recipient
    from othor.mail import delivery
    from othor.mail.recipients import unsubscribe
    from othor.mail.relay import MemoryRelay
    from tests.ui.mail import test_emails as snap

    ctx, cid, _runs = nights
    relay = MemoryRelay()
    with ctx.sf() as s, tenant_scope(cid):
        brief = s.scalars(sa.select(Brief)).one()
        for email, name in (
            ("uk@indigothread.example", "Uday Kamath"),
            ("priya@indigothread.example", "Priya Menon"),
        ):
            if not s.scalars(sa.select(Recipient).where(Recipient.email == email)).first():
                s.add(Recipient(brief_id=brief.id, email=email, name=name, added_by="test"))
        s.commit()
        bid = brief.id

    def morning(day: dt.date) -> list[str]:
        at = dt.datetime.combine(day, dt.time(0, 30), tzinfo=dt.UTC)  # 6:00 AM IST
        before = len(relay.outbox)
        with ctx.sf() as s, tenant_scope(cid):
            company = s.scalars(sa.select(Company).where(Company.id == cid)).one()
            out = delivery.deliver_brief(s, ctx.settings, ctx.key, relay, company, bid, day, at)
        assert out.status == "sent", (day, out.status)
        return sorted(m.to for m in relay.outbox[before:])

    assert morning(D(2026, 10, 31)) == ["priya@indigothread.example", "uk@indigothread.example"]
    with ctx.sf() as s, tenant_scope(cid):
        priya = s.scalars(
            sa.select(Recipient).where(Recipient.email == "priya@indigothread.example")
        ).one()
        unsubscribe(s, priya, dt.datetime(2026, 10, 31, 3, 0, tzinfo=dt.UTC), via="link")
        s.commit()
    assert morning(D(2026, 11, 1)) == ["uk@indigothread.example"]
    assert morning(QUIET) == ["uk@indigothread.example"]
    for i, m in enumerate(relay.outbox):
        snap.EMAILS[f"_night{i}"] = m
        try:
            snap.test_renders_in_gmail_and_outlook(f"_night{i}")
        finally:
            del snap.EMAILS[f"_night{i}"]


SITE_FORECASTS = Path(__file__).resolve().parents[2] / "othor_site" / "data" / "site_forecasts.json"


@pytest.mark.skipif(
    os.environ.get("OTHOR_RECORD_FORECASTS") != "1" or COUNT < 100,
    reason="writes the website's forecast record: 100 nights and OTHOR_RECORD_FORECASTS=1",
)
def test_write_the_sample_forecast_record(nights):
    """v0.19 (M19.6): the sample company's forecast track record after 100 nights, for
    /learn/forecasts. Computed by the run (forecasts.json `track`), written as it is; the site
    never types these figures (the same rule as the Performance page)."""
    import json

    from othor.ontology.models import Spec

    ctx, cid, runs = nights
    rid = runs[D(2026, 11, 1)]
    doc = _out(ctx, cid, rid, "forecasts.json") or {}
    with ctx.sf() as s, tenant_scope(cid):
        from othor.engine.models import Run

        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        spec = dict(s.scalars(sa.select(Spec).where(Spec.id == run.spec_id)).one().compiled)
    names = {k: m.get("name") or k for k, m in spec.get("metrics", {}).items()}
    rows = []
    for series, t in sorted((doc.get("track") or {}).items()):
        metric, grain = series.split("|")
        rows.append(
            {
                "series": series,
                "name": names.get(metric, metric),
                "grain": grain,
                "source": t["source"],
                "n": t["n"],
                "inside": t["inside"],
                "typical_error_pct": t["typical_error_pct"],
                "level": t["level"],
            }
        )
    assert rows, "the 1 Nov run has a track record"
    out = {"nights": COUNT, "as_of": "2026-11-01", "company": "Indigo Thread Co.", "rows": rows}
    SITE_FORECASTS.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
