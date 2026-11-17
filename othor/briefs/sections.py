# SPDX-License-Identifier: Apache-2.0
"""Brief sections from plug-ins (Advanced Intelligence Phase 0 §6): a part of the brief beside
the four ranked items, never among them ("Outside · 2" from External Signals).

`build(spec, version, run_date, findings, watches, points, context, outputs_doc)` is a pure
function of the run's outputs, so the run and replay make the same section. It returns
{"title", "note"?, "items": [{"text", "line"?, "state"?}]} or None. Every number in its words
comes from computed values (non-negotiable 1). The core keeps at most `max_items` items, drops
anything else, and stores the section in the issue under "sections" (only when there is one, so
an issue without keeps its bytes). It renders in the web issue, the PDF and the emails.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from othor.engine.store import Point

log = logging.getLogger("othor.briefs")

Build = Callable[..., dict[str, Any] | None]
ITEM_KEYS = ("text", "line", "state")
STATES = ("measured", "assessed", "not_determinable", "needs_confirmation")


@dataclass(frozen=True)
class BriefSection:
    key: str
    build: Build
    max_items: int = 2


SECTIONS: dict[str, BriefSection] = {}


def register_brief_section(key: str, build: Build, max_items: int = 2) -> BriefSection:
    if key in SECTIONS and SECTIONS[key].build is not build:
        raise ValueError(f"brief section {key!r} is already taken")
    if max_items < 1:
        raise ValueError("a section shows at least one item")
    SECTIONS[key] = BriefSection(key, build, max_items)
    return SECTIONS[key]


def _clean(key: str, got: Any, max_items: int) -> dict[str, Any] | None:
    if not isinstance(got, dict) or not isinstance(got.get("title"), str):
        return None
    items = []
    for i in got.get("items") or []:
        if not isinstance(i, dict) or not isinstance(i.get("text"), str) or not i["text"]:
            continue
        item = {k: str(i[k]) for k in ITEM_KEYS if i.get(k)}
        if item.get("state") not in (None, *STATES):
            item.pop("state")
        items.append(item)
    if not items:
        return None
    out: dict[str, Any] = {"key": key, "title": got["title"], "items": items[:max_items]}
    if isinstance(got.get("note"), str) and got["note"]:
        out["note"] = got["note"]
    return out


def build_all(
    spec: dict[str, Any],
    version: dict[str, Any],
    run_date: dt.date,
    findings: list[dict[str, Any]],
    watches: list[dict[str, Any]],
    points: list[Point],
    context: dict[str, Any],
    outputs_doc: dict[str, Any],
) -> list[dict[str, Any]]:
    """Every registered section for one brief version, in key order. A section that fails is
    logged and left out; the brief goes out without it."""
    out = []
    for key, sec in sorted(SECTIONS.items()):
        try:
            got = sec.build(
                spec,
                version,
                run_date,
                [dict(f) for f in findings],
                list(watches),
                points,
                context,
                outputs_doc,
            )
        except Exception:
            log.exception("brief section %s failed; left out", key)
            continue
        cleaned = _clean(key, got, sec.max_items)
        if cleaned:
            out.append(cleaned)
    return out
