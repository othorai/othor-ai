# SPDX-License-Identifier: Apache-2.0
"""The licence (M20.3, v0.20): signed by Othor AI, checked offline on the install. A valid key
names its features and limits; a changed byte, another signer or a bad format is invalid; after
expiry 14 days' grace, then expired. Nothing here touches the network."""

from __future__ import annotations

import datetime as dt
import socket

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from othor.core import licence

TODAY = dt.date(2026, 11, 16)


@pytest.fixture
def signer(monkeypatch):
    """A throwaway signing pair; its public key is the one the install trusts in the test."""
    priv = Ed25519PrivateKey.generate()
    monkeypatch.setattr(licence, "PUBLIC_KEYS", (licence.public_key_text(priv.public_key()),))
    return priv


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the licence check must never use the network")

    monkeypatch.setattr(socket, "create_connection", refuse)


def _key(priv, **over):
    payload = {
        "id": "lic_1",
        "customer": "Acme",
        "edition": "enterprise",
        "features": ["multi_company"],
        "limits": {"companies": 2},
        "issued": "2026-11-01",
        "expires": "2026-12-31",
        **over,
    }
    return licence.sign(priv, payload)


def test_a_valid_key_names_its_features_and_limits(signer):
    st = licence.check(_key(signer), TODAY)
    assert st.state == "valid" and st.customer == "Acme" and st.edition == "enterprise"
    assert st.allows("multi_company") and not st.allows("discover")
    assert st.limits == {"companies": 2} and st.days_left == 45


def test_a_changed_byte_another_signer_or_a_bad_format_is_invalid(signer):
    good = _key(signer)
    head, payload, sig = good.split(".")
    tampered = ".".join(
        [head, payload[:-2] + ("A" if payload[-2] != "A" else "B") + payload[-1], sig]
    )
    assert licence.check(tampered, TODAY).state == "invalid"
    other = Ed25519PrivateKey.generate()
    assert licence.check(_key(other), TODAY).state == "invalid"
    for bad in ("", "nonsense", "othor-licence-v1.x", "othor-licence-v2.a.b"):
        st = licence.check(bad, TODAY)
        assert st.state in ("invalid", "none") and not st.allows("multi_company")


def test_grace_then_expired(signer):
    key = _key(signer, expires="2026-11-10")
    assert licence.check(key, dt.date(2026, 11, 10)).state == "valid"
    g = licence.check(key, dt.date(2026, 11, 11))
    assert g.state == "grace" and g.allows("multi_company") and g.grace_left == 13
    assert licence.check(key, dt.date(2026, 11, 24)).state == "grace"
    e = licence.check(key, dt.date(2026, 11, 25))
    assert e.state == "expired" and not e.allows("multi_company")


def test_a_key_without_an_expiry_never_expires(signer):
    st = licence.check(_key(signer, expires=None), dt.date(2040, 1, 1))
    assert st.state == "valid" and st.days_left is None


def test_none_without_a_key(signer):
    assert licence.check(None, TODAY).state == "none"


def test_the_environment_text_beats_the_file(signer, tmp_path):
    from othor.core.config import Settings

    f = tmp_path / "licence.key"
    f.write_text(_key(signer, customer="From file"))
    s = Settings(_env_file=None, env="test", data_dir=str(tmp_path), licence="")  # type: ignore[call-arg]
    assert licence.read(s) is not None and "From file" in str(
        licence.check(licence.read(s), TODAY).customer
    )
    s2 = Settings(
        _env_file=None,
        env="test",
        data_dir=str(tmp_path),
        licence=_key(signer, customer="From env"),
    )  # type: ignore[call-arg]
    assert licence.check(licence.read(s2), TODAY).customer == "From env"
