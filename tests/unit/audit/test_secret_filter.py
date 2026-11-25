# SPDX-License-Identifier: Apache-2.0
"""0.35: the Audit log never shows a key named like a password, token, secret or key."""

import re

from othor.audit import view

LEAKY = {
    "password": "x",
    "db_password": "x",
    "passwd": "x",
    "token": "x",
    "refresh_token": "x",
    "client_secret": "x",
    "api_key": "x",
    "key": "x",
    "Credentials": {"a": 1},
    "name": "Order tracker",
    "nested": {"secret": "x", "ok": 1, "list": [{"token": "x", "fine": 2}]},
}


def test_safe_drops_secret_keys_at_any_depth() -> None:
    got = view.safe(LEAKY)
    assert got == {"name": "Order tracker", "nested": {"ok": 1, "list": [{"fine": 2}]}}


def test_details_never_show_secret_values() -> None:
    rows = view.details(LEAKY, {"api_token": "abc", "host": "db"})
    text = repr(rows)
    assert "x'" not in text and "abc" not in text
    assert ("Host", "", "db") in rows and ("Name", "Order tracker", "") in rows


DENY = re.compile(r"^(ref|id|.*_id|fingerprint|.*hash|key|.*_key)$", re.I)
CODE = re.compile(r"^[\w.-]+:[\w:./-]+$")

# shapes the events really carry (dev data, 24 Nov 2026)
REAL = [
    (None, {"status": "sent", "ref": "alert:ontology_waiting"}),
    (None, {"recipient": "aa9eb630-26d2-491b-871a-226672fb7da3", "status": "sent"}),
    (None, {"source": "template", "guard": "not-needed"}),
    (None, {"number": 61, "status": "done", "input_hash": "e78282c1", "run_hash": "b7216677"}),
    (None, {"kind": "areas", "fingerprint": "areas:70a5df7c", "prompt": "metric_areas@v1"}),
    (None, {"scan_id": "2f0f8a62-d2bd-4d16-936f-813004826842"}),
    ({"state": "needs_confirmation"}, {"state": "confirmed"}),
    ({"use": "ignored", "set_by": "scan"}, {"use": "metric_source", "record": "3a8f"}),
    (None, {"record": "breakdown:season", "suggestion": "k1", "key": "x", "job": "j"}),
    (None, {"redrafted": ["order_tracker_sheet"], "added": ["metric:deal_value"]}),
]


def test_details_show_no_internal_key_or_code() -> None:
    for before, after in REAL:
        for label, was, now in view.details(before, after):
            assert not DENY.match(label.replace(" ", "_")), label
            for v in (was, now):
                assert not any(CODE.match(x.strip()) for x in v.split(",")), (label, v)
                assert "-4" not in v and "hash" not in label.lower()
    assert view.details(None, {"status": "sent", "ref": "alert:ontology_waiting"}) == [
        ("Status", "", "Sent")
    ]
    assert view.details(None, {"scan_id": "2f0f8a62-d2bd-4d16-936f-813004826842"}) == []
    assert ("State", "Waiting for you", "Confirmed") in view.details(*REAL[6])


def test_model_results_are_plain() -> None:
    assert view.model_result("passed") == "kept"
    assert view.model_result("failed:numbers") == "set aside by the numbers check"
    assert "Used" not in {view.model_result(g) for g in ("passed", "failed:x", "refused", "error")}
