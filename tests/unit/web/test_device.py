# SPDX-License-Identifier: Apache-2.0
"""The Account page names a session's device from its stored user agent (M15.8): a short phrase,
by a small pure function; no IP anywhere."""

from __future__ import annotations

import pytest

from othor.web.device import device

MAC_CHROME = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/157.0.0.0 Safari/537.36"
)


@pytest.mark.parametrize(
    ("ua", "want"),
    [
        (MAC_CHROME, "Chrome on macOS"),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
            "Safari on iPhone",
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0",
            "Edge on Windows",
        ),
        (
            "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
            "Firefox on Linux",
        ),
        (
            "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/157.0.0.0 Mobile Safari/537.36",
            "Chrome on Android",
        ),
        (
            "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like "
            "Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
            "Safari on iPad",
        ),
        ("curl/8.7.1", "Unknown device"),
        ("", "Unknown device"),
        (None, "Unknown device"),
    ],
)
def test_a_user_agent_reads_as_a_device(ua, want) -> None:
    assert device(ua) == want
