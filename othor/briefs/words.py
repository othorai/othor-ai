# SPDX-License-Identifier: Apache-2.0
"""The words a stored brief issue shows (the page, the read-only link, the email): the model's
rewrite when it passed the numbers guard, else the template's. Model-free, so delivery and the
pages never import the AI module to read an issue."""

from __future__ import annotations

from typing import Any

FIELDS = ("title", "line", "situation")


def shown(issue_body: dict[str, Any], prose: dict[str, Any] | None) -> dict[str, str]:
    if prose and prose.get("source") == "model":
        return {k: str(prose["model"].get(k) or issue_body.get(k, "")) for k in FIELDS}
    return {k: str(issue_body.get(k, "")) for k in FIELDS}
