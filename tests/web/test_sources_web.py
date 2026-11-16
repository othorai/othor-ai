# SPDX-License-Identifier: Apache-2.0
"""Sources pages end to end: onboarding (10a, 2j, 2k–2kd, 2l, 10b, 10c) and the Sources area
(6c, 6d, panels, 10r, 10m). Jobs run inline, as the worker would."""

from __future__ import annotations

import re
import uuid

import pytest
import sqlalchemy as sa

from othor.connectors.models import SheetTab, Source, SourceTable
from othor.core.tenancy import system_scope
from othor.jobs.runner import WorkerContext, run_pending
from tests.fixtures.sample_company.generate import generate
from tests.fixtures.sample_company.load import write_sheets
from tests.web.conftest import Env, signup

HOST = "http://indigothread.othor.test"
PW = "othor_ro_dev"


def _host(env: Env) -> str:
    return env.host("indigothread")


def _jobs(env: Env) -> int:
    svc = env.app.state.services  # type: ignore[attr-defined]
    return run_pending(WorkerContext(svc.settings, svc.sf, svc.key, clock=svc.clock))


def _to_sources(env: Env) -> None:
    signup(env)
    h = _host(env)
    for step in ("upload", "business"):
        r = (
            env.post(f"{h}/setup/{step}/continue", csrf_url=None)
            if False
            else env.client.post(
                f"{h}/setup/{step}/continue",
                data={"csrf": env.csrf(f"{h}/setup/{step}")},
                follow_redirects=False,
            )
        )
        assert r.status_code == 303


def _post(env: Env, path: str, data: dict | None = None, page: str = "/setup/sources", **kw):
    h = _host(env)
    d = {"csrf": env.csrf(h + page), **(data or {})}
    return env.client.post(h + path, data=d, follow_redirects=False, **kw)


def _db(env: Env, model, *where):
    sf = env.app.state.services.sf  # type: ignore[attr-defined]
    with sf() as s, system_scope("test"):
        return list(s.scalars(sa.select(model).where(*where)))


def _pg_up() -> bool:
    from othor.connectors.sql.engine import SqlCreds
    from othor.connectors.sql.scan import check_connection

    r = check_connection(
        SqlCreds(
            "postgresql", "localhost", 5443, "sales_dw", "othor_ro", PW, "disable", ("public",)
        )
    )
    return not r.message.startswith("Can't reach")


def test_picker_and_forms_render(env: Env):
    _to_sources(env)
    h = _host(env)
    r = env.client.get(f"{h}/setup/sources")
    assert r.status_code == 200 and "Connect where the numbers live." in r.text
    # M15.4: "Not listed?" opens the Request a connector form (was a support@othor.ai line)
    assert "Request a connector" in r.text and "panel=request" in r.text
    for kind, title in (("postgres", "Connect PostgreSQL"), ("mysql", "Connect MySQL")):
        r = env.client.get(f"{h}/setup/sources/{kind}/new")
        assert r.status_code == 200 and title in r.text
        assert (
            "A role that can write is refused." in r.text
            or "A user that can write is refused." in r.text
        )
    r = env.client.get(f"{h}/sources")
    assert r.status_code == 200 and "No sources connected" in r.text
    for q in (
        "panel=add",
        "tab=all",
        "tab=suggested",
        "panel=connect&kind=postgres",
        "panel=sheets",
    ):
        assert env.client.get(f"{h}/sources?{q}").status_code == 200, q
    # M11c G: Add a source is the onboarding list in a panel, no stepper
    add = env.client.get(f"{h}/sources?panel=add").text
    assert "Choose data" not in add and "panel-steps" not in add
    assert "Other connectors · 3" in add and "Request a connector" in add
    assert "Contact your Othor AI representative" not in add
    assert "tile-kind conn pg" in add and "tile-kind conn gs" in add
    sheets = env.client.get(f"{h}/sources?panel=sheets").text
    assert "Choose data" not in sheets and "tile-kind conn gs" in sheets


@pytest.mark.skipif(not _pg_up(), reason="needs make dev (sample sales_dw)")
def test_postgres_connect_scan_and_use_menu(env: Env):
    _to_sources(env)
    h = _host(env)
    form = {
        "host": "localhost",
        "port": "5443",
        "database": "sales_dw",
        "user": "othor_ro",
        "password": PW,
        "ssl": "disable",
        "setup": "1",
        "go": "1",
    }
    # M11c: the connect panel. Connect saves, tests, and connects once the test passes
    panel = "/setup/sources?panel=connect&kind=postgres"
    page = env.client.get(h + panel)
    assert "Connect PostgreSQL" in page.text and 'name="schemas"' not in page.text
    assert "Behind a firewall? Allow our addresses" in page.text
    r = _post(env, "/sources/sql/postgres/test", form, page=panel)
    assert r.status_code == 200 and "Testing the connection…" in r.text
    assert "test-status?go=1" in r.text
    sid = re.search(r'name="sid" value="([0-9a-f-]{36})"', r.text).group(1)
    _jobs(env)
    r = env.client.get(f"{h}/sources/{sid}/test-status?go=1", headers={"HX-Request": "true"})
    assert "Connected · read-only · 48 tables" in r.text and "Reachable" in r.text
    assert f'data-panel-go="/setup/sources?panel=scan&amp;s={sid}"' in r.text
    assert _db(env, Source, Source.id == uuid.UUID(sid))[0].status == "scanning"
    scan = f"{h}/setup/sources?panel=scan&s={sid}"
    page = env.client.get(scan)
    assert "Connecting PostgreSQL · sales_dw" in page.text and "You can close this." in page.text
    assert "every 1s" in page.text
    _jobs(env)
    page = env.client.get(scan)
    assert "Connected · PostgreSQL · sales_dw" in page.text
    assert "Schemas found: public, sales_dw" in page.text and "2 of 2 the role can see" in page.text
    assert "48 of 48" in page.text and "data-panel-go" not in page.text
    # a panel that watched the scan finish closes itself; the list shows the source
    live = env.client.get(scan + "&live=1")
    assert f'data-panel-go="/setup/sources?added={sid}"' in live.text
    r = env.client.get(f"{h}/setup/sources?added={sid}")
    assert "Connected · 1" in r.text and "PostgreSQL · sales_dw" in r.text
    assert "Just added" in r.text and "1 connected · you can add more later" in r.text
    assert f"panel=tables&amp;s={sid}" in r.text and f"panel=schemas&amp;s={sid}" in r.text
    # old addresses land on the panels
    r = env.client.get(f"{h}/setup/sources/{sid}/scan", follow_redirects=False)
    assert r.headers["location"] == f"/setup/sources?panel=scan&s={sid}"
    # the Tables panel: Used · Ignored · Excluded, search
    tables = f"{h}/setup/sources?panel=tables&s={sid}"
    page = env.client.get(tables)
    assert "Used · " in page.text and "sales_dw.fact_invoice" in page.text
    exc = env.client.get(tables + "&tab=excluded")
    assert "public.employee" in exc.text and "Names, phones, emails, PAN" in exc.text
    assert "Never read: not in queries, samples or prompts." in exc.text
    emp = _db(env, SourceTable, SourceTable.name == "employee")[0]
    srch = env.client.get(tables + "&q=invoice")
    assert "<mark>invoice</mark>" in srch.text and "tables match “invoice”" in srch.text
    # Use menu in the panel: ignore fact_order with a reason → toast with Undo → undo
    fo = _db(env, SourceTable, SourceTable.name == "fact_order")[0]
    url = tables
    r = env.client.post(
        f"{h}/sources/tables/{fo.id}/use",
        data={"use": "ignored", "reason": "not_needed"},
        headers={"HX-Request": "true", "HX-Current-URL": url, "X-CSRF-Token": env.csrf(url)},
    )
    loc = r.headers["HX-Redirect"]
    assert "toast=" in loc and "panel=tables" in loc
    r = env.client.get(h + loc)
    assert "sales_dw.fact_order · Ignored · Not needed" in r.text and "Undo" in r.text
    undo = re.search(r'hx-post="(/sources/tables/[^"]+/undo/[^"]+)"', r.text).group(1)
    env.client.post(
        h + undo,
        headers={"HX-Request": "true", "HX-Current-URL": url, "X-CSRF-Token": env.csrf(url)},
    )
    assert _db(env, SourceTable, SourceTable.id == fo.id)[0].use == "metric_source"
    # excluded can't be changed from the normal menu
    r = env.client.post(
        f"{h}/sources/tables/{emp.id}/use",
        data={"use": "dimension"},
        headers={"HX-Request": "true", "X-CSRF-Token": env.csrf(url)},
    )
    assert r.status_code == 400
    # bulk bring back (2kb) only for ignored tables
    bak = _db(env, SourceTable, SourceTable.name == "fact_invoice_bak_2023")[0]
    r = _post(
        env, f"/sources/{sid}/bring-back", {"use": "metric_source", "t": [str(bak.id), str(emp.id)]}
    )
    assert _db(env, SourceTable, SourceTable.id == bak.id)[0].use == "metric_source"
    assert _db(env, SourceTable, SourceTable.id == emp.id)[0].use == "excluded"
    # the Schemas panel: both read; untick public → rescan leaves its tables out; tick it again
    schemas = f"/setup/sources?panel=schemas&s={sid}"
    page = env.client.get(h + schemas)
    assert "The read-only role can see 2." in page.text and "2 of 2 schemas read" in page.text
    r = _post(env, f"/sources/{sid}/schemas", {"schema": ["sales_dw"]}, page=schemas)
    assert r.headers["location"] == f"/setup/sources?panel=scan&s={sid}"
    _jobs(env)
    emp = _db(env, SourceTable, SourceTable.id == emp.id)[0]
    assert emp.state == "skipped"
    page = env.client.get(h + schemas)
    assert "1 of 2 schemas read" in page.text and "not read" in page.text
    r = _post(env, f"/sources/{sid}/schemas", {"schema": ["public", "sales_dw"]}, page=schemas)
    _jobs(env)
    assert _db(env, SourceTable, SourceTable.id == emp.id)[0].state == "done"
    r = _post(env, f"/sources/{sid}/schemas", {"schema": ["public", "sales_dw"]}, page=schemas)
    assert r.headers["location"] == "/setup/sources"  # nothing changed: no rescan
    # Rescan from the panel goes to the scanning panel
    r = _post(
        env,
        f"/sources/{sid}/action/rescan",
        {"next": f"/setup/sources?panel=scan&s={sid}"},
        page=schemas,
    )
    assert r.headers["location"] == f"/setup/sources?panel=scan&s={sid}"
    _jobs(env)
    # the app's detail page keeps "use without personal columns" (2kc)
    exp = env.client.get(f"{h}/sources/{sid}?tab=excluded&expand={emp.id}")
    assert "Use without personal columns" in exp.text and "bank_account" in exp.text
    for path in (
        "/sources",
        f"/sources?s={sid}",
        f"/sources/{sid}",
        f"/sources/{sid}?tab=ignored",
        f"/sources?panel=manage&s={sid}",
        f"/sources?panel=scan&s={sid}",
        f"/sources?panel=review&s={sid}",
        f"/sources?panel=reconnect&s={sid}",
        f"/sources?panel=connect&kind=postgres&sid={sid}",
        f"/sources?panel=tables&s={sid}",
        f"/sources?panel=schemas&s={sid}",
    ):
        r = env.client.get(h + path)
        assert r.status_code == 200, path
        assert PW not in r.text, path  # the password is never shown back
    # M11c G: Tables and Schemas open the onboarding panels from 6c, 6d and Manage (10r)
    tables, schemas_p = f"/sources?panel=tables&amp;s={sid}", f"/sources?panel=schemas&amp;s={sid}"
    for path in ("/sources", f"/sources/{sid}", f"/sources?panel=manage&s={sid}&tab=tables"):
        text = env.client.get(h + path).text
        assert tables in text, path
        assert schemas_p in text, path
    app_scan = env.client.get(f"{h}/sources?panel=scan&s={sid}").text
    assert "Connected · PostgreSQL · sales_dw" in app_scan and "Choose data" not in app_scan
    assert (
        "Healthy" in env.client.get(f"{h}/sources").text
        or "First read tonight" in env.client.get(f"{h}/sources").text
    )
    # pause, then disconnect: credentials deleted
    _post(env, f"/sources/{sid}/action/pause", page="/sources")
    assert _db(env, Source, Source.id == uuid.UUID(sid))[0].status == "paused"
    _post(env, f"/sources/{sid}/action/disconnect", page="/sources")
    src = _db(env, Source, Source.id == uuid.UUID(sid))[0]
    assert src.status == "disconnected" and src.deleted_at is not None
    assert {e.action for e in env.events()} >= {
        "source.created",
        "source.tested",
        "source.connected",
        "scan.finished",
        "table_use.changed",
        "table_use.undone",
        "scan.schema_skipped",
        "scan.schema_unskipped",
        "source.paused",
        "source.disconnected",
    }


def test_sheets_pick_check_and_connect(env: Env, tmp_path):
    write_sheets(generate(), tmp_path / "sample" / "sheets")
    _to_sources(env)
    h = _host(env)
    r = env.client.get(f"{h}/setup/sources/sheets")
    assert "Sign in with Google" in r.text
    r = _post(env, "/sources/google/start", {"setup": "1"}, page="/setup/sources/sheets")
    assert r.status_code == 303
    sid = r.headers["location"].split("sid=")[1]
    r = env.client.get(h + r.headers["location"])
    assert "Signed in as uday@indigothread.example" in r.text and "Choose a spreadsheet" in r.text
    assert "Google adds one spreadsheet at a time" in r.text
    r = env.client.get(f"{h}/sources/{sid}/picker")
    assert "Order tracker FY27" in r.text and "MIS-Weekly" in r.text
    r = _post(
        env,
        f"/sources/{sid}/files",
        {"file": "sample-order-tracker-fy27"},
        page="/setup/sources/sheets",
    )
    _jobs(env)
    r = env.client.get(f"{h}/setup/sources/sheets?sid={sid}")
    assert "Order tracker FY27 · edited" in r.text and "by Priya Menon" in r.text
    assert "Check the header" in r.text and re.search(
        r"Check 1 header, then scan \d+ tabs?", r.text
    )
    assert "Excluded · Personal data" in r.text
    tabs = {t.tab_name: t for t in _db(env, SheetTab, SheetTab.source_id == uuid.UUID(sid))}
    chosen = [str(tabs[n].id) for n in ("Open orders", "Deals")]
    r = _post(env, f"/sources/{sid}/tabs", {"tab": chosen}, page="/setup/sources/sheets")
    check = r.headers["location"]
    assert check == f"/setup/sources/{sid}/check/{tabs['Open orders'].id}"
    r = env.client.get(h + check)
    for text in (
        "Check “Open orders”",
        "Row 3",
        "Rows 1–2 are a title, left out",
        "A4:H88 · 85 rows",
        "Row 89 is a total, left out",
        "Blank, left out",
        "2 merged cells, filled down",
        "Rows 5 and 41",
        "Two formats, read as dates",
        "Read as ₹ lakh",
        "₹1,512.4 L in total",
        "4 values",
        "1 of 1 tab to check",
    ):
        assert text in r.text, text
    assert "Ravira Exports" in r.text  # the preview grid
    r = _post(env, check + "/confirm", page=check)
    assert r.headers["location"] == "/setup/sources"
    r = env.client.get(f"{h}/setup/sources")
    assert "Google Sheets · Order tracker FY27" in r.text and "Connected · 1" in r.text
    tab = _db(env, SheetTab, SheetTab.id == tabs["Open orders"].id)[0]
    assert tab.status == "confirmed" and "preview" not in tab.shape
    # Read now from the app → DuckDB holds the tabs
    _post(env, f"/sources/{sid}/action/read", page="/sources")
    _jobs(env)
    src = _db(env, Source, Source.id == uuid.UUID(sid))[0]
    assert src.health["last_read"]["tabs"] == 2
    # Only the chosen tabs exist for drafting and the source card (M12b: unchosen tabs counted)
    from othor.core.tenancy import tenant_scope
    from othor.ontology.compiler.catalog import load_catalog
    from othor.web.sources_view import source_card

    sf = env.app.state.services.sf  # type: ignore[attr-defined]
    with sf() as s, tenant_scope(src.company_id):
        cat = load_catalog(s)
        names = sorted(n for (sname, _sc, n), t in cat.tables.items() if t.source_id == sid)
        card = source_card(s, src)
    assert names == ["Deals", "Open orders"], names
    assert card.tables_used == 2 and card.tables_total == 2, card


def test_other_company_gets_404_on_source_pages(env: Env, tmp_path):
    write_sheets(generate(), tmp_path / "sample" / "sheets")
    _to_sources(env)
    h = _host(env)
    r = _post(env, "/sources/google/start", {"setup": "1"}, page="/setup/sources/sheets")
    sid = r.headers["location"].split("sid=")[1]
    env.client.post(f"{h}/logout", data={"csrf": env.csrf(f"{h}/settings")})
    env.client.cookies.clear()
    signup(env, email="owner@otherco.in", company="Other Co", slug="otherco")
    o = env.host("otherco")
    for path in (
        f"/sources/{sid}",
        f"/setup/sources/{sid}/scan",
        f"/sources/{sid}/test-status",
        f"/setup/sources/sheets?sid={sid}",
    ):
        r = env.client.get(o + path)
        assert r.status_code in (404, 200), path
        assert "Order tracker" not in r.text and "uday@indigothread.example" not in r.text, path
    assert env.client.get(f"{o}/sources/{sid}").status_code == 404


def test_manage_panel_tabs_are_url_state(env: Env, tmp_path):
    """10r: Overview by default; ?tab=sync opens Sync; the footer is the same on every tab."""
    write_sheets(generate(), tmp_path / "sample" / "sheets")
    _to_sources(env)
    h = _host(env)
    r = _post(env, "/sources/google/start", {"setup": "1"}, page="/setup/sources/sheets")
    sid = r.headers["location"].split("sid=")[1]
    base = f"{h}/sources?panel=manage&s={sid}"
    pages = {t: env.client.get(f"{base}&tab={t}").text for t in ("connection", "tables", "sync")}
    pages["overview"] = env.client.get(base).text
    assert 'class="tab is-on" href="/sources?panel=manage&amp;s=' in pages["overview"]
    for tab, label in (
        ("overview", "Overview"),
        ("connection", "Connection"),
        ("tables", "Tables"),
        ("sync", "Sync"),
    ):
        text = pages[tab]
        assert re.search(rf'class="tab is-on"[^>]*>{label}<', text), tab
        assert "Pause reading" in text and "Disconnect…" in text and ">Done<" in text, tab
    assert "computed from it" in pages["overview"]  # the three tiles are on Overview only
    assert "computed from it" not in pages["sync"]
    assert "Schedule" in pages["sync"] and "Signed in as" in pages["connection"]


def test_a_second_pick_adds_a_spreadsheet_and_remove_takes_it_with_its_tabs(env: Env, tmp_path):
    """Google's Picker gives one spreadsheet per pick (owner, 11 Nov 2026): a pick adds to the
    connection; Remove takes a spreadsheet away with its tabs, so nothing stale is read."""
    write_sheets(generate(), tmp_path / "sample" / "sheets")
    _to_sources(env)
    h = _host(env)
    r = _post(env, "/sources/google/start", {"setup": "1"}, page="/setup/sources/sheets")
    sid = r.headers["location"].split("sid=")[1]
    _post(
        env,
        f"/sources/{sid}/files",
        {"file": "sample-order-tracker-fy27"},
        page="/setup/sources/sheets",
    )
    _jobs(env)
    _post(env, f"/sources/{sid}/files", {"file": "sample-mis-weekly"}, page="/setup/sources/sheets")
    _jobs(env)
    src = _db(env, Source, Source.id == uuid.UUID(sid))[0]
    assert [f["id"] for f in src.config["files"]] == [
        "sample-order-tracker-fy27",
        "sample-mis-weekly",
    ]
    page = env.client.get(f"{h}/setup/sources/sheets?sid={sid}").text
    assert "Add another spreadsheet" in page and page.count("/remove") == 2
    titles = {t.spreadsheet_title for t in _db(env, SheetTab, SheetTab.source_id == src.id)}
    assert titles == {"Order tracker FY27", "MIS-Weekly"}

    r = _post(env, f"/sources/{sid}/files/sample-mis-weekly/remove", page="/setup/sources/sheets")
    assert r.status_code == 303
    src = _db(env, Source, Source.id == uuid.UUID(sid))[0]
    assert [f["id"] for f in src.config["files"]] == ["sample-order-tracker-fy27"]
    tabs = _db(env, SheetTab, SheetTab.source_id == src.id)
    assert {t.spreadsheet_title for t in tabs} == {"Order tracker FY27"}
    gone = _db(
        env, SourceTable, SourceTable.source_id == src.id, SourceTable.schema == "MIS-Weekly"
    )
    assert gone and all(t.gone_at is not None for t in gone)


def test_manage_is_the_primary_button_on_a_connected_source() -> None:
    """Owner, 11 Nov 2026: Manage on each connected source is the theme's blue (primary) button;
    Tables and Schemas stay secondary."""
    from pathlib import Path

    tpl = (
        Path(__file__).resolve().parents[2] / "othor/web/templates/sources/sources.html"
    ).read_text()
    assert 'button("Manage", "primary", "sm"' in tpl
    assert 'button("Tables", "", "sm"' in tpl
    # owner, 12 Nov 2026: no panel docked beside the table and the row isn't a link; the source's
    # panel opens only from Manage, with the numbers it feeds and its matches
    assert "table-select" not in tpl and "row-link" not in tpl and 'pane("side-pane"' not in tpl
    mtpl = (
        Path(__file__).resolve().parents[2] / "othor/web/templates/sources/_manage.html"
    ).read_text()
    assert "Numbers it feeds" in mtpl and "Matched with your other sources" in mtpl


def test_a_new_source_never_takes_a_name_already_in_use(env: Env, tmp_path):
    """The Ontology names sources, so two with one name collide (Indigo Thread on production,
    11 Nov 2026: two Sheets connections both "Google Sheets")."""
    write_sheets(generate(), tmp_path / "sample" / "sheets")
    _to_sources(env)
    for _ in range(2):
        _post(env, "/sources/google/start", {"setup": "1"}, page="/setup/sources/sheets")
    names = sorted(x.name for x in _db(env, Source, Source.kind == "gsheets"))
    assert names == ["Google Sheets", "Google Sheets 2"]


def test_the_catalog_keeps_the_first_of_two_sources_with_one_name(env: Env, tmp_path):
    """A company made before names were unique: the first connected keeps the name, the later
    one is left out, the same way every time; it never overwrites the first."""
    from othor.core.tenancy import tenant_scope
    from othor.ontology.compiler.catalog import load_catalog

    write_sheets(generate(), tmp_path / "sample" / "sheets")
    _to_sources(env)
    for _ in range(2):
        _post(env, "/sources/google/start", {"setup": "1"}, page="/setup/sources/sheets")
    first, second = sorted(_db(env, Source, Source.kind == "gsheets"), key=lambda x: x.created_at)
    sf = env.client.app.state.services.sf  # type: ignore[attr-defined]
    with sf() as s, tenant_scope(first.company_id):
        for src in s.scalars(sa.select(Source).where(Source.kind == "gsheets")):
            src.name, src.status = "Google Sheets", "connected"
        s.commit()
        cat = load_catalog(s)
    assert cat.sources["Google Sheets"][0] == str(first.id)
    assert str(second.id) not in {i for i, _k in cat.sources.values()}


def test_sheets_rows_say_tabs_read_and_the_tables_panel_explains_used() -> None:
    """Owner, 11 Nov 2026: the row said "4 tabs" next to the Tables panel's "Used · 6". The row
    counts the tabs chosen to read; the panel says so and links to Choose tabs."""
    from pathlib import Path

    t = Path(__file__).resolve().parents[2] / "othor/web/templates/sources"
    src = (t / "sources.html").read_text()
    assert '" tab" ~ ("s" if c.tables_used != 1 else "") ~ " read"' in src
    panel = (t / "_panel_tables.html").read_text()
    assert "read each night" in panel and "Choose tabs →" in panel and "?panel=sheets&s=" in panel
