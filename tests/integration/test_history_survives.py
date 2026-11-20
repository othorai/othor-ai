# SPDX-License-Identifier: Apache-2.0
"""0.28.3 (owner, 19 Nov 2026): a number's history survives Business Model answers that don't
change its values. On indigo2 the customer-list answer put "who is active" on the customer
breakdown; the next night treated 13 numbers as redefined and, their SQL being the same, kept
nothing and read only the refresh window: years of history gone, the briefs quiet. Now each
series has its own meaning; a change that isn't one keeps history, a real one reads the series
again in full, and `othor admin reread` brings a cut history back. Needs `make dev && make seed`.
"""

from __future__ import annotations

import copy
import datetime as dt

import sqlalchemy as sa

from othor.core.tenancy import tenant_scope
from othor.engine.sources import REREAD
from othor.engine.store import read_json, read_points, write_json
from othor.ontology import service
from othor.ontology.models import OntologyRecord
from tests.integration.setup_world import OWNER
from tests.integration.test_run_sample import FIRST, _files, _run
from tests.integration.test_series_versions import world  # noqa: F401

NEXT = FIRST + dt.timedelta(days=1)


def _flow_history(ctx, cid, rid):  # type: ignore[no-untyped-def]
    """metric -> how many total (unbroken) points the run kept, for the flow metrics."""
    f = _files(ctx, cid, rid)
    out: dict[str, int] = {}
    for p in read_points(f.series):
        if p.breakdown == "" and p.grain in ("month", "day"):
            out[p.metric] = out.get(p.metric, 0) + 1
    return out


def _record(ctx, cid, typ, key):  # type: ignore[no-untyped-def]
    with ctx.sf() as s, tenant_scope(cid):
        rec = s.scalars(
            sa.select(OntologyRecord).where(
                OntologyRecord.type == typ,
                OntologyRecord.key == key,
                OntologyRecord.status == "active",
            )
        ).one()
        return copy.deepcopy(rec.body)


def _put(ctx, cid, typ, key, body, reason):  # type: ignore[no-untyped-def]
    with ctx.sf() as s, tenant_scope(cid):
        service.apply(s, [service.put(typ, key, body)], actor=OWNER, reason=reason)
        s.commit()


def test_who_is_active_on_a_breakdown_keeps_every_history(world):  # noqa: F811
    ctx, cid = world
    r1 = _run(ctx, cid, run_date=FIRST)
    before = _flow_history(ctx, cid, r1)
    body = _record(ctx, cid, "breakdown", "customer")
    assert body.get("entity"), "the sample's customers already have a life of their own"
    body["entity"]["active_values"] = ["active", "on hold"]  # what the customer-list answer writes
    _put(ctx, cid, "breakdown", "customer", body, "who is active")
    r2 = _run(ctx, cid, run_date=NEXT)
    after = _flow_history(ctx, cid, r2)
    meta1 = read_json(_files(ctx, cid, r1).source_meta("erp_db"))
    meta2 = read_json(_files(ctx, cid, r2).source_meta("erp_db"))
    # the old hash moved (0.28.2 restarted these series and kept 35 days); the meaning didn't
    old1, old2 = meta1["definitions"]["invoiced_value"], meta2["definitions"]["invoiced_value"]
    assert old2["hash"] != old1["hash"]
    for metric, d in meta1["definitions"].items():
        assert meta2["definitions"][metric]["meaning"] == d["meaning"], metric
    for metric, n in before.items():
        assert after.get(metric, 0) >= n, f"{metric} lost history: {n} → {after.get(metric, 0)}"


def test_a_real_change_of_meaning_reads_the_whole_history_again(world):  # noqa: F811
    ctx, cid = world
    r1 = _run(ctx, cid, run_date=FIRST)
    before = _flow_history(ctx, cid, r1)["invoiced_value"]
    body = _record(ctx, cid, "metric", "invoiced_value")
    floor = {"op": ">", "args": [{"col": "net_amount"}, {"lit": -(10**12)}]}
    body["filters"] = [*(body.get("filters") or []), floor]
    _put(ctx, cid, "metric", "invoiced_value", body, "a real change of meaning")
    r2 = _run(ctx, cid, run_date=NEXT)
    meta1 = read_json(_files(ctx, cid, r1).source_meta("erp_db"))
    meta2 = read_json(_files(ctx, cid, r2).source_meta("erp_db"))
    m1, m2 = meta1["definitions"]["invoiced_value"], meta2["definitions"]["invoiced_value"]
    assert m2["meaning"][""] != m1["meaning"][""]
    # not the 35-day refresh with nothing kept: the whole window, read again
    assert _flow_history(ctx, cid, r2)["invoiced_value"] >= before


def test_a_reread_request_is_honoured_once_and_sealed_with_the_run(world):  # noqa: F811
    ctx, cid = world
    r1 = _run(ctx, cid, run_date=FIRST)
    before = _flow_history(ctx, cid, r1)
    marker = ctx.settings.company_dir(cid) / REREAD
    write_json(marker, {"metrics": ["*"]})
    r2 = _run(ctx, cid, run_date=NEXT)
    f2 = _files(ctx, cid, r2)
    assert (read_json(f2.inputs / "context.json") or {}).get("reread") == ["*"]
    assert not marker.exists()  # done once the run sealed
    after = _flow_history(ctx, cid, r2)
    for metric, n in before.items():
        assert after.get(metric, 0) >= n


def test_the_night_after_the_deploy_restarts_nothing(world):  # noqa: F811
    """The first night on 0.28.3 finds a previous run without meanings: it compares the old hash
    as before, so the release alone versions no series."""
    ctx, cid = world
    r1 = _run(ctx, cid, run_date=FIRST)
    before = _flow_history(ctx, cid, r1)
    path = _files(ctx, cid, r1).source_meta("erp_db")
    meta1 = read_json(path)
    for d in meta1["definitions"].values():
        d.pop("meaning", None)
    write_json(path, meta1)  # as a 0.28.2 night left it
    r2 = _run(ctx, cid, run_date=NEXT)
    meta2 = read_json(_files(ctx, cid, r2).source_meta("erp_db"))
    assert meta2["versions"] == []
    assert all("meaning" in d for d in meta2["definitions"].values())
    after = _flow_history(ctx, cid, r2)
    for metric, n in before.items():
        assert after.get(metric, 0) >= n
