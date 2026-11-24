# SPDX-License-Identifier: Apache-2.0
"""Which watch already follows a finding (What to do, #110; plan § D): one made from it, else one
on the same number (and part), else a count from the same check. Matched by the indicator, so a
watch set up another way (the demo accounts' have no finding) still counts; a stopped one never."""

from __future__ import annotations

import datetime as dt

from othor.core.tenancy import tenant_scope
from othor.engine import watches
from othor.engine.models import Watch


def _w(cid, label, indicator, *, frm=None, stopped=False, owner="Aditya Naik", thr="1.6"):
    return Watch(
        company_id=cid,
        workspace_id=cid,
        label=label,
        indicator=indicator,
        threshold=thr,
        direction="above",
        owner_name=owner,
        created_from_finding=frm,
        since=dt.date(2026, 11, 1),
        stopped_at=dt.datetime(2026, 11, 2, tzinfo=dt.UTC) if stopped else None,
    )


SPREAD = {
    "kind": "spread",
    "metric": "dispatch_time",
    "breakdown": "plant",
    "breakdown_value": "Coimbatore",
}
FINDING = {
    "fingerprint": "f1",
    "metric": "dispatch_time",
    "detector": "rising_variance",
    "entities": [],
    "breakdown_value": "Coimbatore",
}


def test_a_watch_on_the_same_number_and_part_covers_it(session_factory, two_companies):
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        s.add(_w(cid, "Dispatch time spread, Surat", {**SPREAD, "breakdown_value": "Surat"}))
        s.add(_w(cid, "Dispatch time spread, Coimbatore · 7 days", SPREAD))
        s.commit()
        w = watches.covering(s, FINDING)
        assert w is not None and w.label.endswith("Coimbatore · 7 days")
        assert watches.label(w, "days") == "alert above 1.6 days · Aditya Naik"


def test_one_made_from_the_finding_comes_first_and_a_stopped_one_never(
    session_factory, two_companies
):
    cid, _ = two_companies
    with session_factory() as s, tenant_scope(cid):
        s.add(_w(cid, "old", SPREAD, frm="f1", stopped=True))
        assert watches.covering(s, FINDING) is None
        s.add(_w(cid, "by metric", {"kind": "value", "metric": "dispatch_time"}))
        s.add(_w(cid, "made from it", {"kind": "value", "metric": "other"}, frm="f1"))
        s.commit()
        assert watches.covering(s, FINDING).label == "made from it"


def test_a_count_from_the_same_check(session_factory, two_companies):
    cid, _ = two_companies
    silent = {
        "fingerprint": "f2",
        "metric": "invoiced_value",
        "detector": "missing_records",
        "entities": [],
    }
    with session_factory() as s, tenant_scope(cid):
        s.add(
            _w(
                cid,
                "Active customers silent 90+ days",
                {"kind": "finding_slot", "detector": "missing_records", "slot": "count"},
                owner="Priya Menon",
                thr="8",
            )
        )
        s.commit()
        w = watches.covering(s, silent)
        assert watches.label(w) == "alert above 8 · Priya Menon"
        assert watches.covering(s, {**silent, "detector": "cross_source"}) is None
