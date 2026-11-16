# SPDX-License-Identifier: Apache-2.0
"""What a run needs from its host (worker, CLI, tests). Never the model gateway."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from othor.core.config import Settings
from othor.core.crypto import InstanceKey
from othor.core.models import utcnow


@dataclass
class EngineContext:
    settings: Settings
    sf: sessionmaker[Session]
    key: InstanceKey
    clock: Callable[[], datetime] = utcnow
    renew: Callable[[], None] | None = None  # the job's lease, renewed after every step
    fault: Callable[[str], None] | None = None  # tests: called as each step starts

    @property
    def image_version(self) -> str:
        return self.settings.image_version
