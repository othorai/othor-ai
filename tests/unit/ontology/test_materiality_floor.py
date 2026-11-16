# SPDX-License-Identifier: Apache-2.0
"""M15.6 · the floor the owner sets: a `materiality` record compiles into the spec's detector
floor; without one an INR company's spec is exactly as before (₹5 L) and a USD company's floor is
$5,000."""

from __future__ import annotations

import pytest

from othor.ontology.compiler.build import DETECTOR_DEFAULTS, compile_records
from othor.ontology.types import validate_body


def _spec(records: dict, currency: str = "INR") -> dict:
    company = {
        "fiscal_year_start_month": 4,
        "timezone": "Asia/Kolkata",
        "currency": currency,
        "number_format": "indian",
        "week_starts": "monday",
    }
    from othor.ontology.compiler.catalog import Catalog

    return compile_records(records, Catalog(sources={}), company, 1)


def test_no_record_is_todays_spec():
    assert _spec({})["detectors"] == DETECTOR_DEFAULTS
    assert DETECTOR_DEFAULTS["materiality_inr"] == "500000"


def test_the_owners_floor_and_the_usd_default():
    body = validate_body("materiality", {"amount": "200000", "source": "owner"})
    assert _spec({("materiality", "company"): body})["detectors"]["materiality_inr"] == "200000"
    assert _spec({}, "USD")["detectors"]["materiality_inr"] == "5000"
    assert DETECTOR_DEFAULTS["materiality_inr"] == "500000"  # the default is never changed


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "1e9"])
def test_a_floor_is_a_positive_amount(bad):
    with pytest.raises(ValueError):
        validate_body("materiality", {"amount": bad})
