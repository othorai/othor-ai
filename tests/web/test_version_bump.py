# SPDX-License-Identifier: Apache-2.0
"""v0.15 (M15.9) to v0.24 (18 Nov 2026): the image is 0.26.0; a run sealed under 0.1.0 keeps its own
version, still replays byte for byte and still passes the ledger check (replay
never reads the version; the ledger reads each run's stored one)."""

from __future__ import annotations

import sqlalchemy as sa

from othor import __version__
from othor.core.tenancy import tenant_scope
from othor.engine.context import EngineContext
from othor.engine.ledger import verify
from othor.engine.models import Run
from othor.engine.replay import replay
from tests.web.test_chat_web import need_sources, sample  # noqa: F401
from tests.web.test_sweep_web import night  # noqa: F401


def test_the_image_is_0_20_0() -> None:
    assert __version__ == "0.26.0"


def test_a_run_sealed_under_0_1_0_still_replays_and_verifies(night):  # noqa: F811
    env, h, cid, rid, svc = night
    assert svc.settings.image_version == "0.26.0"
    with svc.sf() as s, tenant_scope(cid):
        run = s.scalars(sa.select(Run).where(Run.id == rid)).one()
        assert run.image_version == "0.1.0"  # the night was sealed as an older image
    ctx = EngineContext(svc.settings, svc.sf, svc.key, clock=lambda: env.clock.t)
    with tenant_scope(cid):
        assert replay(ctx, rid).ok
    with svc.sf() as s, tenant_scope(cid):
        v = verify(s, svc.settings, cid)
    assert v.ok, v.problems
    assert env.client.get(h.replace("indigothread.", "") + "/healthz").json()["version"] == "0.26.0"


def test_the_package_and_the_project_say_the_same_version() -> None:
    """0.24.1 shipped saying 0.24.0: pyproject bumped, `othor.__version__` not (18 Nov 2026)."""
    import tomllib
    from pathlib import Path

    project = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())
    assert project["project"]["version"] == __version__
