# SPDX-License-Identifier: Apache-2.0
"""Error pages (M11a): no request ends on the framework's default "Not Found" or
"Internal Server Error"; 404s read the same for missing and another company's objects."""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from tests.web.conftest import BASE, ROOT, Env, signup

FRAMEWORK = ('{"detail"', "Internal Server Error", "Traceback", "Not Found</")


def _clean(text: str) -> None:
    for bad in FRAMEWORK:
        assert bad not in text, bad


def _boom_router() -> APIRouter:
    r = APIRouter()

    @r.get("/boom")
    def boom() -> None:
        raise RuntimeError("SELECT secret_value FROM invoices WHERE amount = 4200")

    @r.post("/boom")
    def boom_post() -> None:
        raise RuntimeError("secret_value 4200")

    @r.get("/hooks/boom")
    def boom_json() -> None:
        raise RuntimeError("secret_value")

    return r


@pytest.fixture
def boom(env: Env) -> TestClient:
    app = env.app
    for route in _boom_router().routes:
        app.router.routes.insert(0, route)  # type: ignore[attr-defined]
    return TestClient(app, base_url=ROOT, raise_server_exceptions=False)  # type: ignore[arg-type]


def test_unmatched_path_signed_out_is_bare_404(env: Env) -> None:
    for url in (f"{ROOT}/no/such/page", f"{ROOT}/brief/x/y/z/w", f"{ROOT}/static-nope/a/b"):
        r = env.client.get(url)
        assert r.status_code == 404, url
        assert "404 · not found" in r.text and "Go to sign in" in r.text
        assert "Othor AI" in r.text
        _clean(r.text)


def test_unknown_company_host(env: Env) -> None:
    r = env.client.get(f"http://nobody.{BASE}/some/where")
    assert r.status_code == 404
    assert f"There’s no company at nobody.{BASE}" in r.text


def test_signed_in_404s_render_in_the_app_shell(env: Env) -> None:
    signup(env)
    h = env.host("indigothread")
    r = env.client.get(f"{h}/no/such/page")
    assert r.status_code == 404
    assert 'aria-label="Main"' in r.text  # the app shell's sidebar
    assert "There’s nothing at this address" in r.text
    r = env.client.get(f"{h}/sweep?run=9999")
    assert r.status_code == 404
    assert "There’s no run #9999" in r.text and ">Sweep<" in r.text
    _clean(r.text)


def test_bad_ids_are_404_never_422(env: Env) -> None:
    signup(env)
    h = env.host("indigothread")
    for path in (
        "/sweep?run=abc",
        f"/sources/{'not-a-uuid'}",
        "/sweep/run/abc/log.json",
        "/brief/not-a-uuid",
    ):
        r = env.client.get(f"{h}{path}")
        assert r.status_code == 404, (path, r.status_code)
        if not path.endswith(".json"):
            _clean(r.text)


def test_missing_and_other_companys_run_read_the_same(env: Env) -> None:
    signup(env, email="uk@indigothread.example", company="Indigo Thread Co.", slug="indigothread")
    h = env.host("indigothread")
    missing = env.client.get(f"{h}/brief/{uuid.uuid4()}")
    other = env.client.get(f"{h}/brief/{uuid.uuid4()}")
    assert missing.status_code == other.status_code == 404

    def shape(t: str) -> str:
        return re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}", "<id>", re.sub(r"csrf[^>]*>", "", t))

    assert shape(missing.text) == shape(other.text)


def test_500_has_a_reference_and_no_trace(env: Env, boom: TestClient) -> None:
    signup(env)
    boom.cookies = env.client.cookies
    r = boom.get(f"{env.host('indigothread')}/boom", headers={"X-Request-ID": "req-test-0001"})
    assert r.status_code == 500
    assert "Something went wrong on our side. Nothing you did was lost." in r.text
    assert "Reference req-test-0001" in r.text
    assert r.headers["x-request-id"] == "req-test-0001"
    assert ">Reload<" in r.text and ">Home<" in r.text
    for leak in ("secret_value", "4200", "RuntimeError", "Traceback", "SELECT"):
        assert leak not in r.text
    ev = env.events("system.error")
    assert ev and ev[-1].after["reference"] == "req-test-0001"
    assert ev[-1].after["error"] == "RuntimeError"
    assert "secret_value" not in str(ev[-1].after)


def test_500_on_the_root_host_logs_only(env: Env, boom: TestClient) -> None:
    r = boom.get(f"{ROOT}/boom")
    assert r.status_code == 500 and "Reference " in r.text
    assert env.events("system.error") == []


def test_htmx_error_gives_the_toast(env: Env, boom: TestClient) -> None:
    signup(env)
    boom.cookies = env.client.cookies
    r = boom.get(f"{env.host('indigothread')}/boom", headers={"HX-Request": "true"})
    assert r.status_code == 500
    assert r.headers["hx-retarget"] == "#toast-dock"
    assert r.headers["x-othor-toast"] == "1"
    assert "Couldn’t load this" in r.text and "Retry" in r.text
    assert "<html" not in r.text


def test_json_route_error_shape(env: Env, boom: TestClient) -> None:
    r = boom.get(f"{ROOT}/hooks/boom", headers={"X-Request-ID": "req-json-0001"})
    assert r.status_code == 500
    assert r.json() == {"error": "server error", "reference": "req-json-0001"}


def test_csrf_failure_is_a_page_or_a_toast(env: Env) -> None:
    signup(env)
    h = env.host("indigothread")
    r = env.client.post(f"{h}/settings", data={"field": "currency", "value": "INR"})
    assert r.status_code == 403
    assert "This form expired. Reload the page and try again." in r.text
    _clean(r.text)
    r = env.client.post(
        f"{h}/settings", data={"field": "currency", "value": "INR"}, headers={"HX-Request": "true"}
    )
    assert r.status_code == 403 and r.headers.get("x-othor-toast") == "1"


def test_every_get_route_with_junk_ids_has_no_framework_body(env: Env) -> None:
    signup(env)
    h = env.host("indigothread")
    from fastapi.routing import APIRoute

    for route in env.app.router.routes:  # type: ignore[attr-defined]
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        if route.path.startswith("/_components"):
            continue
        path = re.sub(r"\{[^}]+\}", "junk-0", route.path)
        r = env.client.get(f"{h}{path}", follow_redirects=False)
        if route.path == "/healthz/worker":  # 503 by design: no worker beats in tests (M11b)
            assert r.status_code == 503 and r.json() == {"status": "never"}
            continue
        assert r.status_code < 500, (route.path, r.status_code)
        if r.status_code >= 400 and "json" not in r.headers.get("content-type", ""):
            _clean(r.text)


def test_security_headers_and_timing(env: Env) -> None:
    r = env.client.get(f"{ROOT}/login")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "unsafe-eval" not in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["server-timing"].startswith("app;dur=")
    assert re.match(r"^[0-9a-f]{12}$", r.headers["x-request-id"])
