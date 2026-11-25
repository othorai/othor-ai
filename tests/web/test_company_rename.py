# SPDX-License-Identifier: Apache-2.0
"""Rename the company (owner, 24 Nov 2026; pending-list 75): Settings › Company's name row, in
place; super admin and admins only; an event `company.renamed` with before and after, said
plainly in the Audit log; the web address (slug) never changes; every page and the next email
read the new name."""

from __future__ import annotations

import sqlalchemy as sa

from othor.audit import words
from othor.core.models import Company, Membership
from othor.core.tenancy import system_scope
from tests.web.conftest import Env, signup

HX = {"HX-Request": "true"}


def _post(env: Env, name: str) -> object:
    host = env.host("indigothread")
    tok = env.csrf(f"{host}/settings")
    return env.client.post(
        f"{host}/settings/name", data={"company_name": name}, headers={"X-CSRF-Token": tok, **HX}
    )


def _company(env: Env) -> Company:
    sf = env.app.state.services.sf  # type: ignore[attr-defined]
    with sf() as s, system_scope("test"):
        c: Company = s.scalars(sa.select(Company)).one()
        s.expunge(c)
        return c


def test_the_row_shows_the_name_and_opens_in_place(env: Env) -> None:
    signup(env)
    c, host = env.client, env.host("indigothread")
    page = c.get(f"{host}/settings").text
    assert 'id="name-row"' in page and "Company name" in page
    assert "Indigo Thread Co." in page and "indigothread.othor.localhost" in page
    edit = c.get(f"{host}/settings/name?edit=1", headers=HX)
    assert edit.status_code == 200
    assert 'name="company_name"' in edit.text and 'value="Indigo Thread Co."' in edit.text
    assert "Save" in edit.text and "Cancel" in edit.text
    back = c.get(f"{host}/settings/name", headers=HX)
    assert 'name="company_name"' not in back.text and "Indigo Thread Co." in back.text


def test_rename_saves_in_place_writes_the_event_and_keeps_the_slug(env: Env) -> None:
    signup(env)
    r = _post(env, "  Indigo   Thread  Company ")
    assert r.status_code == 200  # type: ignore[attr-defined]
    text = r.text  # type: ignore[attr-defined]
    assert 'id="name-row"' in text and "Indigo Thread Company" in text
    assert "Company name saved" in text  # the in-place confirmation
    # the header and the menu pick it up at once, without a reload
    assert 'id="crumb-company" hx-swap-oob="innerHTML"' in text
    assert 'id="nav-company" hx-swap-oob="innerHTML"' in text
    co = _company(env)
    assert co.name == "Indigo Thread Company" and co.slug == "indigothread"
    ev = env.events("company.renamed")[-1]
    assert ev.before == {"name": "Indigo Thread Co."}
    assert ev.after == {"name": "Indigo Thread Company"}
    # every page reads the new name; the address is the same
    page = env.client.get(f"{env.host('indigothread')}/home").text
    assert '<span id="crumb-company">Indigo Thread Company</span>' in page
    assert "Indigo Thread Co.<" not in page


def test_the_same_name_writes_nothing(env: Env) -> None:
    signup(env)
    r = _post(env, "Indigo Thread Co.")
    assert r.status_code == 200 and "saved" not in r.text  # type: ignore[attr-defined]
    assert env.events("company.renamed") == []


def test_an_empty_short_or_long_name_shows_the_error_in_place(env: Env) -> None:
    signup(env)
    for name, said in (
        ("   ", "Enter the company name."),
        ("X", "Use at least 2 characters."),
        ("x" * 81, "Use 80 characters or fewer."),
    ):
        r = _post(env, name)
        # 200: htmx swaps only a 2xx, so the row comes back with the error in it
        assert r.status_code == 200, name  # type: ignore[attr-defined]
        assert said in r.text and 'aria-invalid="true"' in r.text  # type: ignore[attr-defined]
        assert 'name="company_name"' in r.text  # type: ignore[attr-defined]
    assert _company(env).name == "Indigo Thread Co."
    assert env.events("company.renamed") == []


def test_only_the_super_admin_and_admins_may_rename(env: Env) -> None:
    signup(env)
    sf = env.app.state.services.sf  # type: ignore[attr-defined]
    with sf() as s, system_scope("test"):
        s.execute(sa.update(Membership).values(role="viewer"))
        s.commit()
    host = env.host("indigothread")
    page = env.client.get(f"{host}/settings").text
    assert 'id="name-row"' in page and "Indigo Thread Co." in page
    assert 'hx-get="/settings/name?edit=1"' not in page  # no Rename button for them
    assert env.client.get(f"{host}/settings/name?edit=1", headers=HX).status_code == 403
    r = _post(env, "Someone Else Ltd")
    assert r.status_code == 403  # type: ignore[attr-defined]
    assert "Only the super admin or an admin can rename the company." in r.text  # type: ignore[attr-defined]
    assert _company(env).name == "Indigo Thread Co."


def test_an_admin_may_rename(env: Env) -> None:
    signup(env)
    sf = env.app.state.services.sf  # type: ignore[attr-defined]
    with sf() as s, system_scope("test"):
        s.execute(sa.update(Membership).values(role="admin"))
        s.commit()
    assert _post(env, "Indigo Thread").status_code == 200  # type: ignore[attr-defined]
    assert _company(env).name == "Indigo Thread"


def test_rename_needs_csrf(env: Env) -> None:
    signup(env)
    host = env.host("indigothread")
    r = env.client.post(f"{host}/settings/name", data={"company_name": "X Co"}, headers=HX)
    assert r.status_code == 403


def test_the_audit_log_says_it_plainly(env: Env) -> None:
    assert (
        words.sentence(
            "company.renamed", before={"name": "Indgo Thread"}, after={"name": "Indigo Thread"}
        )
        == "Renamed the company: Indgo Thread to Indigo Thread"
    )
    assert words.sentence("company.renamed") == "Renamed the company"
    assert words.kind_of("company.renamed") == "settings"
    signup(env)
    _post(env, "Indigo Thread Company")
    page = env.client.get(f"{env.host('indigothread')}/audit?days=all").text
    assert "Renamed the company: Indigo Thread Co. to Indigo Thread Company" in page


def test_the_next_email_carries_the_new_name(env: Env) -> None:
    """Emails are written when sent, from the company row: the next one has the new name."""
    signup(env)
    _post(env, "Indigo Thread Company")
    env.clock.tick(minutes=2)
    host = env.host("indigothread")
    r = env.post(f"{host}/login", {"email": "uk@indigothread.example"})
    assert r.status_code == 303  # type: ignore[attr-defined]
    msg = env.relay.outbox[-1]
    assert msg.sender.startswith("Indigo Thread Company · Othor AI")
    assert "Indigo Thread Company" in msg.html
