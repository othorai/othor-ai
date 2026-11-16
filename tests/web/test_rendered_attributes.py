# SPDX-License-Identifier: Apache-2.0
"""Attributes built from data render as real attributes, escaped once (UI foundation regression:
a joined string of escaped values once rendered quotes as &#34;, so browsers ignored them)."""

from __future__ import annotations

from othor.web.templating import templates
from tests.web.conftest import Env, signup


def _render(src: str, **ctx) -> str:
    return templates.env.from_string('{% from "components/ui.html" import el %}' + src).render(
        **ctx
    )


def test_el_attrs_are_escaped_once():
    out = _render(
        '{% call el("a", "link", attrs={"href": "/s?a=1&b=" ~ x, "title": t, "data-x": none,'
        ' "disabled": flag}) %}go{% endcall %}',
        x='"><script>',
        t="Ravira & Sons",
        flag=True,
    )
    assert out == (
        '<a class="link" href="/s?a=1&amp;b=&#34;&gt;&lt;script&gt;" '
        'title="Ravira &amp; Sons" disabled>go</a>'
    )
    assert _render('{{ el("input", "input", attrs={"value": v, "name": "q"}) }}', v="a, b") == (
        '<input class="input" value="a, b" name="q">'
    )


def test_no_page_renders_a_broken_attribute(env: Env):
    signup(env)
    h = env.host("indigothread")
    pages = [
        "/setup/upload",
        "/setup/business",
        "/settings",
        "/sources",
        "/sources?panel=add",
        "/sources?panel=connect&kind=postgres",
        "/setup/sources?panel=connect&kind=postgres",
        "/setup/sources?panel=connect&kind=mysql",
    ]
    for path in pages:
        html = env.client.get(h + path).text
        assert "=&#34;" not in html, path
    for path in ("/_components", "/_components/9a", "/_components/9b", "/_components/states"):
        html = env.client.get("http://othor.localhost" + path).text
        assert "=&#34;" not in html, path
