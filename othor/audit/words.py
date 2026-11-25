# SPDX-License-Identifier: Apache-2.0
"""The Audit log's words (0.35): every event action → its kind and a plain sentence.

A sentence may carry `{obj}`: the object, linked to its screen, goes there; a sentence without it
gets " · {obj}" added when the object has a screen. A few sentences are worked out from the
event's before and after (a run's number, a source's kind, an invited address). An action this
registry doesn't name reads as a readable form of its name, and a guard test fails on any
`action="…"` in the code that has no sentence here."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

# kinds, in the filter's order (key, label)
KINDS: list[tuple[str, str]] = [
    ("sources", "Sources"),
    ("model", "Business Model"),
    ("briefs", "Briefs"),
    ("sweep", "Sweep"),
    ("runs", "Runs"),
    ("people", "People & sign-in"),
    ("ai", "Othor AI"),
    ("settings", "Settings"),
    ("data", "Your data"),
]
KIND_LABEL = dict(KINDS)

# the kind of an action this registry doesn't name, by its prefix
PREFIX_KIND: dict[str, str] = {
    "source": "sources",
    "scan": "sources",
    "sheet_file": "sources",
    "sheet_files": "sources",
    "sheet_tab": "sources",
    "sheet_tabs": "sources",
    "table_use": "sources",
    "schema_change": "sources",
    "connector": "sources",
    "upload": "sources",
    "series": "sources",
    "ontology": "model",
    "setup": "model",
    "business": "model",
    "onboarding": "model",
    "question": "model",
    "proposal": "model",
    "spec": "model",
    "home": "model",
    "brief": "briefs",
    "suggested_brief": "briefs",
    "recipient": "briefs",
    "mail": "briefs",
    "big_picture": "briefs",
    "plain_facts": "briefs",
    "look_back": "briefs",
    "finding": "sweep",
    "watch": "sweep",
    "discover": "sweep",
    "summary": "sweep",
    "forecasts": "sweep",
    "outside": "sweep",
    "run": "runs",
    "snapshot": "runs",
    "auth": "people",
    "user": "people",
    "member": "people",
    "invitation": "people",
    "mcp_key": "people",
    "mcp_client": "people",
    "othor_chat": "ai",
    "tool": "ai",
    "model_call": "ai",
    "settings": "settings",
    "licence": "settings",
    "company": "settings",
    "contact": "settings",
    "demo": "settings",
    "system": "settings",
}

# action → (kind, sentence)
WORDS: dict[str, tuple[str, str]] = {
    # --- Sources -------------------------------------------------------------------------------
    "source.created": ("sources", "Added a source"),
    "source.connected": ("sources", "Connected a source"),
    "source.reconnected": ("sources", "Connected a source again"),
    "source.disconnected": ("sources", "Disconnected a source"),
    "source.credentials_changed": ("sources", "Changed how Othor AI signs in to a source"),
    "source.paused": ("sources", "Paused a source"),
    "source.resumed": ("sources", "Started reading a source again"),
    "source.renamed": ("sources", "Renamed a source"),
    "source.read": ("sources", "Read a source"),
    "source.read_now": ("sources", "Asked to read a source now"),
    "source.stale": ("sources", "A source couldn't be read tonight"),
    "source.tested": ("sources", "Checked the connection to a source"),
    "source.test_failed": ("sources", "A source's connection check failed"),
    "source.refused": ("sources", "Refused a source that could write"),
    "source.refused_host": ("sources", "Refused a source's address"),
    "source.metric_added": ("sources", "Added a number from a source"),
    "source.metric_staged": ("sources", "Set a number aside to add from a source"),
    "source.metric_unstaged": ("sources", "Took back a number set aside from a source"),
    "scan.requested": ("sources", "Asked Othor AI to look through a source"),
    "scan.started": ("sources", "Started looking through a source"),
    "scan.finished": ("sources", "Finished looking through a source"),
    "scan.schema_skipped": ("sources", "Left a part of a source out"),
    "scan.schema_unskipped": ("sources", "Brought a part of a source back in"),
    "schema_change.acknowledged": ("sources", "Noted a change in a source's tables"),
    "sheet_file.access_removed": ("sources", "Lost access to a sheet"),
    "sheet_file.removed": ("sources", "Removed a sheet"),
    "sheet_files.chosen": ("sources", "Chose the sheets to read"),
    "sheet_tab.confirmed": ("sources", "Confirmed a sheet tab's layout"),
    "sheet_tab.needs_check": ("sources", "A sheet tab's layout needs a check"),
    "sheet_tab.shape_changed": ("sources", "Changed how a sheet tab is read"),
    "sheet_tabs.chosen": ("sources", "Chose the sheet tabs to read"),
    "table_use.changed": ("sources", "Changed what a table is used for"),
    "table_use.kept_excluded": ("sources", "Kept a table left out"),
    "table_use.unchanged": ("sources", "Kept what a table is used for"),
    "table_use.undone": ("sources", "Undid a change to what a table is used for"),
    "table_use.without_personal": ("sources", "Used a table without its personal columns"),
    "connector.requested": ("sources", "Asked for a new kind of source"),
    "upload.added": ("sources", "Uploaded a file"),
    "upload.read": ("sources", "Read an uploaded file"),
    "upload.removed": ("sources", "Removed an uploaded file"),
    "series.converted": ("sources", "Changed how a number's history is kept"),
    "series.reinstated": ("sources", "Brought back a number's history"),
    "series.reread_requested": ("sources", "Asked to read a number's history again"),
    "series.versioned": ("sources", "Kept a new version of a number's history"),
    # --- Business Model ------------------------------------------------------------------------
    "business.saved": ("model", "Saved how the business works"),
    "onboarding.step_saved": ("model", "Moved on in setup"),
    "ontology.confirmed": ("model", "Confirmed the Business Model"),
    "ontology.decision_confirmed": ("model", "Confirmed a choice Othor AI made"),
    "ontology.draft_changed": ("model", "Changed the Business Model draft"),
    "ontology.floor_changed": ("model", "Changed when a change is worth telling"),
    "ontology.level_drafts": ("model", "Othor AI found Business Model items in your files"),
    "ontology.restored": ("model", "Restored an earlier Business Model"),
    "ontology.undone": ("model", "Undid a Business Model change"),
    "ontology.version_created": ("model", "Changed the Business Model"),
    "ontology.chat": ("model", "Talked about the Business Model with Othor AI"),
    "spec.compiled": ("model", "Got the Business Model ready for tonight's run"),
    "spec.rejected": ("model", "A Business Model change couldn't be used"),
    "question.raised": ("model", "Othor AI asked a question"),
    "question.answered": ("model", "Answered a question"),
    "question.decided_by_ai": ("model", "Answered a question for you"),
    "question.decide_declined": ("model", "Othor AI couldn't answer a question for you"),
    "question.draft_discarded": ("model", "Set aside a drafted answer"),
    "question.drafted": ("model", "Drafted an answer to a question"),
    "question.skipped": ("model", "Skipped a question for now"),
    "question.suggestions_ranked": ("model", "Othor AI suggested answers to a question"),
    "question.undone": ("model", "Undid an answer"),
    "proposal.created": ("model", "Othor AI suggested a change"),
    "proposal.extended": ("model", "Othor AI added to a suggestion"),
    "proposal.accepted": ("model", "Accepted a suggestion"),
    "proposal.declined": ("model", "Declined a suggestion"),
    "proposal.undone": ("model", "Undid an accepted suggestion"),
    "proposal.chat": ("model", "Talked about a suggestion with Othor AI"),
    "proposal.reopened": ("model", "Othor AI suggested a change again"),
    "proposal.reworded": ("model", "Othor AI reworded a suggestion"),
    "home.notice_dismissed": ("model", "Closed a note on Home"),
    "setup.chat_applied": ("model", "Applied a change from chat"),
    "setup.chat_turn": ("model", "Talked about a number with Othor AI"),
    "setup.conflict_settled": ("model", "Settled where a number comes from"),
    "setup.decision_reopened": ("model", "Opened a setup choice again"),
    "setup.draft_accepted": ("model", "Accepted a drafted change"),
    "setup.draft_confirmed": ("model", "Confirmed a number"),
    "setup.draft_rejected": ("model", "Turned down a drafted change"),
    "setup.draft_skipped": ("model", "Skipped a number"),
    "setup.draft_unconfirmed": ("model", "Took back a confirmed number"),
    "setup.drafted": ("model", "Othor AI drafted your numbers"),
    "setup.drafted_from_source": ("model", "Othor AI drafted numbers from a source"),
    "setup.drafts_brought_back": ("model", "Brought back skipped numbers"),
    "setup.metric_saved": ("model", "Saved a number"),
    "setup.metrics_confirmed": ("model", "Confirmed the numbers"),
    "setup.redraft_requested": ("model", "Asked Othor AI to draft the numbers again"),
    "setup.redrafted": ("model", "Othor AI drafted the numbers again"),
    "setup.redrafted_for_source": ("model", "Othor AI drafted a number again for a source"),
    "setup.redrafted_with_document": ("model", "Othor AI drafted the numbers again from a file"),
    "setup.tab_fix_applied": ("model", "Applied a fix to a sheet tab"),
    "setup.values_computed": ("model", "Worked out the numbers' first values"),
    # --- Briefs --------------------------------------------------------------------------------
    "brief.created": ("briefs", "Made the {obj}"),
    "brief.changed": ("briefs", "Changed the {obj}"),
    "brief.removed": ("briefs", "Removed the {obj}"),
    "brief.delivered": ("briefs", "Sent the {obj}"),
    "brief.not_delivered": ("briefs", "Didn't send the {obj}"),
    "brief.issue_made_at_creation": ("briefs", "Made the first issue of the {obj}"),
    "brief.link_opened": ("briefs", "Someone opened the {obj} from its link"),
    "brief.link_sent": ("briefs", "Sent a link to the {obj}"),
    "brief.pdf": ("briefs", "Downloaded the {obj} as a PDF"),
    "brief.prose": ("briefs", "Worded the {obj}"),
    "brief.recipient_added": ("briefs", "Added someone to the {obj}"),
    "brief.recipient_removed": ("briefs", "Took someone off the {obj}"),
    "brief.update_kept": ("briefs", "Kept an update to the {obj}"),
    "brief.send_now_requested": ("briefs", "Asked to send the {obj} now"),
    "brief.update_send_requested": ("briefs", "Asked to send an update to the {obj}"),
    "suggested_brief.created": ("briefs", "Othor AI suggested a brief"),
    "suggested_brief.dismissed": ("briefs", "Set aside a suggested brief"),
    "recipient.bounced": ("briefs", "A brief email couldn't be delivered"),
    "recipient.resubscribed": ("briefs", "Someone asked to get a brief again"),
    "recipient.unsubscribed": ("briefs", "Someone stopped getting a brief"),
    "big_picture.made": ("briefs", "Made a Big Picture report"),
    "big_picture.emailed": ("briefs", "Emailed a Big Picture report"),
    "plain_facts.built": ("briefs", "Made the Weekly Digest"),
    "plain_facts.written": ("briefs", "Othor AI wrote the Weekly Digest"),
    "plain_facts.emailed": ("briefs", "Emailed the Weekly Digest"),
    "plain_facts.pdf": ("briefs", "Downloaded the Weekly Digest as a PDF"),
    "look_back.applied": ("briefs", "Applied a Look back"),
    "look_back.asked": ("briefs", "Asked for a Look back"),
    "look_back.changed": ("briefs", "Changed a Look back"),
    "look_back.deleted": ("briefs", "Deleted a Look back"),
    "look_back.emailed": ("briefs", "Emailed a Look back"),
    "look_back.made": ("briefs", "Made a Look back"),
    "look_back.planned": ("briefs", "Othor AI planned a Look back"),
    "look_back.read": ("briefs", "Read a Look back"),
    "look_back.read_again": ("briefs", "Read a Look back again"),
    "look_back.regenerated": ("briefs", "Made a Look back again"),
    "look_back.suggested": ("briefs", "Othor AI suggested a Look back"),
    "look_back.suggestion_dismissed": ("briefs", "Set aside a suggested Look back"),
    "look_back.suggestion_made": ("briefs", "Made a suggested Look back"),
    "look_back.unreadable": ("briefs", "A Look back couldn't be read"),
    "look_back.no_source": ("briefs", "A Look back had no source to read"),
    "look_back.too_many": ("briefs", "A Look back asked for too much at once"),
    "look_back.unreachable": ("briefs", "A Look back couldn't reach its source"),
    "mail.company_closed": ("data", "Emailed that the company was closed"),
    "mail.export_ready": ("data", "Emailed that a copy of your data is ready"),
    "mail.plain_facts": ("briefs", "Emailed the Weekly Digest"),
    "mail.purge_reminder": ("data", "Emailed a reminder before your data is deleted"),
    "mail.trial_reminder": ("settings", "Emailed a reminder about the trial"),
    "mail.run_failed": ("runs", "Emailed that a nightly run didn't finish"),
    "mail.run_late": ("runs", "Emailed that a nightly run is late"),
    "mail.source_stale": ("sources", "Emailed that a source couldn't be read"),
    "mail.ontology_waiting": ("model", "Emailed that numbers are waiting for you"),
    "mail.digest": ("briefs", "Emailed a digest"),
    # --- Sweep ---------------------------------------------------------------------------------
    "finding.muted": ("sweep", "Muted a finding"),
    "finding.unmuted": ("sweep", "Unmuted a finding"),
    "finding.snoozed": ("sweep", "Snoozed a finding"),
    "finding.unsnoozed": ("sweep", "Woke a snoozed finding"),
    "watch.started": ("sweep", "Started watching something"),
    "watch.stopped": ("sweep", "Stopped watching something"),
    "forecasts.written": ("sweep", "Wrote the forecasts"),
    "outside.assessed": ("sweep", "Checked outside news for your business"),
    "outside.check_requested": ("sweep", "Asked for an outside check"),
    "outside.turned_on": ("sweep", "Turned on outside checks"),
    "outside.turned_off": ("sweep", "Turned off outside checks"),
    # External Signals 2.1 (25 Nov 2026): the sentence names the series (SPECIAL)
    "outside.added": ("sweep", "Added an outside number to the test"),
    "outside.not_now": ("sweep", "Set aside a suggested outside number"),
    "outside.removed": ("sweep", "Took an outside number out of the test"),
    "outside.brought_back": ("sweep", "Brought back a suggested outside number"),
    "outside.suggested": ("sweep", "Othor AI suggested outside numbers to test"),
    # External Signals 2.0 (25 Nov 2026): an instance event (the operator's CLI), no company's
    "outside.bundle_imported": ("sweep", "Loaded outside numbers from an offline bundle"),
    "discover.dismissed": ("sweep", "Set aside a Discover group"),
    "discover.found": ("sweep", "Discover found a group worth a look"),
    "discover.refresh_refused": ("sweep", "Couldn't refresh a Discover group"),
    "discover.refresh_started": ("sweep", "Started refreshing a Discover group"),
    "discover.refresh_joined": ("sweep", "Joined a Discover refresh already running"),
    "discover.removed": ("sweep", "Removed a Discover group"),
    "discover.restored": ("sweep", "Brought back a Discover group"),
    "discover.summary_written": ("sweep", "Othor AI summed up a Discover group"),
    "discover.switched_on": ("sweep", "Switched on a Discover group"),
    "discover.turned_off": ("sweep", "Turned off a Discover group"),
    "discover.undone": ("sweep", "Undid a Discover change"),
    "discover.view": ("sweep", "Changed how a Discover group is shown"),
    "summary.hidden": ("sweep", "Hid a number on the Summary"),
    "summary.shown": ("sweep", "Showed a number on the Summary"),
    "summary.lead_changed": ("sweep", "Changed the Summary's lead number"),
    "summary.reordered": ("sweep", "Reordered the Summary"),
    "summary.view_changed": ("sweep", "Changed how the Summary is shown"),
    # --- Runs ----------------------------------------------------------------------------------
    "run.started": ("runs", "Run {obj} started"),
    "run.finished": ("runs", "Run {obj} finished"),
    "run.step_failed": ("runs", "A step of run {obj} didn't finish"),
    "run.replayed": ("runs", "Ran {obj} again to check it gives the same numbers"),
    "run.again_requested": ("runs", "Asked to run the numbers again now"),
    "run.again_refused": ("runs", "Couldn't run the numbers again now"),
    "run.rerun_requested": ("runs", "Asked to run a day again"),
    "run.retried": ("runs", "Tried run {obj} again"),
    "snapshot.backfilled": ("runs", "Filled in a number's past values"),
    # --- People & sign-in ----------------------------------------------------------------------
    "auth.code_sent": ("people", "Asked for a sign-in code"),
    "auth.code_failed": ("people", "Entered a wrong sign-in code"),
    "auth.code_locked": ("people", "Sign-in locked after too many wrong codes"),
    "auth.signed_in": ("people", "Signed in"),
    "auth.signed_out": ("people", "Signed out"),
    "auth.signed_out_everywhere": ("people", "Signed out on every device"),
    "auth.not_a_member": ("people", "Someone who isn't a member tried to sign in"),
    "auth.rate_limited": ("people", "Too many sign-in tries; asked to wait"),
    "user.signup": ("people", "Signed up"),
    "user.renamed": ("people", "Changed their name"),
    "member.invited": ("people", "Invited someone"),
    "member.invite_resent": ("people", "Sent an invitation again"),
    "member.invite_revoked": ("people", "Took back an invitation"),
    "member.joined": ("people", "Joined the company"),
    "member.removed": ("people", "Removed someone"),
    "member.suggestion_dismissed": ("people", "Set aside a suggested person to invite"),
    "invitation.created": ("people", "Invited a company to Othor AI"),
    "invitation.expired_on_use": ("people", "Used an invitation that had run out"),
    "invitation.revoked": ("people", "Took back an invitation"),
    "invitation.used": ("people", "Used an invitation"),
    "mcp_key.created": ("people", "Made a key for an AI app"),
    "mcp_key.revoked": ("people", "Took back an AI app's key"),
    "mcp_client.allowed": ("people", "Allowed an AI app in"),
    "mcp_client.registered": ("people", "An AI app asked to connect"),
    # --- Othor AI ------------------------------------------------------------------------------
    "othor_chat.applied": ("ai", "Applied answers from chat"),
    "othor_chat.asked": ("ai", "Asked Othor AI"),
    "othor_chat.drafted": ("ai", "Othor AI drafted a change in chat"),
    "othor_chat.reply": ("ai", "Othor AI replied in chat"),
    "tool.called": ("ai", "Looked something up in chat"),
    "model_call": ("ai", "Othor AI wrote"),
    # --- Settings ------------------------------------------------------------------------------
    "settings.changed": ("settings", "Changed the settings"),
    "settings.undone": ("settings", "Undid a change to the settings"),
    "company.created": ("settings", "Made the company"),
    "company.ai_cap_changed": ("settings", "Changed this month's AI allowance"),
    "company.trial_changed": ("settings", "Changed the trial"),
    "company.logo_set": ("settings", "Changed the company logo"),
    "company.logo_removed": ("settings", "Removed the company logo"),
    "company.renamed": ("settings", "Renamed the company"),
    "model.chosen": ("settings", "Chose the model Othor AI writes with"),
    "model.reset": ("settings", "Put Othor AI back on its usual model"),
    "model.checked": ("settings", "Checked a model for Othor AI"),
    "company.tab_icon_set": ("settings", "Changed the browser tab icon"),
    "licence.state": ("settings", "The licence changed state"),
    "licence.updated": ("settings", "Updated the licence"),
    "contact.sent": ("settings", "Wrote to Othor AI"),
    "demo.installed": ("settings", "Set up the sample company"),
    "system.error": ("settings", "Something went wrong on a page"),
    # --- Your data -----------------------------------------------------------------------------
    "company.export_requested": ("data", "Asked for a copy of your data"),
    "company.exported": ("data", "Made a copy of your data"),
    "company.export_downloaded": ("data", "Downloaded a copy of your data"),
    "company.close_code_sent": ("data", "Asked for a code to close the company"),
    "company.closed": ("data", "Closed the company"),
    "company.reopened": ("data", "Opened the company again"),
    "company.purged": ("data", "Deleted the company's data"),
}

# families of actions made with an f-string: prefix → (kind, sentence)
FAMILIES: dict[str, tuple[str, str]] = {
    "setup.chat_": ("model", "Changed a number in chat"),
    "setup.discovery_": ("model", "Chose whether to keep a number Othor AI found"),
    "discover.refresh_": ("sweep", "Refreshed a Discover group"),
    "mail.": ("briefs", "Sent an email"),
}

APPLIED_DRAFT = "Applied Othor AI's draft: "

# Othor AI's own drafting, started by a person: shown as "Othor AI (asked by …)" even when the
# row names only the person (rows written before 0.35 stay as they are)
AI_MADE = frozenset(
    {
        "setup.drafted",
        "setup.redrafted",
        "setup.redrafted_for_source",
        "setup.redrafted_with_document",
        "setup.drafted_from_source",
        "setup.values_computed",
        "ontology.level_drafts",
        "othor_chat.drafted",
        "othor_chat.reply",
        "question.suggestions_ranked",
        "suggested_brief.created",
        "look_back.planned",
        "look_back.suggested",
        "plain_facts.written",
        "discover.summary_written",
        "outside.suggested",
    }
)

# shown under People & sign-in only: a code request is followed by its sign-in (failed codes
# and lockouts stay everywhere)
QUIET = frozenset({"auth.code_sent"})

Sentence = Callable[[Mapping[str, Any], Mapping[str, Any], str], str | None]


def _plural(n: Any, word: str) -> str:
    try:
        k = int(n)
    except (TypeError, ValueError):
        return word + "s"
    return f"{k:,} {word}{'' if k == 1 else 's'}"


_SOURCE_KIND = {
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "sheets": "Google Sheets",
    "google_sheets": "Google Sheets",
}


def _source_created(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
    kind = _SOURCE_KIND.get(str(a.get("kind") or "").lower())
    return f"Added {kind}" if kind else None


def _run(word: str) -> Sentence:
    def say(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
        if str(a.get("kind") or "nightly") == "nightly":
            return f"Nightly run {{obj}} {word}"
        return None

    return say


def _delivered(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
    if a.get("status") and a.get("status") != "sent":
        return "Couldn't send the {obj} to one person"
    return "Sent the {obj} to one person" if a.get("recipient") else None


def _recipient(word: str) -> Sentence:
    def say(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
        email = a.get("email") or b.get("email")
        return (
            f"{word} {email} {'to' if word == 'Added' else 'from'} the {{obj}}" if email else None
        )

    return say


def _invited(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
    email = a.get("email")
    if not email:
        return None
    role = a.get("role")
    return f"Invited {email}" + (
        f" as {'super admin' if role == 'owner' else role}" if role else ""
    )


def _removed(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
    email = a.get("email") or b.get("email")
    return f"Removed {email}" if email else None


def _renamed(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
    if a.get("name") and b.get("name"):
        return f"Changed their name: {b['name']} to {a['name']}"
    return None


def _company_renamed(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
    if a.get("name") and b.get("name"):
        return f"Renamed the company: {b['name']} to {a['name']}"
    return None


def _model_chosen(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
    from othor.ai.kinds import LABELS
    from othor.ai.providers.presets import PRESETS

    p = PRESETS.get(str(a.get("provider") or ""))
    if p is None:
        return None
    model = f" · {a['model_id']}" if a.get("model_id") else ""
    kind = LABELS.get(str(a.get("kind") or ""))
    return f"Chose {p.label}{model} for Othor AI" + (f": {kind}" if kind else "")


def _tab_icon(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
    return {
        "letters": "The browser tab shows the company's letters",
        "logo": "The browser tab shows the company logo",
    }.get(str(a.get("tab_icon")))


def _found(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
    n = a.get("tables")
    return f"Finished looking through a source: {_plural(n, 'table')}" if n is not None else None


def _sheet_tabs(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
    tabs = a.get("tabs") or a.get("files")
    if isinstance(tabs, list) and tabs:
        what = "sheet tabs" if "tabs" in a else "sheets"
        return f"Chose the {what} to read: {', '.join(map(str, tabs[:4]))}" + (
            "…" if len(tabs) > 4 else ""
        )
    return None


def _version(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
    n = a.get("number")
    return f"Changed the Business Model: version {n}" if n else None


def _question(word: str) -> Sentence:
    def say(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
        text = str(a.get("text") or "").strip()
        return f"{word}: {text}" if text else None

    return say


def _decided(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
    rec = str(a.get("record") or "")
    return f"Answered a question for you · {rec.partition(':')[2]}" if ":" in rec else None


def _tool(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
    tool = str(a.get("tool") or "")
    where = "in chat" if a.get("via") != "mcp" else "from an AI app"
    if not tool:
        return None
    return f"Looked up {tool.replace('_', ' ')} {where}"


def _settings(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
    keys = sorted(set(a) | set(b))
    if not keys:
        return None
    return "Changed the settings: " + ", ".join(k.replace("_", " ") for k in keys[:3])


def _outside(word: str, tail: str) -> Sentence:
    def say(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
        name = a.get("name")
        return f"{word} {name}{tail}" if name else None

    return say


def _chat_applied(b: Mapping[str, Any], a: Mapping[str, Any], obj: str) -> str | None:
    if "changes" in a:
        what = _plural(a["changes"], "change")
    elif "answers" in a:
        what = _plural(a["answers"], "answer")
    else:
        return None
    if a.get("version"):
        what += f" to the Business Model (version {a['version']})"
    return APPLIED_DRAFT + what


SPECIAL: dict[str, Sentence] = {
    "othor_chat.applied": _chat_applied,
    "source.created": _source_created,
    "run.started": _run("started"),
    "run.finished": _run("finished"),
    "brief.delivered": _delivered,
    "brief.recipient_added": _recipient("Added"),
    "brief.recipient_removed": _recipient("Took"),
    "member.invited": _invited,
    "member.removed": _removed,
    "user.renamed": _renamed,
    "company.renamed": _company_renamed,
    "model.chosen": _model_chosen,
    "company.tab_icon_set": _tab_icon,
    "scan.finished": _found,
    "sheet_tabs.chosen": _sheet_tabs,
    "sheet_files.chosen": _sheet_tabs,
    "ontology.version_created": _version,
    "question.raised": _question("Othor AI asked"),
    "question.decided_by_ai": _decided,
    "tool.called": _tool,
    "outside.added": _outside("Added", " to the outside test"),
    "outside.not_now": _outside("Set aside the suggestion to test", ""),
    "outside.removed": _outside("Took", " out of the outside test"),
    "outside.brought_back": _outside("Brought back the suggestion to test", ""),
    "settings.changed": _settings,
}


def readable(action: str) -> str:
    """An action this registry doesn't name: 'source.metric_staged' → 'Source · metric staged'."""
    head, _, tail = action.partition(".")
    head = head.replace("_", " ").strip().capitalize() or "Something happened"
    return f"{head} · {tail.replace('_', ' ')}" if tail else head


def kind_of(action: str) -> str:
    if action in WORDS:
        return WORDS[action][0]
    for prefix, (kind, _) in FAMILIES.items():
        if action.startswith(prefix):
            return kind
    return PREFIX_KIND.get(action.partition(".")[0], "settings")


def named(action: str) -> bool:
    """True when the registry has a sentence for the action (the guard test's check)."""
    return action in WORDS or any(action.startswith(p) for p in FAMILIES)


def sentence(
    action: str,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    obj: str = "",
) -> str:
    """The plain sentence, with `{obj}` where the object's link goes (when it has one)."""
    b, a = dict(before or {}), dict(after or {})
    said = None
    special = SPECIAL.get(action)
    if special is not None:
        said = special(b, a, obj)
    if said is None:
        if action in WORDS:
            said = WORDS[action][1]
        else:
            fam = next((v for p, v in FAMILIES.items() if action.startswith(p)), None)
            said = fam[1] if fam else readable(action)
    if a.get("drafted_by") == "othor_ai" and not said.startswith(APPLIED_DRAFT):
        said = APPLIED_DRAFT + said[:1].lower() + said[1:]
    return said
