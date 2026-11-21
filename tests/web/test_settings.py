# SPDX-License-Identifier: Apache-2.0
"""Settings (7a): each change writes an event with before/after; Undo writes another."""

from tests.web.conftest import Env, signup


def test_change_writes_event_and_undo_restores(env: Env) -> None:
    signup(env)
    c, host = env.client, env.host("indigothread")
    page = c.get(f"{host}/settings")
    assert page.status_code == 200
    for text in (
        "Fiscal year starts",
        "Currency",
        "Time zone",
        "Nightly run 2:00 AM IST · brief 6:00 AM IST",
        "Full brief as a page · from brief@othor.localhost · recipients set per brief",
    ):
        assert text in page.text, text
    tok = env.csrf(f"{host}/settings")
    r = c.post(
        f"{host}/settings",
        data={"field": "timezone", "value": "Asia/Dubai"},
        headers={"X-CSRF-Token": tok},
    )
    assert r.status_code == 200 and "Time zone saved" in r.text and "Undo" in r.text
    ev = env.events("settings.changed")[-1]
    assert ev.before == {"timezone": "Asia/Kolkata"} and ev.after == {"timezone": "Asia/Dubai"}
    r = c.post(f"{host}/settings/undo/{ev.id}", headers={"X-CSRF-Token": tok})
    assert r.headers.get("HX-Refresh") == "true"
    undone = env.events("settings.undone")[-1]
    assert undone.after == {"timezone": "Asia/Kolkata"}
    assert 'value="Asia/Kolkata" selected' in c.get(f"{host}/settings").text


def test_settings_needs_csrf_and_valid_values(env: Env) -> None:
    signup(env)
    c, host = env.client, env.host("indigothread")
    assert c.post(f"{host}/settings", data={"field": "timezone", "value": "UTC"}).status_code == 403
    tok = env.csrf(f"{host}/settings")
    r = c.post(
        f"{host}/settings",
        data={"field": "timezone", "value": "Mars/Base"},
        headers={"X-CSRF-Token": tok},
    )
    assert r.status_code == 400
    r = c.post(
        f"{host}/settings", data={"field": "slug", "value": "x"}, headers={"X-CSRF-Token": tok}
    )
    assert r.status_code == 400


def test_email_delivery_toggle(env: Env) -> None:
    signup(env)
    c, host = env.client, env.host("indigothread")
    tok = env.csrf(f"{host}/settings")
    c.post(
        f"{host}/settings",
        data={"field": "email_delivery", "value": "off"},
        headers={"X-CSRF-Token": tok},
    )
    assert env.events("settings.changed")[-1].after == {"email_delivery": False}


def test_a_switch_saves_without_a_toast(env: Env) -> None:
    """#17: a Delivery switch shows its own state; it saves in place with no "saved · Undo" toast
    (flipping it back undoes it). The change is still an event. The selects keep their toast
    (test_change_writes_event_and_undo_restores)."""
    signup(env)
    c, host = env.client, env.host("indigothread")
    tok = env.csrf(f"{host}/settings")
    for field in ("email_delivery", "chart_smooth"):
        r = c.post(
            f"{host}/settings",
            data={"field": field, "value": "off"},
            headers={"X-CSRF-Token": tok, "HX-Request": "true"},
        )
        assert r.status_code == 204 and r.text == "", field
    assert [e.after for e in env.events("settings.changed")][-2:] == [
        {"email_delivery": False},
        {"chart_smooth": False},
    ]
