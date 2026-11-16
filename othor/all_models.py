# SPDX-License-Identifier: Apache-2.0
"""Import every model module so `Base.metadata` is complete (Alembic, tests). Product code
imports the module it needs; only migrations and tests import this."""

from othor.ai import models as ai_models
from othor.briefs import models as brief_models
from othor.connectors import models as connector_models
from othor.core import models as core_models
from othor.core import ratelimit as ratelimit_models
from othor.engine import models as engine_models
from othor.jobs import models as job_models
from othor.mail import models as mail_models
from othor.ontology import models as ontology_models
from othor.setup import models as setup_models

Base = core_models.Base

__all__ = [
    "Base",
    "ai_models",
    "brief_models",
    "connector_models",
    "core_models",
    "engine_models",
    "job_models",
    "mail_models",
    "ontology_models",
    "ratelimit_models",
    "setup_models",
]
