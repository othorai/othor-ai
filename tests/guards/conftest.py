# SPDX-License-Identifier: Apache-2.0
"""Guards reuse the web app fixture for the HTTP-level checks."""

# the replay guard runs Indigo Thread on the sample sources, as the integration tests do
from tests.integration.run_world import sqlite_only  # noqa: F401
from tests.integration.test_run_sample import world  # noqa: F401
from tests.unit.ontology.conftest import sample  # noqa: F401
from tests.web.conftest import env  # noqa: F401
