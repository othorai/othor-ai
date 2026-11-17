# SPDX-License-Identifier: Apache-2.0
"""Each upload is read in a child process with a time limit and a memory ceiling (M11b §6b).

A file built to hang a reader or eat memory takes down only its child: the worker marks it
"Couldn't be read" and carries on, and the job ends normally, so it's never retried in a loop.
The ceiling is `RLIMIT_AS`, enforced on Linux (the server); elsewhere the time limit still holds.
"""

from __future__ import annotations

import importlib
import logging
import multiprocessing as mp
from multiprocessing.connection import Connection
from typing import Any

from othor.setup.parse import Parsed

log = logging.getLogger("othor.uploads")

TIMEOUT_S = 60.0
MEMORY_BYTES = 1024 * 1024 * 1024
READER = "othor.setup.parse:parse_file"


def _unreadable() -> Parsed:
    return Parsed("unreadable", None, {"text": "Couldn’t be read"}, [])


def _child(conn: Connection, reader: str, raw: bytes, kind: str, name: str, memory: int) -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    except (ImportError, ValueError, OSError):  # not enforced on this platform
        pass
    try:
        module, fn = reader.split(":")
        result: Any = getattr(importlib.import_module(module), fn)(raw, kind, name)
    except MemoryError:
        result = None
    except Exception:
        result = None
    conn.send(result)
    conn.close()


def read_limited(
    raw: bytes,
    kind: str,
    name: str,
    *,
    timeout_s: float = TIMEOUT_S,
    memory_bytes: int = MEMORY_BYTES,
    reader: str = READER,
) -> Parsed:
    """`parse.parse_file` in a child process; over the time or memory limit, or a child that
    dies, is "Couldn't be read". `reader` is "module:function" (tests pass misbehaving ones)."""
    ctx = mp.get_context("spawn")  # never fork the worker (its scheduler thread, DB pools)
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_child, args=(child, reader, raw, kind, name, memory_bytes), daemon=True
    )
    proc.start()
    child.close()
    try:
        if parent.poll(timeout_s):
            got = parent.recv()
        else:
            log.warning("upload read timed out", extra={"kind": kind})
            got = None
    except (EOFError, OSError):  # the child died (killed for memory, or crashed)
        got = None
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(5)
        parent.close()
    if not isinstance(got, Parsed):
        # why, without anything from the file: a negative exit code is the signal that ended it
        # (-11 a crash, -9 killed); 0 means the reader raised (17 Nov 2026: a crash was silent)
        log.warning("upload not read", extra={"kind": kind, "exitcode": proc.exitcode})
    return got if isinstance(got, Parsed) else _unreadable()
