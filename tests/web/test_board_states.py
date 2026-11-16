# SPDX-License-Identifier: Apache-2.0
"""The board-states registry (M12, tests/fixtures/board_states.py): every build board is
registered or listed with a reason, and every registered state renders (200) in its world.
The `night` world needs `make dev && make seed`."""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa

from othor.connectors.models import Source
from othor.core.tenancy import tenant_scope
from othor.engine.models import Finding
from tests.fixtures import board_states as bs
from tests.web.board_worlds import (  # noqa: F401
    PREPS,
    WORLD_FIXTURE,
    apply,
    auth_world,
    night_world,
    setup_world,
)
from tests.web.conftest import ROOT, Env, signup
from tests.web.test_brief_web import _brief_id
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401

ROOT_DIR = bs.ROOT


def night_values(svc, cid, rid) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """The path values of the night world: the brief, one finding per detector, the ERP source."""
    out = {"bid": str(_brief_id(svc, cid))}
    with svc.sf() as s, tenant_scope(cid):
        rows = s.scalars(sa.select(Finding).where(Finding.run_id == rid)).all()
        for r in sorted(rows, key=lambda r: (r.breakdown_value is not None, r.fingerprint)):
            out.setdefault(f"fp_{r.detector}", r.fingerprint)
        src = s.scalars(sa.select(Source).order_by(Source.created_at)).first()
        out["sid"] = str(src.id) if src else ""
    return out


@pytest.mark.skipif(
    not (bs.ROOT / "docs" / "08-screens.md").exists(),
    reason="the design-board list (docs/08) is internal; not in the public repository",
)
def test_every_build_board_is_registered_or_pending() -> None:
    boards = bs.build_boards()
    assert len(boards) > 100
    both = set(bs.STATES) & set(bs.PENDING)
    assert both == set(), f"registered and pending: {sorted(both)}"
    missing = [b for b in boards if b not in bs.STATES and b not in bs.PENDING]
    assert missing == [], f"build boards with no state and no reason: {missing}"
    unknown = sorted((set(bs.STATES) | set(bs.PENDING)) - set(boards))
    assert unknown == [], f"not a build board in docs/08: {unknown}"
    for st in bs.STATES.values():
        assert st.world in {
            "root",
            "company",
            "fresh",
            "night",
            "components",
            "auth",
            "setup",
            "mail",
            "rules",
        }, st.id
        assert not st.prep or st.prep in PREPS, (st.id, st.prep)
        assert not st.prep or st.world in WORLD_FIXTURE, (st.id, st.world)
        if st.world == "mail":
            assert (ROOT_DIR / "tests/ui/mail/golden" / st.path).exists(), st.id
        if st.world == "rules":
            assert st.note, st.id


def test_signed_out_fresh_and_component_states_render(env: Env) -> None:
    signup(env)
    h = env.host("indigothread")
    for s in bs.STATES.values():
        if (s.world == "fresh" or s.world == "components") and not s.prep:
            r = env.client.get(h + s.path, follow_redirects=False)
            assert r.status_code == 200, (s.id, s.path, r.status_code)
    env.client.cookies.clear()
    for s in bs.STATES.values():
        if s.world in ("root", "company") and not s.prep:
            base = ROOT if s.world == "root" else h
            r = env.client.get(base + s.path, follow_redirects=False)
            assert r.status_code == 200, (s.id, s.path, r.status_code)


def test_night_states_render(night):  # noqa: F811
    env, h, cid, rid, svc = night
    values = night_values(svc, cid, rid)
    for s in bs.STATES.values():
        if s.world != "night" or s.prep:
            continue
        missing = bs.placeholders(s.path) - set(values)
        assert not missing, (s.id, missing)
        path = bs.fill(s.path, values)
        r = env.client.get(h + path, follow_redirects=False)
        assert r.status_code == 200, (s.id, path, r.status_code)


PREPPED = sorted(
    (
        s.id
        for s in bs.STATES.values()
        if s.world in WORLD_FIXTURE and (s.prep or s.world != "night")
    ),
    key=lambda i: (bs.STATES[i].world, i),
)


@pytest.mark.skipif(
    not os.environ.get("OTHOR_TEST_BOARDS"),
    reason="each state builds its own world (about 30 min in all): make test-boards",
)
@pytest.mark.parametrize("sid", PREPPED)
def test_prepared_state_renders(sid: str, request: pytest.FixtureRequest) -> None:
    """Each state with its own world: build the world, run the prep, the page renders (200)."""
    st = bs.STATES[sid]
    w = request.getfixturevalue(WORLD_FIXTURE[st.world])
    values = apply(st, w)
    missing = bs.placeholders(st.path) - set(values)
    assert not missing, (sid, missing)
    r = w.env.client.get(w.host + bs.fill(st.path, values), follow_redirects=False)
    assert r.status_code == 200, (sid, r.status_code, r.headers.get("location"))
