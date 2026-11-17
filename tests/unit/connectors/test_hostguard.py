# SPDX-License-Identifier: Apache-2.0
"""The source-host guard (owner, 16 Nov 2026; othor.ai only): a sample service name or any host
that resolves to a non-public address is refused, at save and on every connection, and the
connection goes to the address that was checked. Off by default; allowed companies skip it."""

from __future__ import annotations

import socket
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa

from othor.connectors.sql import hostguard
from othor.connectors.sql.engine import SqlCreds, friendly_error, make_engine

PUBLIC = "93.184.216.34"


def _resolves(monkeypatch: pytest.MonkeyPatch, *addrs: str) -> None:
    def fake(host: str, port: Any, *a: Any, **kw: Any) -> list[Any]:
        if not addrs:
            raise socket.gaierror("no such name")
        out = []
        for x in addrs:
            fam = socket.AF_INET6 if ":" in x else socket.AF_INET
            out.append((fam, socket.SOCK_STREAM, 6, "", (x, port)))
        return out

    monkeypatch.setattr(hostguard.socket, "getaddrinfo", fake)


@pytest.fixture(autouse=True)
def _off_after() -> Any:
    yield
    hostguard.configure(SimpleNamespace())


def _on(allow: str = "") -> None:
    hostguard.configure(SimpleNamespace(source_host_guard=True, source_host_allow=allow))


@pytest.mark.parametrize("host", ["sales-dw", "erp-db", "ERP-DB", "erp-db."])
def test_sample_names_are_refused(host: str) -> None:
    assert hostguard.refusal(host, 5432) == ("sample", [])


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "db",
        "127.0.0.1",
        "10.0.3.7",
        "172.16.0.5",
        "192.168.1.10",
        "169.254.169.254",  # the cloud metadata address
        "100.64.0.1",  # shared (CGNAT)
        "0.0.0.0",  # noqa: S104 (an address we refuse, not one we bind)
        "::1",
        "fd00::1",
        "::ffff:10.0.0.1",
        "[::1]",
        "224.0.0.1",
    ],
)
def test_internal_addresses_are_refused(host: str) -> None:
    assert hostguard.refusal(host, 5432)[0] == "private"


def test_a_public_address_passes_and_is_returned() -> None:
    assert hostguard.refusal(PUBLIC, 5432) == (None, [PUBLIC])


def test_a_name_is_refused_if_any_address_is_private(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolves(monkeypatch, PUBLIC, "10.0.0.8")
    assert hostguard.refusal("db.example.com", 5432)[0] == "private"
    _resolves(monkeypatch, PUBLIC)
    assert hostguard.refusal("db.example.com", 5432) == (None, [PUBLIC])


def test_a_name_that_does_not_resolve_is_not_tried(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolves(monkeypatch)
    assert hostguard.refusal("nowhere.example.com", 5432) == (None, [])
    with pytest.raises(hostguard.Unresolved):
        hostguard.check("nowhere.example.com", 5432)


def test_off_by_default_and_the_allow_list_skips_it() -> None:
    hostguard.configure(SimpleNamespace())
    assert not hostguard.applies(slug="anyone")
    _on("indigo, indigo2,test")
    assert hostguard.applies(slug="trialco")
    assert not hostguard.applies(slug="indigo2") and not hostguard.applies(slug="INDIGO")
    assert hostguard.applies()  # an unknown company: guarded


def _creds(host: str, dialect: str = "postgresql", ssl: str = "require") -> SqlCreds:
    return SqlCreds(
        dialect,  # type: ignore[arg-type]
        host,
        5432 if dialect == "postgresql" else 3306,
        "erp",
        "reader",
        "pw",
        ssl,  # type: ignore[arg-type]
        company_id=uuid.uuid4(),
    )


class _Caught(Exception):
    pass


def _cparams(eng: sa.Engine) -> dict[str, Any]:
    """The parameters the driver would get, after the guard's listener ran (no server)."""
    got: dict[str, Any] = {}

    @sa.event.listens_for(eng, "do_connect")
    def _grab(_d: Any, _r: Any, _a: Any, cparams: dict[str, Any]) -> None:
        got.update(cparams)
        raise _Caught

    with pytest.raises(_Caught):
        eng.connect()
    return got


def test_every_connection_checks_again_and_pins_the_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _on()
    _resolves(monkeypatch, PUBLIC)
    p = _cparams(make_engine(_creds("db.example.com")))
    assert p["host"] == "db.example.com" and p["hostaddr"] == PUBLIC  # TLS keeps the name
    m = _cparams(make_engine(_creds("db.example.com", "mysql")))
    assert m["host"] == PUBLIC
    v = _cparams(make_engine(_creds("db.example.com", "mysql", ssl="verify-ca")))
    assert v["host"] == "db.example.com"  # verify-ca checks the name


def test_a_dns_change_after_saving_is_refused_at_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    _on()
    c = _creds("db.example.com")
    eng = make_engine(c)
    _resolves(monkeypatch, "10.1.2.3")  # the name now points inside
    with pytest.raises(hostguard.PrivateHost) as e:
        eng.connect()
    assert friendly_error(e.value, c) == hostguard.MESSAGE
    assert "10.1.2.3" not in friendly_error(e.value, c)


def test_the_sample_host_is_refused_at_connect_with_the_plain_line() -> None:
    _on()
    c = _creds("erp-db", "mysql")
    with pytest.raises(hostguard.PrivateHost) as e:
        make_engine(c).connect()
    assert friendly_error(e.value, c) == (
        "This address is on a private network. "
        "Othor AI connects only to databases reachable from the internet."
    )


def test_off_the_engine_has_no_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    hostguard.configure(SimpleNamespace())
    p = _cparams(make_engine(_creds("sales-dw")))
    assert p["host"] == "sales-dw" and "hostaddr" not in p
