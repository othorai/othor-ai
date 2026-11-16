# SPDX-License-Identifier: Apache-2.0
"""The licence in the running app (M20.3): several companies on one install need it; without it,
or past its company limit, no new company is set up, while existing ones work as before.
Settings › Licence takes a pasted key; the worker records each change of state."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import sqlalchemy as sa

from othor.core import licence
from othor.core.models import Event, PlatformEvent
from othor.core.tenancy import system_scope
from othor.jobs import licence_check
from tests.conftest import _TEST_SIGNER
from tests.web.conftest import ROOT, make_env, signup


def _key(**over: object) -> str:
    payload = {
        "id": "lic_w",
        "customer": "Acme",
        "edition": "trial",
        "features": ["multi_company"],
        "limits": {},
        "issued": "2026-11-01",
        "expires": None,
        **over,
    }
    return licence.sign(_TEST_SIGNER[0], payload)  # type: ignore[arg-type]


def test_no_licence_no_new_company_but_existing_ones_work(tmp_path: Path) -> None:
    with make_env(tmp_path, licence=_key()) as env:
        signup(env)
    # the same install, its licence gone: sign-in works, a new company is refused
    with make_env(tmp_path, licence="") as env:
        r = env.client.get(ROOT + "/signup")
        assert r.status_code == 403 and "No new companies here" in r.text
        assert "licence doesn" in r.text
        assert env.client.get(env.host("indigothread") + "/login").status_code == 200


def test_the_company_limit(tmp_path: Path) -> None:
    with make_env(tmp_path, licence=_key(limits={"companies": 1})) as env:
        signup(env)
        r = env.client.get(ROOT + "/signup")
        assert r.status_code == 403 and "company limit is reached" in r.text


def test_paste_a_key_in_settings(tmp_path: Path) -> None:
    lic = tmp_path / "lic.key"
    lic.write_text(_key() + "\n")
    with make_env(tmp_path, licence="", licence_file=str(lic)) as env:
        signup(env)
        h = env.host("indigothread")
        page = env.client.get(h + "/settings").text
        assert 'id="licence"' in page and "Acme · Enterprise Edition · trial" in page
        tok = env.csrf(h + "/settings")
        bad = env.client.post(h + "/settings/licence", data={"csrf": tok, "key": "nonsense"})
        assert bad.status_code == 400 and "isn" in bad.text
        new = _key(customer="Acme Renewed", expires="2027-12-31")
        ok = env.client.post(h + "/settings/licence", data={"csrf": tok, "key": new})
        assert ok.status_code == 200 and "Acme Renewed" in ok.text and new not in ok.text
        assert lic.read_text().strip() == new
        svc = env.app.state.services  # type: ignore[attr-defined]
        with svc.sf() as s, system_scope("test: the licence event"):
            ev = s.scalars(sa.select(Event).where(Event.action == "licence.updated")).all()
            assert ev and ev[-1].after["customer"] == "Acme Renewed"
            assert new not in str(ev[-1].after)


def test_a_server_set_key_cant_be_replaced_in_settings(tmp_path: Path) -> None:
    with make_env(tmp_path, licence=_key()) as env:
        signup(env)
        h = env.host("indigothread")
        assert "Set by the server (OTHOR_LICENCE)." in env.client.get(h + "/settings").text
        tok = env.csrf(h + "/settings")
        r = env.client.post(h + "/settings/licence", data={"csrf": tok, "key": _key()})
        assert r.status_code == 400 and "Set by the server" in r.text


def test_the_worker_records_each_change_of_state(tmp_path: Path) -> None:
    with make_env(tmp_path, licence=_key(expires="2026-11-10")) as env:
        svc = env.app.state.services  # type: ignore[attr-defined]
        at = dt.datetime(2026, 11, 5, tzinfo=dt.UTC)
        assert licence_check.check(svc.sf, svc.settings, at) == "valid"
        assert licence_check.check(svc.sf, svc.settings, at) is None  # unchanged
        assert (
            licence_check.check(svc.sf, svc.settings, dt.datetime(2026, 11, 12, tzinfo=dt.UTC))
            == "grace"
        )
        assert (
            licence_check.check(svc.sf, svc.settings, dt.datetime(2026, 11, 30, tzinfo=dt.UTC))
            == "expired"
        )
        with svc.sf() as s:
            got = [
                e.after["state"]
                for e in s.scalars(
                    sa.select(PlatformEvent)
                    .where(PlatformEvent.action == "licence.state")
                    .order_by(PlatformEvent.at)
                )
            ]
        assert got == ["valid", "grace", "expired"]
