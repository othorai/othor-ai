# SPDX-License-Identifier: Apache-2.0
"""One Contact us form (owner, 17 Nov 2026): the website's /contact and the app's side panel,
the topic set by the link that opened it. Stored in access_request (only othor.core.auth touches
it) and emailed to support. Hosted only: without the website, links stay mailto."""

from __future__ import annotations

from typing import Any

# (value, what the owner sees in the select)
TOPICS: list[tuple[str, str]] = [
    ("sales", "The Enterprise Edition or pricing"),
    ("trial", "Continuing after the free trial"),
    ("self-hosting", "Running Othor AI on our own servers"),
    ("ai", "Raising the monthly AI allowance"),
    ("other", "Something else"),
]
# the email's subject line, per topic
SUBJECTS = {
    "sales": "Sales",
    "trial": "Continue after the trial",
    "self-hosting": "Self-hosting",
    "ai": "AI allowance",
    "other": "Contact",
}
# a topic where a message is optional: the topic says it all
MESSAGE_OPTIONAL = {"trial", "ai"}

# how many people work there, as a range (Request access, owner 10 Nov 2026)
TEAM_SIZES = [
    ("under-50", "Fewer than 50"),
    ("50-199", "50 to 199"),
    ("200-999", "200 to 999"),
    ("1000-plus", "1,000 or more"),
]


def topic(value: str | None) -> str:
    """A known topic, or "other"."""
    return value if value in dict(TOPICS) else "other"


def errors(
    name: str, email: str, company: str, topic_: str, message: str, team_size: str = ""
) -> dict[str, str]:
    out: dict[str, str] = {}
    if not name:
        out["name"] = "Enter your name."
    if "@" not in email or "." not in email.split("@")[-1]:
        out["email"] = "Enter your work email."
    if not company:
        out["company"] = "Enter the company name."
    if topic_ not in dict(TOPICS):
        out["topic"] = "Choose what it’s about."
    if team_size and team_size not in dict(TEAM_SIZES):
        out["team_size"] = "Choose how many people work there."
    if not message and topic_ not in MESSAGE_OPTIONAL:
        out["message"] = "Tell us what you need."
    return out


def available(settings: Any) -> bool:
    """The form exists: the public website is on and a website plug-in is installed."""
    from othor.core import plugins

    return bool(settings.public_site and plugins.root_page() is not None)
