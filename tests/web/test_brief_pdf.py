# SPDX-License-Identifier: Apache-2.0
"""M15.7 · one brief as a PDF: from the stored issue (the same numbers as the page), the same
bytes twice, run and spec on every page, the owner's and the recipient's download, an event."""

from __future__ import annotations

import io
import re

import sqlalchemy as sa
from pypdf import PdfReader

from othor.core.tenancy import tenant_scope
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def _brief_id(env, h) -> str:
    page = env.client.get(h + "/brief").text
    return re.search(r'href="/brief/(' + UUID + r')"', page).group(1)


def _text(data: bytes) -> tuple[str, PdfReader]:
    r = PdfReader(io.BytesIO(data))
    return "\n".join(p.extract_text() for p in r.pages), r


def _issue(svc, cid):
    from othor.briefs.models import BriefIssue

    with svc.sf() as s, tenant_scope(cid):
        i = s.scalars(sa.select(BriefIssue).order_by(BriefIssue.date.desc())).first()
        return dict(i.body), i.id


def test_the_owner_downloads_the_brief(night):  # noqa: F811
    env, h, cid, _rid, svc = night
    bid = _brief_id(env, h)
    r = env.client.get(h + f"/brief/{bid}/pdf?charts=1&appendix=1")
    assert r.status_code == 200, r.text[:300]
    assert r.headers["content-type"] == "application/pdf"
    assert 'attachment; filename="indigothread-' in r.headers["content-disposition"]
    text, pdf = _text(r.content)
    body, _ = _issue(svc, cid)
    for i in body["items"]:  # every item's money, as stored (the same as the page)
        assert i["money"] in text, i["money"]
    assert "Run #" in text and "spec v" in text and "replayable" in text
    assert "Appendix" in text and "method and settings" in text.lower()
    # A4 for an INR company: 595 × 842 points
    box = pdf.pages[0].mediabox
    assert (round(float(box.width)), round(float(box.height))) == (595, 842)
    # every page carries the footer
    assert all("replayable" in p.extract_text() for p in pdf.pages)
    # the same bytes twice (the roadmap's "done when"), even a minute later: the subset fonts'
    # timestamps are the issue's, not the clock's (CI caught it crossing a second, 14 Nov 2026)
    import time

    import fontTools.misc.timeTools as tt

    real = time.time
    tt.time.time = lambda: real() + 60  # type: ignore[assignment]
    try:
        again = env.client.get(h + f"/brief/{bid}/pdf?charts=1&appendix=1")
    finally:
        tt.time.time = real  # type: ignore[assignment]
    assert again.content == r.content


def test_choices_and_event(night):  # noqa: F811
    from othor.core.models import Event

    env, h, cid, _rid, svc = night
    bid = _brief_id(env, h)
    plain = env.client.get(h + f"/brief/{bid}/pdf")
    text, _ = _text(plain.content)
    assert "Appendix" not in text and "method and settings" not in text.lower()
    full = env.client.get(h + f"/brief/{bid}/pdf?charts=1&appendix=1")
    assert len(full.content) > len(plain.content)
    with svc.sf() as s, tenant_scope(cid):
        evs = s.scalars(sa.select(Event).where(Event.action == "brief.pdf")).all()
    assert len(evs) == 2


def test_another_brief_id_is_not_found(night):  # noqa: F811
    env, h, *_ = night
    r = env.client.get(h + "/brief/00000000-0000-0000-0000-000000000000/pdf")
    assert r.status_code == 404


def test_a_usd_company_gets_letter_and_dollars(night):  # noqa: F811
    import datetime as dt

    from othor.core.models import Company
    from othor.engine import orchestrator
    from othor.engine.context import EngineContext
    from tests.fixtures.sample_company.history import STEADY_RUN
    from tests.web.test_currency_floor import _set

    env, h, cid, _rid, svc = night
    assert _set(env, h, "currency", "USD").status_code == 200
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
        rid = r.id
    with tenant_scope(cid):
        assert orchestrator.execute(ctx, rid) == "done"
    env.clock.t += dt.timedelta(days=1)
    bid = _brief_id(env, h)
    pdf = env.client.get(h + f"/brief/{bid}/pdf?charts=1&appendix=1")
    _text_all, reader = _text(pdf.content)
    box = reader.pages[0].mediabox
    assert (round(float(box.width)), round(float(box.height))) == (612, 792)  # US Letter
    # every amount in dollars; a ₹ can only appear in the owner's own rule texts (the appendix)
    first = reader.pages[0].extract_text()
    assert "$" in first and "₹" not in first
