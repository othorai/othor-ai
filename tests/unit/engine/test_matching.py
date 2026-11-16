# SPDX-License-Identifier: Apache-2.0
"""Cross-source matching (6h, decided 5 Nov 2026): the share of another source's keys that match
an entity, by identity (GSTIN) or by a hard-normalised name; ambiguous names left out and listed;
a join proposed only at 80%+ with at least 20 matched keys."""

from __future__ import annotations

from othor.engine.matching import compute, norm_identity, norm_name

ROWS = [[f"Customer {i:02d} Pvt Ltd", "active", f"27AAA{i:02d}", None] for i in range(30)]
ROWS += [
    ["Ravira Exports", "active", "27KRI", None],
    ["Shree Varada Textiles", "active", "29SGT", None],
]
ROWS += [["Laxmi Mills Ltd", "active", "1", None], ["Laxmi Mills Limited", "active", "2", None]]


def _spec(by="name", joined=False):
    return {
        "probes": {
            "entities": {"customer": {"source": "erp_db"}},
            "matches": [
                {
                    "key": "customer:t1:customer",
                    "entity": "customer",
                    "by": by,
                    "source": "Order tracker",
                    "table_name": "Deals",
                    "column": "customer",
                    "to_source": "erp_db",
                    "to_name": "customers",
                    "to_column": "name",
                    "joined": joined,
                }
            ],
        }
    }


def _files(values):
    return {
        "erp_db": {"entities": {"customer": ROWS}},
        "Order tracker": {"matches": [{"key": "customer:t1:customer", "values": values}]},
    }


def test_names_are_normalised_hard():
    assert norm_name("Shree Varada Textiles Pvt. Ltd.") == "shree varada textiles"
    assert norm_name("SHREE VARADA TEXTILES PRIVATE LIMITED") == "shree varada textiles"
    assert norm_name("Anvika & Co") == "anvika"
    assert norm_name("Kaveri Inds") == norm_name("Kaveri Industries LLP") == "kaveri"
    assert norm_identity("27 aaa-cK1234") == "27AAACK1234"


def test_share_ambiguous_unmatched_and_the_minimum():
    vals = ["ravira exports", "Shree Varada Textiles Pvt Ltd", "Laxmi Mills", "New Prospect"]
    (m,) = compute(_spec(), _files(vals))
    assert (m["matched"], m["total"], m["unmatched"], m["ambiguous_count"]) == (2, 4, 1, 1)
    assert m["ambiguous"] == [["Laxmi Mills", ["Laxmi Mills Limited", "Laxmi Mills Ltd"]]]
    assert m["examples"] == ["New Prospect"] and m["grade"] == "name"
    assert ["Shree Varada Textiles Pvt Ltd", "Shree Varada Textiles"] in m["pairs"]
    assert m["propose"] is False  # 50%, and under 20 matched
    small = compute(_spec(), _files(["Ravira Exports", "Shree Varada Textiles"]))[0]
    assert small["share"] == "1.0000" and small["propose"] is False  # 100% of 2 proves nothing
    many = [f"customer {i:02d}" for i in range(24)] + ["Unknown One"]
    (m,) = compute(_spec(), _files(many))
    assert (
        (m["matched"], m["total"]) == (24, 25) and m["share"] == "0.9600" and m["propose"] is True
    )


def test_by_identity_and_a_joined_pair_is_not_proposed():
    vals = [f"27aaa{i:02d}" for i in range(25)]
    (m,) = compute(_spec("identity"), _files(vals))
    assert m["grade"] == "identity" and m["matched"] == 25 and m["propose"] is True
    (m,) = compute(_spec("identity", joined=True), _files(vals))
    assert m["propose"] is False
    assert compute(_spec(), {"erp_db": {"entities": {"customer": ROWS}}}) == []
