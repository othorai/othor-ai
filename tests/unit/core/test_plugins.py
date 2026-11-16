# SPDX-License-Identifier: Apache-2.0
"""Plug-ins (M20.1, v0.20): the core loads extensions it doesn't name, from the `othor.plugins`
entry-point group or OTHOR_PLUGINS; a broken one is skipped, a clash refused."""

from __future__ import annotations

import logging

import pytest

from othor.core import plugins
from othor.core.config import Settings


@pytest.fixture(autouse=True)
def fresh():
    plugins.reset()
    yield
    plugins.reset()


def _settings(spec: str) -> Settings:
    return Settings(env="test", plugins=spec)


def _named(got: tuple[plugins.Plugin, ...]) -> list[str]:
    """The plug-ins a test named (installed ones, the website and othor_ee, load too)."""
    return [p.name for p in got if p.name not in ("site", "ee")]


def test_none_loads_nothing_and_no_setting_loads_only_entry_points():
    assert plugins.load(_settings("none")) == ()


def test_an_explicit_plugin_loads_once():
    got = plugins.load(_settings("tests.plugins.demo_plugin:plugin"))
    assert _named(got) == ["demo"]
    assert plugins.load(_settings("tests.plugins.demo_plugin:plugin")) is got  # cached


def test_a_broken_plugin_is_skipped_and_logged(caplog):
    with caplog.at_level(logging.ERROR, logger="othor.plugins"):
        spec = "tests.plugins.missing:plugin,tests.plugins.demo_plugin:plugin"
        got = plugins.load(_settings(spec))
    assert _named(got) == ["demo"]
    assert "tests.plugins.missing:plugin" in caplog.text


def test_a_job_kind_that_clashes_with_the_core_is_refused(caplog):
    with caplog.at_level(logging.ERROR, logger="othor.plugins"):
        got = plugins.load(_settings("tests.plugins.demo_plugin:clashing"))
    assert _named(got) == []
    assert "run" in caplog.text


def test_parts_are_gathered_across_plugins():
    plugins.load(_settings("tests.plugins.demo_plugin:plugin"))
    assert "demo_job" in plugins.jobs()
    assert [n.key for n in plugins.nav("company")] == ["demo"]
    assert [f.slot for f in plugins.fragments("settings.company.advanced")] == [
        "settings.company.advanced"
    ]
    assert plugins.fragments("nowhere") == ()


def test_on_load_registers_a_plugin_detector():
    from othor.engine.detectors.base import REGISTRY

    try:
        plugins.load(_settings("tests.plugins.demo_plugin:with_detector"))
        assert "demo_detector" in REGISTRY
    finally:
        REGISTRY.pop("demo_detector", None)
