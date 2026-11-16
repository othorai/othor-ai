# SPDX-License-Identifier: Apache-2.0
"""Search, phase 1 (docs/plans/SEO.md, M11b): what search engines may index.

Only a website plug-in's pages on the root host are indexable (M20.2: the othor.ai website is
the private `othor_site` plug-in; it names its pages through `Plugin.indexable`). Every other
response (a company host, the app and sign-in paths on any host, an instance without the site)
says `X-Robots-Tag: noindex, nofollow`, so a company's name is never findable through us.
"""

from __future__ import annotations

from othor.core import plugins

NOINDEX = b"noindex, nofollow"
# files that aren't pages: no header (images may show in image search; robots and the sitemap
# are read by crawlers, not indexed)
UNMARKED = ("/static/", "/favicon.ico", "/apple-touch-icon.png", "/robots.txt", "/sitemap.xml")
# the app's paths on the root host, for robots.txt (the X-Robots-Tag covers every one anyway)
DISALLOW = (
    "/login",
    "/signup",
    "/auth",
    "/setup",
    "/home",
    "/brief",
    "/sweep",
    "/ontology",
    "/sources",
    "/settings",
    "/links",
    "/l/",
    "/u/",
    "/first-run",
    "/hooks",
    "/healthz",
    "/_components",
)


def indexable_paths() -> list[str]:
    """The website's pages a plug-in offers for search, in its order (none without one)."""
    return plugins.indexable_paths()


def noindex(host: str, path: str, *, base_domain: str, public_site: bool) -> bool:
    if path.startswith(UNMARKED):
        return False
    if not public_site or host != base_domain:
        return True
    return path not in indexable_paths()


def robots_txt(*, on_root: bool, public_site: bool, sitemap_url: str) -> str:
    if not (on_root and public_site and indexable_paths()):
        return "User-agent: *\nDisallow: /\n"
    lines = ["User-agent: *", *(f"Disallow: {p}" for p in DISALLOW), "Allow: /", ""]
    return "\n".join(lines) + f"Sitemap: {sitemap_url}\n"


def sitemap_xml(base: str) -> str:
    from xml.sax.saxutils import escape

    urls = "".join(f"<url><loc>{escape(base + p)}</loc></url>" for p in indexable_paths())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}</urlset>\n"
    )
