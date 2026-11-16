# SPDX-License-Identifier: Apache-2.0
import datetime as dt
from typing import Any

from tests.fixtures.sample_company.generate import SampleCompany, generate


def test_same_seed_same_bytes(company: SampleCompany, expected: dict[str, Any]) -> None:
    again = generate(seed=expected["seed"], as_of=dt.date.fromisoformat(expected["as_of"]))
    assert again.data_hash() == company.data_hash()


def test_hash_is_pinned(company: SampleCompany, expected: dict[str, Any]) -> None:
    """Changing the generator on purpose means bumping generator_version and the pinned hash."""
    assert company.generator_version == expected["generator_version"]
    assert company.data_hash() == expected["data_hash"]


def test_other_seed_differs(company: SampleCompany) -> None:
    other = generate(seed=7, as_of=company.as_of)
    assert other.data_hash() != company.data_hash()


def test_as_of_cuts_the_data(company: SampleCompany) -> None:
    earlier = generate(seed=company.seed, as_of=dt.date(2026, 11, 2))
    inv = earlier.erp["invoices"]
    assert inv["invoice_date"].max() < dt.date(2026, 11, 2)
    assert company.erp["invoices"]["invoice_date"].max() < company.as_of
