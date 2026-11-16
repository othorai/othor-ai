# SPDX-License-Identifier: Apache-2.0
"""Readers that misbehave, for the per-file limits (run in the child process by name)."""

from __future__ import annotations

import time
from typing import Any


def hang(raw: bytes, kind: str, name: str) -> Any:
    time.sleep(120)


def hog(raw: bytes, kind: str, name: str) -> Any:
    keep = bytearray(3 * 1024 * 1024 * 1024)  # 3 GB, over the 1 GB ceiling
    return len(keep)


def crash(raw: bytes, kind: str, name: str) -> Any:
    import os

    os._exit(9)
