# SPDX-License-Identifier: Apache-2.0
"""Run migrations programmatically (`othor migrate`, tests).

Plug-ins (M20.1) bring their own branch (a version folder with its own branch label): the
locations are the core's, then each loaded plug-in's, and "up to date" means every head is
applied."""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

HERE = Path(__file__).resolve().parent


def _locations() -> list[str]:
    from othor.core import plugins

    extra = [str(p.migrations) for p in plugins.loaded() if p.migrations is not None]
    return [str(HERE / "versions"), *extra]


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE))
    cfg.set_main_option("path_separator", "os")
    # path_separator "os": the folders are joined with ":" (";" on Windows), not spaces
    cfg.set_main_option("version_locations", os.pathsep.join(_locations()))
    cfg.attributes["url"] = url
    return cfg


def upgrade(url: str, revision: str = "heads") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)


def heads() -> frozenset[str]:
    """The newest migration of each branch in the image (the core's, and each plug-in's)."""
    from alembic.script import ScriptDirectory

    return frozenset(ScriptDirectory.from_config(alembic_config("sqlite://")).get_heads())


def head() -> str:
    """The core's newest migration (one branch: kept for callers that show it)."""
    hs = heads()
    return next(iter(hs)) if len(hs) == 1 else ",".join(sorted(hs))


def current_heads(engine: object) -> frozenset[str]:
    """The metadata DB's applied heads (empty: not migrated). Raises when it can't connect."""
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:  # type: ignore[attr-defined]
        return frozenset(MigrationContext.configure(conn).get_current_heads())


def current(engine: object) -> str | None:
    """The metadata DB's revision, as `head()` spells it (None: not migrated at all)."""
    hs = current_heads(engine)
    if not hs:
        return None
    return next(iter(hs)) if len(hs) == 1 else ",".join(sorted(hs))
