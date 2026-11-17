# SPDX-License-Identifier: Apache-2.0
"""The open core without the website (M20.2): the app alone, nothing of othor.ai's site, and
nothing for search engines to index."""

from __future__ import annotations

from pathlib import Path

import pytest

from othor.core import plugins
from tests.web.conftest import ROOT, make_env

SITE_PATHS = [
    "/product",
    "/how-it-works",
    "/learn",
    "/learn/forecasts",
    "/whats-new",
    "/privacy",
    "/terms",
    "/request-access",
    "/contact",
    "/ips",
    "/llms.txt",
    "/performance",
]


@pytest.fixture(autouse=True)
def fresh():
    plugins.reset()
    yield
    plugins.reset()


def test_the_app_alone(tmp_path: Path) -> None:
    with make_env(tmp_path, plugins="none", public_site=True) as env:
        c = env.client
        home = c.get(ROOT + "/")
        # no landing: the root host asks which company (multi) as on an instance without a site
        assert home.status_code == 200 and "site-h" not in home.text
        assert home.headers["x-robots-tag"] == "noindex, nofollow"
        for p in SITE_PATHS:
            assert c.get(ROOT + p).status_code == 404, p
        assert c.get(ROOT + "/robots.txt").text == "User-agent: *\nDisallow: /\n"
        assert c.get(ROOT + "/sitemap.xml").status_code == 404
        # no Request access, no Terms/Privacy unless the install sets them
        signup = c.get(ROOT + "/signup").text
        assert "request access" not in signup.lower() and "/terms" not in signup


def test_an_install_can_set_its_own_links(tmp_path: Path) -> None:
    with make_env(
        tmp_path,
        plugins="none",
        legal_url="https://acme.example/legal",
        support_email="help@acme.example",
        signup="open",
    ) as env:
        signup = env.client.get(ROOT + "/signup").text
        assert 'href="https://acme.example/legal/terms"' in signup
