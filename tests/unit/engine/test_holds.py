# SPDX-License-Identifier: Apache-2.0
"""Snooze and mute (M6b, answered 5 Nov 2026): the page applies them at once through one overlay
that every count reads; the engine applies them from the next run, as run inputs, so replay of
every run stays identical. A snoozed finding is Suppressed, never Resolved; when the snooze ends
it comes back with its first-seen date."""

from __future__ import annotations

import datetime as dt

from othor.engine import findings
from othor.engine.sweep import carry_context, run_sweep
from tests.detectors.synth import RUN, noise, points, spec, weeks


def _world():
    w = weeks(80)
    vals = 1_000_000 + 20_000 * noise(7, 80)
    vals[72:] += 900_000
    pts = points("net_revenue", "week", w, vals)
    doc = spec(net_revenue={"name": "Net revenue", "unit": "inr", "kind": "flow"})
    doc["detectors"] = {**doc["detectors"], "materiality_inr": "1"}
    return doc, pts


def _night(doc, pts, day, context):
    return run_sweep(doc, "h", day, [], pts, {}, context)


def _open(out):
    return [f for f in out.findings if f["status"] == "open"]


def test_a_snooze_is_a_run_input_and_holds_the_finding_until_its_date():
    doc, pts = _world()
    first = _night(doc, pts, RUN, {})
    (f,) = _open(first)
    until = RUN + dt.timedelta(days=7)
    holds = {"snoozes": [{"fingerprint": f["fingerprint"], "until": until.isoformat()}]}
    ctx = carry_context(first.findings, [], [], holds)
    second = _night(doc, pts, RUN + dt.timedelta(days=1), ctx)
    assert not _open(second)
    (held,) = [x for x in second.findings if x["fingerprint"] == f["fingerprint"]]
    assert held["status"] == "snoozed" and held["snoozed_until"] == until.isoformat()
    assert held["first_seen"] == f["first_seen"]
    assert second.doc["counts"]["findings"] == 0 and second.doc["counts"]["suppressed"] >= 1
    assert second.doc["counts"]["resolved"] == 0  # snoozed is not resolved
    # the same inputs give the same bytes (replay)
    again = _night(doc, pts, RUN + dt.timedelta(days=1), ctx)
    assert again.doc["findings_hash"] == second.doc["findings_hash"]
    # after the date it comes back, open since the first night
    ctx3 = carry_context(second.findings, [], [], holds)
    third = _night(doc, pts, until + dt.timedelta(days=1), ctx3)
    (back,) = _open(third)
    assert back["fingerprint"] == f["fingerprint"] and back["change"] != "New"
    assert back["first_seen"] == f["first_seen"]


def test_a_mute_holds_the_pattern_in_any_direction():
    doc, pts = _world()
    first = _night(doc, pts, RUN, {})
    (f,) = _open(first)
    holds = {"mutes": [{"pattern": findings.pattern_of(f)}]}
    second = _night(
        doc, pts, RUN + dt.timedelta(days=1), carry_context(first.findings, [], [], holds)
    )
    (held,) = [x for x in second.findings if x["fingerprint"] == f["fingerprint"]]
    assert held["status"] == "muted" and not _open(second)


def test_the_overlay_applies_at_once_and_every_count_reads_it():
    doc, pts = _world()
    out = _night(doc, pts, RUN, {})
    (f,) = _open(out)
    snoozes = [{"fingerprint": f["fingerprint"], "until": (RUN + dt.timedelta(days=7)).isoformat()}]
    shown = findings.overlay(out.findings, snoozes, [], RUN)
    assert [x["status"] for x in shown if x["fingerprint"] == f["fingerprint"]] == ["snoozed"]
    c = findings.view_counts(shown, out.watches, out.doc["counts"])
    assert c["findings"] == out.doc["counts"]["findings"] - 1
    assert c["suppressed"] == out.doc["counts"]["suppressed"] + 1
    # the stored run is untouched
    assert [x["status"] for x in out.findings] == ["open"]
    # an expired snooze or none: as stored
    assert findings.overlay(out.findings, [], [], RUN) == out.findings
    late = RUN + dt.timedelta(days=8)
    assert findings.overlay(out.findings, snoozes, [], late) == out.findings


def test_undo_at_view_time_brings_a_held_finding_back():
    doc, pts = _world()
    first = _night(doc, pts, RUN, {})
    (f,) = _open(first)
    holds = {"snoozes": [{"fingerprint": f["fingerprint"], "until": "2099-01-01"}]}
    second = _night(
        doc, pts, RUN + dt.timedelta(days=1), carry_context(first.findings, [], [], holds)
    )
    shown = findings.overlay(second.findings, [], [], RUN + dt.timedelta(days=1))
    assert [x["status"] for x in shown if x["fingerprint"] == f["fingerprint"]] == ["open"]
