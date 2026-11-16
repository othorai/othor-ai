# SPDX-License-Identifier: Apache-2.0
"""Shared setup fixtures: the sample company's upload files (generated once per session)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.sample_company.generate import SampleCompany, generate
from tests.fixtures.sample_company.uploads import write_uploads


@pytest.fixture(scope="session")
def sample() -> SampleCompany:
    return generate()


@pytest.fixture(scope="session")
def upload_files(tmp_path_factory, sample) -> dict[str, Path]:
    out = tmp_path_factory.mktemp("uploads")
    return {p.name: p for p in write_uploads(sample, out)}
