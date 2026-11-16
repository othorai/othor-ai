# SPDX-License-Identifier: Apache-2.0
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.sample_company.generate import SampleCompany, generate

EXPECTED_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "sample_company" / "expected.json"
)


@pytest.fixture(scope="session")
def expected() -> dict[str, Any]:
    return json.loads(EXPECTED_PATH.read_text())


@pytest.fixture(scope="session")
def company(expected: dict[str, Any]) -> SampleCompany:
    return generate(seed=expected["seed"], as_of=dt.date.fromisoformat(expected["as_of"]))
