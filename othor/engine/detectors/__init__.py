# SPDX-License-Identifier: Apache-2.0
"""The detectors (docs/04 §3). Importing this package registers every one in `base.REGISTRY`,
in this order (the order of Sweep's lists)."""

from othor.engine.detectors import (  # noqa: F401  (registration)
    changepoint,
    concentration,
    cross_source,
    distributions,
    forecast_breach,
    missing_records,
    net_hiding_gross,
    pairs,
    rising_variance,
)
from othor.engine.detectors.base import REGISTRY

__all__ = ["REGISTRY"]
