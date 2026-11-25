# SPDX-License-Identifier: Apache-2.0
"""The three kinds of work a company can give its own model (Any model, 0.37, owner 24 Nov 2026).

Every prompt folder belongs to exactly one kind (a guard test fails otherwise). Settings › AI Model
sets one model for all three, or a model per kind; the gateway picks the provider by the kind of
the prompt it is asked to run.
"""

from __future__ import annotations

KINDS: tuple[str, ...] = ("understanding", "reports", "answering")
LABELS: dict[str, str] = {
    "understanding": "Understanding your business",
    "reports": "Writing your reports",
    "answering": "Answering you",
}
NOTES: dict[str, str] = {
    "understanding": "Reading your sources and files, drafting your Business Model",
    "reports": "The Morning Brief's words, Big Picture, Discover, Look back",
    "answering": "Ask Othor AI, Home answers, chat on proposals",
}

_PROMPTS: dict[str, tuple[str, ...]] = {
    "understanding": (
        "draft_metrics",
        "business_draft",
        "scan_table_label",
        "read_upload",
        "metric_areas",
        "metric_connect",
        "metric_where",
        "metric_edit",
        "level_drafts",
        "goal_from_targets",
        "goal_parse",
        "suggest_pairs",
        "tab_fix",
        "ontology_change",
        "document_changes",
        "decision_suggest",
        "outside_suggest",
        "size_numbers",
    ),
    "reports": (
        "brief_rewrite",
        "brief_draft",
        "brief_edit",
        "brief_suggest",
        "big_picture",
        "discover_summary",
        "forecasts",
        "look_back_plan",
        "look_back_suggest",
        "look_back_write",
        "plain_facts",
    ),
    "answering": (
        "ask_route",
        "ask_answer",
        "othor_chat",
        "home_answer",
        "proposal_chat",
        "conflict_chat",
        "look_back_drill",
    ),
}
# "Check this model" runs its own small prompt for whichever kind it checks
CHECK_PROMPT = "model_check"
PROMPT_KIND: dict[str, str] = {p: k for k, ps in _PROMPTS.items() for p in ps}


def kind_of(prompt_id: str) -> str:
    """The kind of work a prompt does; one not listed counts as Understanding your business."""
    return PROMPT_KIND.get(prompt_id, "understanding")


def prompts_of(kind: str) -> tuple[str, ...]:
    return _PROMPTS[kind]
