# SPDX-License-Identifier: Apache-2.0
"""Integration tests read the compose sample sources; `sample_sources_seeded` (tests/conftest.py)
fills them before any test reads them, whatever order the tests run in."""

from tests.integration.run_world import sqlite_only  # noqa: F401  (runs: one metadata DB)
from tests.unit.ontology.conftest import sample  # noqa: F401  (the generated company)
