# SPDX-License-Identifier: Apache-2.0
"""Outbound hosts from plug-ins (Advanced Intelligence Phase 0 §7): a host a loaded plug-in
declares is reachable; without the plug-in it is refused before the request leaves."""

from __future__ import annotations

import httpx
import pytest

from othor.core import http, plugins
from othor.core.plugins import Plugin


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="ok")


@pytest.fixture
def no_plugins(monkeypatch):
    monkeypatch.setattr(plugins, "_loaded", ())
    yield


def test_a_host_is_refused_without_its_plugin(no_plugins):
    with http.outbound_client(httpx.MockTransport(_ok)) as c, pytest.raises(http.HostNotAllowed):
        c.get("https://fbil.org.in/rates")


def test_a_plugins_host_is_allowed(monkeypatch):
    monkeypatch.setattr(plugins, "_loaded", (Plugin("demo", hosts=frozenset({"fbil.org.in"})),))
    assert "fbil.org.in" in http.allowed_hosts()
    with http.outbound_client(httpx.MockTransport(_ok)) as c:
        assert c.get("https://fbil.org.in/rates").text == "ok"
        with pytest.raises(http.HostNotAllowed):
            c.get("https://example.com/")
