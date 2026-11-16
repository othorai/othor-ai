# SPDX-License-Identifier: Apache-2.0
"""Skip markers for tests that need a private plug-in (M20.5). The public repository holds the
open core only; a test of the othor.ai website (request access, /ips, the legal pages) runs here
and skips there, with the reason."""

from __future__ import annotations

import importlib.util

import pytest

needs_site = pytest.mark.skipif(
    importlib.util.find_spec("othor_site") is None,
    reason="tests the othor.ai website plug-in, which isn't in the open core",
)
HAS_SITE = importlib.util.find_spec("othor_site") is not None
