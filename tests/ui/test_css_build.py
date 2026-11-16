# SPDX-License-Identifier: Apache-2.0
"""The stylesheet build (docs/plans/UI-foundation.md, decision 2): the pinned Tailwind v4
standalone CLI, verified by checksum; content-hashed output found through a manifest; the same
bytes and name on every build; every class a template names exists in the build."""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from tests.ui.css_build import ROOT, build_css_module, selectors

bc = build_css_module()


@pytest.fixture(scope="module")
def built():
    try:
        path = bc.build()
    except (OSError, SystemExit) as e:  # no network and no cached CLI
        pytest.skip(f"Tailwind CLI unavailable: {e}")
    return path


def test_pinned_and_verified():
    assert bc.VERSION == "v4.3.3"
    assert set(bc.SHA256) == {"linux-arm64", "linux-x64", "macos-arm64", "macos-x64"}
    assert all(re.fullmatch(r"[0-9a-f]{64}", v) for v in bc.SHA256.values())


def test_a_tampered_download_fails_closed(tmp_path, monkeypatch):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"not the tailwind binary"

    monkeypatch.setattr(bc, "CACHE", tmp_path)
    monkeypatch.setattr(bc.urllib.request, "urlopen", lambda *a, **k: Resp())
    with pytest.raises(SystemExit, match="checksum mismatch"):
        bc.cli("linux-x64")
    assert not list(tmp_path.glob("tailwindcss-*"))


def test_hashed_output_and_manifest(built):
    data = built.read_bytes()
    assert built.name == f"app.{hashlib.sha256(data).hexdigest()[:8]}.css"
    assert json.loads(bc.MANIFEST.read_text()) == {"app.css": f"css/{built.name}"}
    assert [p.name for p in bc.OUT.glob("app.*.css") if p.name != "app.dev.css"] == [built.name]


def test_same_inputs_same_bytes(built):
    first = built.read_bytes()
    again = bc.build()
    assert again.name == built.name and again.read_bytes() == first


def test_every_template_class_is_built(built):
    css = selectors(built.read_text())
    jinja = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
    missing = []
    for p in sorted((ROOT / "othor" / "web" / "templates").rglob("*.html")):
        text = p.read_text()
        for m in re.finditer(r'\bclass="([^"]*)"', text):
            for tok in jinja.sub(" ", m.group(1)).split():
                if tok.startswith("js-") or tok.endswith("-") or tok in css:
                    continue
                line = text.count("\n", 0, m.start()) + 1
                missing.append(f"{p.relative_to(ROOT)}:{line}: {tok}")
    assert missing == [], "classes the build didn't produce:\n" + "\n".join(missing)


def test_pages_link_the_built_file(built):
    from othor.web.templating import asset

    assert asset("app.css") == f"/static/css/{built.name}"


def test_ontology_rows_are_the_tables_size() -> None:
    """#52: the business, the patterns and the names read at the Numbers table's size."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    css = (root / "othor" / "web" / "styles" / "components.css").read_text()
    assert ".ont-section .ont-row { padding: 16px 0; font-size: var(--text-13);" not in css
