# SPDX-License-Identifier: Apache-2.0
"""Source credentials: encrypted with the instance key, bound to their source (docs/02 "Secrets").

`load` is called in the worker only, per job; nothing returns the plaintext to the web layer.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from othor.connectors.models import SourceSecret
from othor.core.crypto import InstanceKey
from othor.core.models import utcnow


def _aad(company_id: uuid.UUID, source_id: uuid.UUID) -> bytes:
    return f"source-secret:{company_id}:{source_id}".encode()


def store(
    s: Session,
    key: InstanceKey,
    company_id: uuid.UUID,
    source_id: uuid.UUID,
    secret: dict[str, Any],
) -> None:
    blob = key.encrypt(json.dumps(secret, sort_keys=True).encode(), aad=_aad(company_id, source_id))
    row = s.scalars(sa.select(SourceSecret).where(SourceSecret.source_id == source_id)).first()
    if row is None:
        s.add(SourceSecret(source_id=source_id, blob=blob))
    else:
        row.blob = blob
        row.rotated_at = utcnow()


def load(
    s: Session, key: InstanceKey, company_id: uuid.UUID, source_id: uuid.UUID
) -> dict[str, Any] | None:
    row = s.scalars(sa.select(SourceSecret).where(SourceSecret.source_id == source_id)).first()
    if row is None:
        return None
    data: dict[str, Any] = json.loads(key.decrypt(row.blob, aad=_aad(company_id, source_id)))
    return data


def delete(s: Session, source_id: uuid.UUID) -> bool:
    row = s.scalars(sa.select(SourceSecret).where(SourceSecret.source_id == source_id)).first()
    if row is None:
        return False
    s.delete(row)
    return True
