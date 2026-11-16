# SPDX-License-Identifier: Apache-2.0
"""A source's name stays unique when its connection details are saved again (indigo2, 12 Nov
2026: "sales_dw 2" went back to "sales_dw" on Test connection, so two sources shared a name)."""

from __future__ import annotations

from othor.connectors import service
from othor.connectors.models import Source
from othor.core.crypto import InstanceKey
from othor.core.tenancy import tenant_scope

CONFIG = {
    "host": "db",
    "port": 5432,
    "database": "sales_dw",
    "user": "othor_ro",
    "sslmode": "disable",
}


def _sql(s, key, cid, config=CONFIG):
    return service.create_sql_source(
        s, key, cid, kind="postgres", config=dict(config), password="x", actor="t", in_setup=True
    )


def test_saving_the_details_again_keeps_the_unique_name(session_factory, two_companies, tmp_path):
    cid, _ = two_companies
    key = InstanceKey.load_or_create(tmp_path / "k", allow_create=True)
    with session_factory() as s, tenant_scope(cid):
        first = _sql(s, key, cid)
        second = _sql(s, key, cid)
        s.flush()
        assert (first.name, second.name) == ("sales_dw", "sales_dw 2")
        service.update_sql_source(s, key, second, config=dict(CONFIG), password=None, actor="t")
        assert second.name == "sales_dw 2"
        # a different database renames it, still never onto a name in use
        service.update_sql_source(
            s, key, second, config={**CONFIG, "database": "erp_db"}, password=None, actor="t"
        )
        assert second.name == "erp_db"
        service.update_sql_source(
            s, key, first, config={**CONFIG, "database": "erp_db"}, password=None, actor="t"
        )
        assert first.name == "erp_db 2"
        assert len({x.name for x in s.query(Source)}) == 2
