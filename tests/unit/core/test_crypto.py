# SPDX-License-Identifier: Apache-2.0
import os
import stat
import uuid
from pathlib import Path

import pytest

from othor.core.crypto import DecryptError, InstanceKey, KeyFileError


def test_round_trip(tmp_path: Path) -> None:
    key = InstanceKey.load_or_create(tmp_path / "instance.key", allow_create=True)
    blob = key.encrypt(b"postgres-password", aad=b"source:1")
    assert b"postgres-password" not in blob
    assert key.decrypt(blob, aad=b"source:1") == b"postgres-password"


def test_nonce_is_fresh(tmp_path: Path) -> None:
    key = InstanceKey.load_or_create(tmp_path / "k", allow_create=True)
    assert key.encrypt(b"x", aad=b"a") != key.encrypt(b"x", aad=b"a")


def test_tampered_or_wrong_aad_fails(tmp_path: Path) -> None:
    key = InstanceKey.load_or_create(tmp_path / "k", allow_create=True)
    blob = bytearray(key.encrypt(b"secret", aad=b"source:1"))
    with pytest.raises(DecryptError):
        key.decrypt(bytes(blob), aad=b"source:2")
    blob[-1] ^= 1
    with pytest.raises(DecryptError):
        key.decrypt(bytes(blob), aad=b"source:1")


def test_other_key_cannot_decrypt(tmp_path: Path) -> None:
    k1 = InstanceKey.load_or_create(tmp_path / "k1", allow_create=True)
    k2 = InstanceKey.load_or_create(tmp_path / "k2", allow_create=True)
    with pytest.raises(DecryptError):
        k2.decrypt(k1.encrypt(b"s", aad=b""), aad=b"")


def test_key_file_created_0600_and_reloaded(tmp_path: Path) -> None:
    path = tmp_path / "instance.key"
    k1 = InstanceKey.load_or_create(path, allow_create=True)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    k2 = InstanceKey.load_or_create(path, allow_create=False)
    assert k2.decrypt(k1.encrypt(b"s", aad=b"a"), aad=b"a") == b"s"


def test_missing_key_refused_when_creation_not_allowed(tmp_path: Path) -> None:
    with pytest.raises(KeyFileError, match="missing"):
        InstanceKey.load_or_create(tmp_path / "nope.key", allow_create=False)


def test_readable_by_others_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "k"
    InstanceKey.load_or_create(path, allow_create=True)
    os.chmod(path, 0o644)
    with pytest.raises(KeyFileError, match="permissions"):
        InstanceKey.load_or_create(path, allow_create=False)


def test_subkeys_stable_per_company_and_purpose(tmp_path: Path) -> None:
    key = InstanceKey.load_or_create(tmp_path / "k", allow_create=True)
    a, b = uuid.UUID(int=1), uuid.UUID(int=2)
    assert key.subkey(a, "link") == key.subkey(a, "link")
    assert key.subkey(a, "link") != key.subkey(b, "link")
    assert key.subkey(a, "link") != key.subkey(a, "unsubscribe")
    assert len(key.subkey(a, "link")) == 32
