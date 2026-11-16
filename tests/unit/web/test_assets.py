# SPDX-License-Identifier: Apache-2.0
"""Alpine.js is vendored, pinned and the CSP build (M12): no CDN, no eval, loaded after othor.js."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STATIC = ROOT / "othor" / "web" / "static"
TEMPLATES = ROOT / "othor" / "web" / "templates"


def test_alpine_is_pinned_in_the_manifest() -> None:
    manifest = json.loads((STATIC / "MANIFEST.json").read_text())
    entry = manifest["js/alpine-csp.min.js"]
    data = (STATIC / "js" / "alpine-csp.min.js").read_bytes()
    assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    assert "@alpinejs/csp@3.14.9" in entry["source"]


def test_alpine_is_the_csp_build() -> None:
    js = (STATIC / "js" / "alpine-csp.min.js").read_text()
    assert "new Function" not in js
    assert not re.search(r"\beval\(", js)


def test_base_loads_alpine_after_othor_js_with_defer() -> None:
    base = (TEMPLATES / "base.html").read_text()
    scripts = re.findall(r'<script src="([^"]+)"([^>]*)>', base)
    srcs = [s for s, _ in scripts]
    assert "/static/js/alpine-csp.min.js" in srcs
    assert srcs.index("/static/js/othor.js") < srcs.index("/static/js/alpine-csp.min.js")
    assert all("defer" in attrs for _, attrs in scripts)


def test_no_template_loads_a_script_from_another_host() -> None:
    """Only the Google Picker on /sources (docs/05) loads from another host."""
    bad = []
    for p in TEMPLATES.rglob("*.html"):
        picker = p.relative_to(TEMPLATES).parts[0] == "sources"
        for src in re.findall(r'<script[^>]*\bsrc="([^"]+)"', p.read_text()):
            if not src.startswith("/static/") and not (
                picker and src.startswith("https://apis.google.com/")
            ):
                bad.append(f"{p.relative_to(ROOT)}: {src}")
    assert bad == []


def test_the_picker_may_load_where_it_is_shown() -> None:
    """The Google Picker is shown on Sources and in setup's Sources step; Google's script and its
    frame are allowed on both, and nowhere else (11 Nov 2026: setup's Picker did nothing)."""
    from othor.web.middleware import security_headers

    def csp(path: str) -> str:
        return dict(security_headers(path, "prod"))[b"content-security-policy"].decode()

    shown = ("/sources/abc", "/setup/sources", "/setup/sources/sheets", "/setup/sources/x/scan")
    for path in shown:
        c = csp(path)
        assert "script-src 'self' https://apis.google.com" in c, path
        assert "frame-src https://docs.google.com" in c, path
    for path in ("/login", "/home", "/brief", "/setup/company", "/"):
        c = csp(path)
        assert "apis.google.com" not in c and "frame-src 'none'" in c, path


def test_picker_pages_send_their_origin_to_google() -> None:
    """11 Nov 2026: Google said "The API developer key is invalid": the key is restricted to
    othor.ai's sites, and with no Referer Google can't check it. The Picker pages send the origin
    only (never the path or query) to other sites; every other page sends nothing."""
    from othor.web.middleware import security_headers

    def policy(path: str) -> bytes:
        return dict(security_headers(path, "prod"))[b"referrer-policy"]

    for path in ("/sources", "/sources/x?panel=sheets", "/setup/sources/sheets"):
        assert policy(path) == b"strict-origin", path
    for path in ("/home", "/brief", "/login", "/l/token", "/"):
        assert policy(path) == b"same-origin", path
