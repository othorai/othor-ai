# SPDX-License-Identifier: Apache-2.0
"""Board states on demand (M12): every build board in docs/08-screens.md, put into its board's
state on a fresh test app, so it can be captured and compared at any time (docs/ui-review).

Each entry names a world (how the company is set up), the page path and the viewport width. The
paths are templates filled from the world (`{bid}` the brief, `{fp_<detector>}` a finding of
tonight's run, `{sid}` the ERP source), plus whatever the state's `prep` returns. A state with a
`prep` starts from a fresh copy of its world, and the named builder in `tests/web/board_worlds.py`
moves it into the board's state. `actions` are browser steps run before the capture (an open
menu, ticked rows, a wrong code submitted; see `act()` in scripts/compare_screen.py).
`tests/web/test_board_states.py` fails if a build board isn't registered.

Worlds:
- `root`: the root host, signed out.
- `company`: the company host, signed out (a company exists).
- `fresh`: signed up, onboarding not started.
- `night`: the steady night of Sun 1 Nov 2026 on the sample company (needs `make dev && make seed`).
- `components`: the component pages (/_components), any session.
- `auth`: a company exists and its owner is signed out; the prep drives sign-in or signup.
- `setup`: signed up, with the sample sources (`make dev && make seed`); the prep moves
  onboarding to the board's step.
- `mail`: an email; `path` is its golden file in tests/ui/mail/golden/ (the snapshot tests).
- `rules`: a rules board (several states on one sheet). Not captured; `note` names the tests that
  hold its rules.

Dev and test only: nothing in othor/ imports this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class BoardState:
    id: str
    world: str
    path: str
    width: int = 1440
    note: str = ""
    prep: str = ""
    actions: tuple[str, ...] = ()


_S = BoardState
WRONG_CODE = (
    *(f'fill:input[aria-label="Digit {i}"]|{d}' for i, d in enumerate("000001", 1)),
    "wait:800",  # the sixth digit submits the form (othor.js)
)
STATES: dict[str, BoardState] = {
    s.id: s
    for s in [
        # sign-in and company (M1)
        _S("1a", "root", "/login"),
        _S("1b", "company", "/login"),
        _S("1e", "root", "/signup"),
        _S("1g", "company", "/login?signed_out=1"),
        _S("1c", "auth", "/login/code", prep="code_sent"),
        _S("1d", "auth", "/login/code", prep="code_sent", actions=WRONG_CODE),
        _S("1f", "auth", "/signup/verify", prep="at_signup_verify"),
        _S(
            "1h",
            "auth",
            "/login",
            actions=('fill:input[name="email"]|ravi@indigothread.example', "submit:form.auth-card"),
        ),
        _S("2a", "auth", "/signup/address", prep="at_signup_address"),
        # onboarding
        _S("2b", "fresh", "/setup/upload"),
        # Home
        _S("3a", "night", "/home"),
        _S("3aa", "night", "/home?panel=questions"),
        # Morning Brief
        _S("4d", "night", "/brief/{bid}"),
        _S("4d1", "night", "/brief/{bid}?view=full"),
        _S("4d2", "night", "/brief/{bid}/edit"),
        _S("4d3", "night", "/brief/{bid}/edit?tab=scope"),
        _S("4d4", "night", "/brief/{bid}/edit?tab=numbers"),
        _S("4d6", "night", "/brief/{bid}/edit?tab=delivery"),
        _S("4c", "night", "/brief/new"),
        _S("4e", "night", "/brief/archive"),
        _S("4g", "night", "/brief/{bid}/finding/{fp_net_hiding_gross}"),
        _S("4f", "night", "/brief/{bid}/finding/{fp_net_hiding_gross}?tab=test"),
        _S("4f1", "night", "/brief/{bid}/finding/{fp_net_hiding_gross}?tab=metric"),
        _S("4f2", "night", "/brief/{bid}/finding/{fp_net_hiding_gross}?tab=sql"),
        _S("4f3", "night", "/brief/{bid}/finding/{fp_net_hiding_gross}?tab=run"),
        _S("10g", "night", "/brief/{bid}", width=390),
        # Sweep
        _S("5a", "night", "/sweep"),
        _S("5b", "night", "/sweep/finding/{fp_missing_records}"),
        _S("5c", "night", "/sweep/finding/{fp_cross_source}"),
        _S("5d", "night", "/sweep/watch/{fp_rising_variance}"),  # the board drafts the spread watch
        _S("5e", "night", "/sweep?tab=watching"),
        _S("10h", "night", "/sweep/finding/{fp_net_hiding_gross}", width=390),
        _S("10o", "night", "/sweep/finding/{fp_concentration}/brief"),
        # Ontology
        _S("6a", "night", "/ontology"),
        _S("6a2", "night", "/ontology?metric=net_revenue"),
        _S("6a3", "night", "/ontology?panel=othor&subject=ontology"),
        _S("6a4", "night", "/ontology?panel=history"),
        # Sources
        _S("6c", "night", "/sources"),
        _S("6d", "night", "/sources/{sid}"),
        _S("6e", "night", "/sources?panel=add"),
        _S("10r", "night", "/sources?panel=manage&s={sid}"),
        # Settings
        _S("7a", "night", "/settings"),
        # --- setup world (M12b): onboarding steps; builders in tests/web/board_worlds_setup.py
        _S(
            "2c",
            "setup",
            "/setup/upload",
            prep="files_read",
            note="no model: the screenshot waits for it",
        ),
        _S("2d", "setup", "/setup/business", prep="at_business_step", note="no model: no draft"),
        _S("10a", "setup", "/setup/sources", prep="at_sources_step"),
        # M11c: connecting, scanning, tables and schemas open in side panels over the list
        _S(
            "10a1",
            "setup",
            "/setup/sources?panel=connect&kind=mysql&sid={sid}",
            prep="mysql_tested",
        ),
        _S(
            "2j",
            "setup",
            "/setup/sources?panel=connect&kind=postgres&sid={sid}",
            prep="postgres_tested",
        ),
        _S("2k", "setup", "/setup/sources?panel=scan&s={sid}", prep="postgres_scanning"),
        _S(
            "2ka",
            "setup",
            "/setup/sources?panel=tables&s={sid}",
            prep="postgres_scanning",
            actions=('click:summary[aria-label="Change how sales_dw.fact_order is used"]',),
        ),
        _S(
            "2kb",
            "setup",
            "/setup/sources?panel=tables&s={sid}&tab=ignored",
            prep="postgres_scanning",
        ),
        _S(
            "2kc",
            "setup",
            "/setup/sources?panel=tables&s={sid}&tab=excluded",
            prep="postgres_scanning",
        ),
        _S(
            "2kd",
            "setup",
            "/setup/sources?panel=tables&s={sid}&q=invoice",
            prep="postgres_scanning",
        ),
        _S(
            "2ke",
            "setup",
            "/setup/sources?panel=tables&s={sid}&tab=ignored",
            prep="postgres_scanning",
            actions=(
                'click:summary[aria-label="Change how sales_dw.fact_invoice_bak_2023 is used"]',
            ),
        ),
        _S("2kf", "setup", "/setup/sources?panel=schemas&s={sid}", prep="postgres_scanned"),
        _S("2l", "setup", "/setup/sources", prep="postgres_scanned"),
        _S(
            "2la",
            "setup",
            "/setup/sources",
            prep="four_sources",
            note="the Excel upload is out of v0.1: the four v0.1 sources (two Sheets)",
        ),
        _S("2m", "setup", "/setup/metrics", prep="metrics_drafting"),
        _S("2n", "setup", "/setup/metrics", prep="metrics_drafted"),
        _S("2n0", "setup", "/setup/metrics?panel=ontology", prep="metrics_drafted"),
        _S("2na", "setup", "/setup/metrics?panel=decisions", prep="metrics_drafted"),
        _S("2ng", "setup", "/setup/metrics?panel=assumptions", prep="metrics_drafted"),
        _S(
            "2nh",
            "setup",
            "/setup/metrics?panel=assumptions&key={assumption}",
            prep="metrics_drafted",
        ),
        _S("2ni", "setup", "/setup/metrics?panel=confirm", prep="metrics_drafted"),
        _S(
            "2nb",
            "setup",
            "/setup/metrics?panel=chat&purpose=where&key=dispatch_dispatch_mtrs",
            prep="metrics_drafted",
        ),
        _S(
            "2nc",
            "setup",
            "/setup/metrics?panel=chat&purpose=connect&key=order_book_value",
            prep="metrics_drafted",
        ),
        _S(
            "2nca",
            "setup",
            "/setup/metrics?panel=connect&key=order_book_value&kind=gsheets&sid={sid}",
            prep="tracker_choose_data",
        ),
        _S(
            "2ncb",
            "setup",
            "/setup/metrics?panel=connect&key=order_book_value&kind=gsheets&sid={sid}",
            prep="tracker_connected",
        ),
        _S(
            "2nd",
            "setup",
            "/setup/metrics?panel=chat&purpose=edit&key=receivables_over_60",
            prep="edit_change_computed",
        ),
        _S("2ne", "setup", "/setup/metrics/document", prep="document_redrafting"),
        _S("2nf", "setup", "/setup/metrics", prep="document_redrafted"),
        _S("2o", "setup", "/setup/first-run", prep="first_run_running"),
        _S("2oc", "setup", "/setup/first-run", prep="first_run_done"),
        _S("2oa", "setup", "/home", prep="first_run_left"),
        _S("2ob", "setup", "/brief", prep="first_run_left"),
        _S("10b", "setup", "/setup/sources/sheets?sid={sid}", prep="sheets_signed_in"),
        _S("10c", "setup", "{check}", prep="sheets_tab_check"),
        # --- end setup world
        # --- night variants (M12b): builders in tests/web/board_worlds_night.py
        _S("3b", "night", "/home", prep="all_caught_up"),
        _S("4a", "night", "/brief"),
        _S("4b", "night", "/brief", prep="several_briefs"),
        _S(
            "4c1",
            "night",
            "/brief",
            prep="several_briefs",
            actions=('submit:form[action="/brief/new"]:has(input[name="suggestion"])',),
        ),
        _S("4c2", "night", "/brief?created={new}", prep="brief_created"),
        _S("4d0", "night", "/brief/{quiet}", prep="quiet_day"),
        _S("4d5", "night", "/brief/{bid}/edit?tab=add&q=receivables"),
        _S("4e1", "night", "/brief/archive?month=2026-11&failed={failed}", prep="failed_night"),
        _S("5f", "night", "/sweep", prep="quiet_night"),
        _S("6a1", "night", "/ontology?numbers=all"),
        _S("6eb", "night", "/sources?panel=sheets&s={new}", prep="tracker_tabs_listed"),
        _S("6f", "night", "/sources?panel=sheets&s={new}", prep="tracker_tabs_listed"),
        _S("6g", "night", "/sources?panel=scan&s={new}", prep="tracker_scanning"),
        _S("6h", "night", "/sources?panel=review&s={new}", prep="tracker_review"),
        _S(
            "6ha",
            "night",
            "/sources?panel=chat&s={new}&key={fix}&purpose=fix",
            prep="tracker_review",
        ),
        _S("6i", "night", "/sources?panel=manage&s={new}", prep="tracker_confirmed"),
        _S("10d", "night", "{link}", prep="link_opened"),
        _S("10e", "night", "{link}", prep="link_expired"),
        _S("10f", "night", "{link}", prep="link_unsubscribe"),
        _S("10i", "setup", "/sweep", prep="first_days"),
        _S("10j", "setup", "/brief/{bid}", prep="first_days"),
        _S("10k", "night", "/home", prep="sheets_revoked"),
        _S("10l", "night", "/brief/{bid}", prep="sheets_revoked"),
        _S("10m", "night", "/sources?panel=reconnect&s={mis}", prep="sheets_revoked"),
        _S("10n", "night", "/ontology?metric=order_book_value"),
        _S("10p", "night", "/sweep/finding/{fp_nd}", prep="not_determinable"),
        # --- end night variants
        # emails: the golden HTML of the snapshot tests (tests/ui/mail), at the board's 640 px
        _S("8a", "mail", "8a_sign_in_code.html", width=640),
        _S("8b", "mail", "8b_brief_simple.html", width=640),
        _S("8b1", "mail", "8b1_brief_full.html", width=640),
        _S("8c", "mail", "8c_run_failed.html", width=640, note="also 8c_source_stale, 8c_run_late"),
        _S("8d", "mail", "8d_ontology_waiting.html", width=640),
        _S("8e", "mail", "8e_weekly_digest.html", width=640),
        _S("10q", "mail", "10q_brief_quiet.html", width=640),
        # rules boards: several states on one sheet, held by tests rather than one capture
        _S(
            "4h",
            "rules",
            "",
            note="the four brief states: tests/unit/briefs/test_assemble.py, test_tiles.py, "
            "tests/ui/mail (8b, 8b_stale, 10q) and the 4d/4d0/10l captures",
        ),
        _S(
            "4h1",
            "rules",
            "",
            note="brief rules (cause not determinable, stale source, title and Ask today): "
            "tests/unit/briefs/test_title.py, test_ask_line.py, test_tiles.py",
        ),
        # components
        _S("9a", "components", "/_components/9a"),
        _S("9b", "components", "/_components/9b"),
        _S("9c", "components", "/_components/9c"),
    ]
}

# Boards not registered yet, with the reason. M12b fills these in (docs/plans/M12.md §4–5).
_M12B = "M12b: needs its own world builder"
PENDING: dict[str, str] = {}

ROW = re.compile(r"^\| (\d+[a-z0-9]*) \|[^|]*\|[^|]*\|\s*([^|]*?)\s*\|", re.M)


def build_boards() -> list[str]:
    """Board ids marked `build` in docs/08-screens.md."""
    text = (ROOT / "docs" / "08-screens.md").read_text()
    return [m.group(1) for m in ROW.finditer(text) if m.group(2).startswith("build")]


def fill(path: str, values: dict[str, str]) -> str:
    return path.format(**values)


def placeholders(path: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", path))
