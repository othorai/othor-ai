# SPDX-License-Identifier: Apache-2.0
"""Instance key and secret encryption (AES-256-GCM).

Envelope: b"v1" | nonce (12 bytes) | ciphertext+tag. The AAD binds a blob to its owner
(e.g. b"source:<id>") so a ciphertext can't be moved to another record.
Per-company subkeys (signed links, unsubscribe) are derived with HKDF-SHA256.
"""

from __future__ import annotations

import base64
import os
import stat
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_BYTES = 32
NONCE_BYTES = 12
VERSION = b"v1"


class KeyFileError(RuntimeError):
    pass


class DecryptError(RuntimeError):
    pass


class InstanceKey:
    def __init__(self, raw: bytes) -> None:
        if len(raw) != KEY_BYTES:
            raise KeyFileError("instance key must be 32 bytes")
        self._raw = raw
        self._aead = AESGCM(raw)

    def __repr__(self) -> str:
        return "InstanceKey(***)"

    @classmethod
    def load_or_create(cls, path: Path, *, allow_create: bool) -> InstanceKey:
        if not path.exists():
            if not allow_create:
                raise KeyFileError(f"instance key file is missing: {path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = AESGCM.generate_key(bit_length=256)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(base64.urlsafe_b64encode(raw))
            return cls(raw)
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise KeyFileError(f"instance key file permissions are too open ({oct(mode)}): {path}")
        try:
            raw = base64.urlsafe_b64decode(path.read_bytes().strip())
        except ValueError as e:
            raise KeyFileError("instance key file is not valid base64") from e
        return cls(raw)

    def encrypt(self, plaintext: bytes, *, aad: bytes) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return VERSION + nonce + self._aead.encrypt(nonce, plaintext, aad)

    def decrypt(self, blob: bytes, *, aad: bytes) -> bytes:
        if blob[:2] != VERSION or len(blob) < 2 + NONCE_BYTES + 16:
            raise DecryptError("unknown envelope")
        nonce, ct = blob[2 : 2 + NONCE_BYTES], blob[2 + NONCE_BYTES :]
        try:
            return self._aead.decrypt(nonce, ct, aad)
        except InvalidTag as e:
            raise DecryptError("ciphertext failed authentication") from e

    def subkey(self, company_id: uuid.UUID, purpose: str) -> bytes:
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=company_id.bytes,
            info=b"othor/" + purpose.encode(),
        )
        return hkdf.derive(self._raw)
