# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from alembic import context

from othor.all_models import Base
from othor.core.db import make_engine

config = context.config


def _plugin_models() -> None:
    """A plug-in's tables join the metadata (M20.1)."""
    import importlib

    from othor.core import plugins

    for p in plugins.loaded():
        for name in p.models:
            importlib.import_module(name)


_plugin_models()
target_metadata = Base.metadata


def _url() -> str:
    url = config.attributes.get("url") or config.get_main_option("sqlalchemy.url")
    if not url:
        from othor.core.config import get_settings

        url = get_settings().db_url
    return str(url)


def run_offline() -> None:
    context.configure(
        url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    engine = make_engine(_url())
    with engine.connect() as conn:
        context.configure(
            connection=conn,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
