# SPDX-License-Identifier: Apache-2.0
"""Read-only check (MySQL SHOW GRANTS) and the personal-column guard on every source query."""

from __future__ import annotations

import pytest

from othor.connectors.sql.grants import REFUSED, mysql_write_privileges
from othor.connectors.sql.query_log import PersonalColumnInQuery, QueryLog, guard_sql
from othor.connectors.sql.scan import stats_sql

RO = [
    "GRANT USAGE ON *.* TO `othor_ro`@`%`",
    "GRANT SELECT, SHOW VIEW ON `erp_db`.* TO `othor_ro`@`%`",
]


def test_refusal_message_is_verbatim():
    assert REFUSED == "This user can change data. Create a read-only user and try again."


def test_read_only_mysql_user_passes():
    assert mysql_write_privileges(RO, "erp_db") == []


@pytest.mark.parametrize(
    ("line", "bad"),
    [
        (
            "GRANT SELECT, INSERT, UPDATE, DELETE ON `erp_db`.* TO `othor_rw`@`%`",
            ["DELETE", "INSERT", "UPDATE"],
        ),
        (
            "GRANT ALL PRIVILEGES ON *.* TO `root`@`%` WITH GRANT OPTION",
            ["ALL PRIVILEGES", "GRANT OPTION"],
        ),
        ("GRANT ALL PRIVILEGES ON `erp_db`.* TO `x`@`%`", ["ALL PRIVILEGES"]),
        ("GRANT SELECT, CREATE, DROP ON `erp_db`.* TO `x`@`%`", ["CREATE", "DROP"]),
        ("GRANT UPDATE (`status`) ON `erp_db`.`invoices` TO `x`@`%`", ["UPDATE"]),
        ("GRANT INSERT ON *.* TO `x`@`%`", ["INSERT"]),
        ("GRANT `writer_role`@`%` TO `x`@`%`", ["ROLE writer_role"]),
    ],
)
def test_writer_mysql_user_is_refused(line, bad):
    assert sorted(mysql_write_privileges([*RO, line], "erp_db")) == sorted(bad)


def test_rights_on_another_database_do_not_count():
    other = "GRANT INSERT, UPDATE ON `scratch`.* TO `othor_ro`@`%`"
    assert mysql_write_privileges([*RO, other], "erp_db") == []


PERSONAL = {
    "employee": {"name", "phone", "email", "pan", "bank_account", "address"},
    "customers": {"contact_email", "contact_phone"},
}


def test_guard_blocks_a_personal_column():
    with pytest.raises(PersonalColumnInQuery):
        guard_sql('SELECT count("pan") FROM "public"."employee"', PERSONAL)
    with pytest.raises(PersonalColumnInQuery):
        guard_sql("SELECT contact_email FROM erp_db.customers", PERSONAL)


def test_guard_allows_non_personal_columns_and_literals():
    guard_sql('SELECT count(*), count(DISTINCT "plant") FROM "public"."employee"', PERSONAL)
    guard_sql("SELECT name, region FROM erp_db.customers", PERSONAL)  # company name, not personal
    guard_sql(
        "SELECT column_name FROM information_schema.columns WHERE column_name = 'pan'", PERSONAL
    )


def test_stats_query_for_employee_never_names_a_personal_column():
    cols = [
        ("employee_id", "int"),
        ("department", "text"),
        ("plant", "text"),
        ("grade", "text"),
        ("join_date", "date"),
        ("exit_date", "date"),
    ]
    for dialect in ("postgresql", "mysql"):
        sql = stats_sql(dialect, "public", "employee", cols, sample=False)
        guard_sql(sql, PERSONAL)
        for p in PERSONAL["employee"]:
            assert f'"{p}"' not in sql and f"`{p}`" not in sql


def test_query_log_records_hash_and_rows():
    log = QueryLog(personal=PERSONAL)
    log.record("SELECT 1", {}, rows=1, ms=2)
    (q,) = log.entries
    assert q.rows == 1 and len(q.hash) == 64
