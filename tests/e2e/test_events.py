# SPDX-License-Identifier: Apache-2.0
"""The sample company's next weeks on real nights (docs/plans/sample-demo-events.md).

The nights from 20 Nov 2026 (three before the events start on 23 Nov; by default to
2 Jan 2027; OTHOR_EVENT_NIGHTS=103 runs to 2 Mar, the daily 100-night brief test's window),
the sources loaded as of each night from the generator with its events
(tests/fixtures/sample_company/events.py), read and run the real way, as
tests/e2e/test_nights.py does for Oct. The owner's answers are given up front (Skarfab's pause,
the Surat shutdown) and the 5e watches are on, as production's Indigo Thread has them.

Each morning's Leadership issue and its email subject are kept (OTHOR_EVENTS_SUMMARY=<path>
writes them as JSON), and each event is checked on the night it should show: the tag, the
detector, a finding cleared by the data, the subject line. Needs `make dev`.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from othor.core.models import Company
from othor.core.tenancy import tenant_scope
from othor.engine import orchestrator
from othor.engine.replay import replay
from othor.engine.store import RunFiles, read_json
from tests.fixtures.sample_company import e2e
from tests.fixtures.sample_company.history import prior_snapshots
from tests.integration.run_world import confirm_sample, engine_ctx, require_sources
from tests.integration.test_seed import DW_ADMIN, ERP_ADMIN

D = dt.date
FIRST = D(2026, 11, 20)  # three nights before the seam, so 23 Nov isn't all New
COUNT = int(os.environ.get("OTHOR_EVENT_NIGHTS", "44"))  # 20 Nov – 2 Jan; 103: to 2 Mar
NIGHTS = [FIRST + dt.timedelta(days=i) for i in range(COUNT)]


def _clock(night: dt.date):
    at = dt.datetime.combine(night - dt.timedelta(days=1), dt.time(20, 35), tzinfo=dt.UTC)
    return lambda: at


def _out(ctx, cid, rid, name):
    return read_json(RunFiles(ctx.settings.runs_dir(cid), rid).outputs / name)


@pytest.fixture(scope="module")
def nights(tmp_path_factory):
    from othor.core.db import make_engine, make_session_factory
    from othor.core.tenancy import system_scope
    from othor.migrations.runner import upgrade

    require_sources()
    tmp = tmp_path_factory.mktemp("events")
    url = f"sqlite:///{tmp / 'meta.db'}"
    upgrade(url)
    sf = make_session_factory(make_engine(url))
    with sf() as s, system_scope("e2e"):
        co = Company(slug="indigothread", name="Indigo Thread Co.")
        s.add(co)
        s.commit()
        cid = co.id
    erp_url, dw_url = e2e.ensure_databases(ERP_ADMIN, DW_ADMIN)
    first = FIRST - dt.timedelta(days=1)
    ctx = engine_ctx(sf, tmp, clock=_clock(first))
    c = e2e.as_of(first)
    e2e.load_night(c, erp_url, dw_url, ctx.settings.sample_dir / "sheets")
    confirm_sample(ctx, cid, c, with_mis=True, e2e=True, watches=True, surat_answered=True)
    with ctx.sf() as s, tenant_scope(cid):
        company = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        f = orchestrator.start_fixture(
            ctx,
            s,
            company,
            run_date=first,
            points={"erp_db": prior_snapshots(c, FIRST)},
            image_version="0.1.0",
        )
        s.commit()
        fid = f.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, fid) == "done"
    runs = {first: fid}
    log: dict[dt.date, dict[str, Any]] = {}
    for night in NIGHTS:
        ctx = engine_ctx(sf, tmp, clock=_clock(night))
        e2e.load_night(e2e.as_of(night), erp_url, dw_url, ctx.settings.sample_dir / "sheets")
        with ctx.sf() as s, tenant_scope(cid):
            company = s.scalars(sa.select(Company).where(Company.id == cid)).one()
            r = orchestrator.start(
                s, company, kind="nightly", run_date=night, now=ctx.clock(), image_version="0.1.0"
            )
            s.commit()
            rid = r.id
        with tenant_scope(cid):
            assert orchestrator.execute(ctx, rid) == "done", night
        runs[night] = rid
        log[night] = morning(ctx, cid, rid, night)
    path = os.environ.get("OTHOR_EVENTS_SUMMARY")
    if path:
        Path(path).write_text(
            json.dumps({d.isoformat(): v for d, v in log.items()}, indent=1, ensure_ascii=False)
        )
    return ctx, cid, runs, log


def morning(ctx, cid, rid, night: dt.date) -> dict[str, Any]:
    """The Leadership issue of the night, its email subject, and the night's finding changes."""
    from othor.briefs.models import Brief, BriefIssue
    from othor.mail.delivery import brief_mail
    from othor.mail.messages import brief_subject

    with ctx.sf() as s, tenant_scope(cid):
        company = s.scalars(sa.select(Company).where(Company.id == cid)).one()
        brief = s.scalars(sa.select(Brief).where(Brief.name == "Leadership")).one()
        issue = s.scalars(
            sa.select(BriefIssue).where(BriefIssue.brief_id == brief.id, BriefIssue.run_id == rid)
        ).one()
        subject = brief_subject(brief_mail(s, company, brief, issue))
        body = dict(issue.body)
    found = _out(ctx, cid, rid, "findings.json")
    return {
        "title": issue.title,
        "subject": subject,
        "quiet": bool(body.get("quiet")),
        "items": [
            {"title": i.get("title"), "change": i.get("change"), "detector": i.get("detector")}
            for i in body.get("items") or []
        ],
        "still_open": [x.get("title") for x in body.get("still_open") or []],
        # 0.28.5: "Since last week"
        "week": {
            "line": (body.get("week") or {}).get("line"),
            "fixed": [(x["title"], x["how"]) for x in (body.get("week") or {}).get("fixed") or []],
        },
        "changes": [
            {
                "detector": f["detector"],
                "metric": f.get("metric"),
                "change": f.get("change"),
                "status": f["status"],
                "title": (f.get("text") or {}).get("title", ""),
                "reason": (f.get("resolved") or {}).get("reason"),
            }
            for f in found
            if f.get("change") in ("New", "Worse", "Better", "Came back", "Resolved")
        ],
    }


def _changes(log, day: dt.date, detector: str, change: str) -> list[dict[str, Any]]:
    return [x for x in log[day]["changes"] if x["detector"] == detector and x["change"] == change]


def _between(log, lo: dt.date, hi: dt.date, detector: str, change: str) -> list[dt.date]:
    return [d for d in sorted(log) if lo <= d <= hi and _changes(log, d, detector, change)]


def test_every_night_has_a_leadership_issue_and_a_dated_subject(nights):
    _ctx, _cid, _runs, log = nights
    for night, m in log.items():
        assert m["subject"].endswith(night.strftime("%-d %b")), (night, m["subject"])
        assert " · Leadership · " in m["subject"]


def test_e1_ravira_back_clears_its_silence(nights):
    """Ravira's invoice of 24 Nov is in the data of 25 Nov: its silence is cleared by data."""
    _ctx, _cid, _runs, log = nights
    got = _between(log, D(2026, 11, 25), D(2026, 11, 27), "missing_records", "Resolved")
    assert got, [log[d]["changes"] for d in (D(2026, 11, 25), D(2026, 11, 26))]


def test_south_is_cleared_once_november_is_in(nights):
    """P1 compares September and October; with November complete it is cleared by data."""
    _ctx, _cid, _runs, log = nights
    assert _changes(log, D(2026, 12, 1), "net_hiding_gross", "Resolved")


def test_e3_the_clean_up_shows_as_better(nights):
    """Six silent customers marked inactive on 1 Dec: the 90-day count is Better on 2 Dec."""
    _ctx, _cid, _runs, log = nights
    (got,) = _changes(log, D(2026, 12, 2), "missing_records", "Better")
    assert got["title"].startswith("10 customers")


def test_e6_receivables_over_60_days_get_worse(nights):
    _ctx, _cid, _runs, log = nights
    got = _between(log, D(2026, 12, 7), D(2026, 12, 20), "changepoint", "Worse")
    assert any(
        "over 60 days" in x["title"] for d in got for x in _changes(log, d, "changepoint", "Worse")
    ), got


def test_e8_a_calm_week_and_e10_the_year_end_push_are_quiet(nights):
    """Mid-December nothing new or worse for a week, and the year-end invoicing push (the
    quarter-end known pattern) isn't a finding: those mornings lead with what's still open.
    (0.28.5: an item now stays a week after its last change, as 0.28.4 meant; before, items
    left early, counted from the day Sweep first saw them, and these mornings read Quiet day.)"""
    _ctx, _cid, _runs, log = nights
    calm = [D(2026, 12, d) for d in (15, 16, 17, 18, 19, 20, 28, 29, 30, 31)]
    for d in calm:
        moved = [i for i in log[d]["items"] if str(i["change"]).startswith(("New", "Worse"))]
        assert not moved, (d, moved)
        assert log[d]["subject"].startswith(("Still open", "Quiet day")), (d, log[d]["subject"])
    for d in calm[6:]:  # the year-end push: no revenue finding opens for it
        assert not _changes(log, d, "forecast_breach", "New"), d
    # an item leaves the top items a week after its last change (25 Dec: Better)
    assert any("customers" in str(i["title"]) for i in log[D(2026, 12, 31)]["items"])
    assert not any("customers" in str(i["title"]) for i in log[D(2027, 1, 2)]["items"])


def test_not_every_morning_says_still_open(nights):
    """The point of the events: the subject leads with what changed on enough mornings."""
    _ctx, _cid, _runs, log = nights
    fresh = [d for d, m in log.items() if not m["subject"].startswith("Still open")]
    assert len(fresh) >= len(log) // 5, [(d, log[d]["subject"]) for d in sorted(log)]


def test_since_last_week_says_what_got_fixed_and_how(nights):
    """0.28.5: Ravira's silence, cleared on 25 Nov, is under Fixed ("Back to normal on 25 Nov")
    for the week after; the calm mid-December mornings still carry the week's line; a week with
    nothing reported, fixed or open has no section."""
    _ctx, _cid, _runs, log = nights
    fixed = log[D(2026, 11, 26)]["week"]["fixed"]
    assert any("Ravira" in t and how.startswith("Back to normal on 2") for t, how in fixed), fixed
    assert not any("Ravira" in t for t, _h in log[D(2026, 12, 10)]["week"]["fixed"])
    for d in (D(2026, 12, 16), D(2026, 12, 18)):
        line = log[d]["week"]["line"] or ""
        assert line.startswith("Last 7 days: "), (d, line)


def test_every_night_replays(nights):
    ctx, cid, runs, _log = nights
    with tenant_scope(cid):
        for night, rid in sorted(runs.items()):
            assert replay(ctx, rid).ok, night
