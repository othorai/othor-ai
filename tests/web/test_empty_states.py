# SPDX-License-Identifier: Apache-2.0
"""Empty states before the first run (M12, docs/ui-review/states.md): every area shows its
dashed empty box with one line of what comes next, and no zero counts or empty controls."""

from __future__ import annotations

import re

import pytest

from tests.web.conftest import Env, signup

EMPTY = {
    "/home": "Nothing to review yet",
    "/brief": "No brief yet",
    "/brief/archive": "No briefs yet",
    "/sweep": "Nothing has fired yet",
    "/ontology": "No Ontology yet",
    "/sources": "No sources connected",
}


def _main(html: str) -> str:
    m = re.search(r"<main[^>]*>(.*?)</main>", html, re.S)
    assert m
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1)))


@pytest.mark.parametrize("path", sorted(EMPTY))
def test_each_area_shows_its_empty_state(env: Env, path: str) -> None:
    signup(env)
    r = env.client.get(env.host("indigothread") + path, follow_redirects=False)
    assert r.status_code == 200
    assert 'class="empty' in r.text or "empty-state" in r.text or "dashed" in r.text
    text = _main(r.text)
    assert EMPTY[path] in text
    assert not re.search(r"\b0 / 0\b|Show 0|· 0 briefs", text), text
