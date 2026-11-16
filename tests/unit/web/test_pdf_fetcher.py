# SPDX-License-Identifier: Apache-2.0
"""The brief PDF fetches nothing from outside (15 Nov 2026, Dependabot: WeasyPrint SSRF and EPS
advisories): `data:` URIs and the bundled fonts and template files only; any http(s), ftp or other
file URL is refused, so a URL that ever reached the HTML could not be fetched."""

from __future__ import annotations

import pytest

from othor.web import brief_pdf


def test_bundled_files_and_data_uris_are_fetched():
    f = brief_pdf.LocalOnlyFetcher()
    font = next(brief_pdf.FONTS.glob("*.ttf")).as_uri()
    assert f.fetch(font).read()[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO")
    assert f.fetch("data:text/plain;base64,aGk=").read() == b"hi"


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "https://example.com/x.png",
        "ftp://example.com/x",
        "file:///etc/passwd",
        "file:///tmp/../etc/hosts",
    ],
)
def test_everything_else_is_refused(url):
    with pytest.raises(ValueError):
        brief_pdf.LocalOnlyFetcher().fetch(url)


def test_a_pdf_with_an_outside_image_renders_without_fetching_it(monkeypatch):
    import weasyprint

    html = '<p>x</p><img src="http://169.254.169.254/x.png"><img src="file:///etc/passwd">'
    pdf = weasyprint.HTML(
        string=html, base_url=str(brief_pdf.HERE), url_fetcher=brief_pdf.LocalOnlyFetcher()
    ).write_pdf()
    assert pdf[:5] == b"%PDF-"
