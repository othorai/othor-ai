# SPDX-License-Identifier: Apache-2.0
"""Sources suggested on 10a from the business message (M12b): the owner's own capitals stay."""

from __future__ import annotations

from othor.setup.suggest_sources import suggest


def test_the_owners_capitals_stay() -> None:
    got = {s.name for s in suggest(["MIS-Weekly", "erp_db"], "", [], set())}
    assert "MIS-Weekly" in got and "Mis-weekly" not in got
    assert "erp_db" in got


def test_a_lower_case_first_letter_is_raised() -> None:
    got = {s.name for s in suggest(["order tracker sheet"], "", [], set())}
    assert all(n is None or n[:1].isupper() or "_" in n for n in got)


def test_a_bare_kind_name_goes_once_that_kind_is_connected() -> None:
    """#19: the message said "PostgreSQL"; once sales_dw (PostgreSQL) is connected, the bare
    "PostgreSQL" suggestion goes, while named ones (erp_db) stay until they're connected."""
    from othor.setup.suggest_sources import without_connected_kinds

    sugs = suggest(["PostgreSQL", "erp_db", "MySQL"], "", [], set())
    assert [s.term for s in sugs] == ["PostgreSQL", "erp_db", "MySQL"]
    left = without_connected_kinds(sugs, {"postgres"})
    assert [s.term for s in left] == ["erp_db", "MySQL"]
    left = without_connected_kinds(sugs, {"postgres", "mysql"})
    assert [s.term for s in left] == ["erp_db"]  # a named database stays until it's connected
