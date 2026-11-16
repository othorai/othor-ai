# SPDX-License-Identifier: Apache-2.0
"""Missing records: one regular customer gone quiet is Not determinable, with its GSTIN check;
active customers silent 90+ days are Measured with their 12-month value; a regular customer
inside its rhythm, and an irregular one, don't fire; no entity record → needs entity."""

import datetime as dt

from othor.engine.detectors.missing_records import run
from tests.detectors.synth import RUN, context, spec

AS_OF = RUN - dt.timedelta(days=1)


def _events(name, last, every, n, value):
    return [[name, (last - dt.timedelta(days=every * i)).isoformat(), value] for i in range(n)]


def _probe(events, status):
    return {
        "entities": {
            "customer": {
                "metric": "invoiced_value",
                "events": events,
                "status": status,
                "active_values": ["active"],
            }
        }
    }


def test_a_regular_customer_gone_quiet_is_not_determinable_with_checks():
    ev = _events("Ravira Exports", AS_OF - dt.timedelta(days=23), 6, 180, 100_000)
    status = [
        ["Ravira Exports", "active", "27AAACK1234F1Z5", "2023-01-01"],
        ["Other Co", "active", "29BBBCO9999F1Z1", "2022-01-01"],
    ]
    ev += _events("Other Co", AS_OF - dt.timedelta(days=2), 5, 100, 50_000)
    results, cov = run(context(spec(), [], _probe(ev, status)))
    k = next(r for r in results if r.entities == ["Ravira Exports"])
    assert float(k.p_value) < 1e-6 and k.state_hint == "not_determinable"
    assert k.slots["gap_days"] == 23 and k.slots["median_days"] == "6.000000"
    moved = next(x for x in k.reasons if x["key"] == "billing_moved")
    assert moved["ruled_out"] is True and moved["check"]["found"] == []
    year = (
        sum(
            1
            for e in ev
            if e[0] == "Ravira Exports" and (AS_OF - dt.date.fromisoformat(e[1])).days < 365
        )
        * 100_000
    )
    assert abs(float(k.money) - year / 365 * 23) < 1
    assert not any(r.entities == ["Other Co"] and float(r.p_value) < 0.01 for r in results)
    assert cov.status == "ran"


def test_billing_moved_is_not_ruled_out_when_a_new_customer_shares_the_gstin():
    ev = _events("Ravira Exports", AS_OF - dt.timedelta(days=23), 6, 180, 100_000)
    status = [
        ["Ravira Exports", "active", "27AAACK1234F1Z5", "2023-01-01"],
        [
            "Ravira Global",
            "active",
            "27AAACK1234F1Z5",
            (AS_OF - dt.timedelta(days=10)).isoformat(),
        ],
    ]
    results, _ = run(context(spec(), [], _probe(ev, status)))
    k = next(r for r in results if r.entities == ["Ravira Exports"])
    moved = next(x for x in k.reasons if x["key"] == "billing_moved")
    assert moved["ruled_out"] is False and moved["check"]["found"] == ["Ravira Global"]


def test_active_customers_silent_90_days_are_measured():
    ev, status = [], []
    for i in range(14):
        name = f"Quiet {i:02d}"
        ev += _events(name, AS_OF - dt.timedelta(days=95 + i), 20, 20, 30_000)
        status.append([name, "active", None, "2022-01-01"])
    status.append(["Gone", "inactive", None, "2022-01-01"])
    ev += _events("Gone", AS_OF - dt.timedelta(days=200), 20, 20, 30_000)
    results, _ = run(context(spec(), [], _probe(ev, status)))
    g = next(r for r in results if r.state_hint == "measured")
    assert g.slots["count"] == 14 and "Gone" not in g.entities and float(g.p_value) < 1e-9
    expect = sum(
        30_000
        for e in ev
        if e[0].startswith("Quiet") and (AS_OF - dt.date.fromisoformat(e[1])).days < 365
    )
    assert abs(float(g.money) - expect) < 1


def test_irregular_customers_and_no_entity_record():
    import numpy as np

    rng = np.random.default_rng(3)
    last, ev = AS_OF - dt.timedelta(days=20), []
    d = last
    for _ in range(40):
        ev.append(["Lumpy", d.isoformat(), 10_000])
        d -= dt.timedelta(days=int(rng.integers(2, 40)))
    results, _ = run(context(spec(), [], _probe(ev, [["Lumpy", "active", None, "2020-01-01"]])))
    assert all(float(r.p_value) > 0.001 for r in results)
    results, cov = run(context(spec(), [], {}))
    assert results == [] and cov.needs == "entity" and cov.status == "could_not_run"


def test_a_fortnightly_account_is_judged_against_its_own_rhythm():
    """Tessora EU invoices every 10–18 days (median 14): a 23-day gap is within its rhythm; at
    28 days (twice its median, beyond its 95th percentile) it is Missing records (5 Nov 2026)."""

    def world(gap):
        last = AS_OF - dt.timedelta(days=gap)
        ev, d = [], last
        for i in range(60):
            ev.append(["Tessora EU", d.isoformat(), 1_456_000])
            d -= dt.timedelta(days=10 + (i * 7) % 9)  # 10 … 18 days
        status = [["Tessora EU", "active", "DE123", "2022-01-01"]]
        results, _ = run(context(spec(), [], _probe(ev, status)))
        return next(r for r in results if r.entities == ["Tessora EU"])

    calm = world(23)
    assert float(calm.p_value) == 1.0 and calm.reason == "within its own rhythm"
    assert calm.slots["median_days"] == "14.000000"
    late = world(28)
    assert float(late.p_value) < 0.01 and late.state_hint == "not_determinable"
