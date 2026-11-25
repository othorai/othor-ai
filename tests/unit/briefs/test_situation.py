# SPDX-License-Identifier: Apache-2.0
"""The sentences under a brief's title (the Situation): each item once (24 Nov 2026, the daily
100-night test: on 17 of 101 nights item 1 was said twice when the title led with another item)."""

from __future__ import annotations

from othor.briefs.assemble import situation_text


def _item(fp: str, title: str, detector: str = "changepoint", **slots) -> dict:
    return {
        "fingerprint": fp,
        "title": title,
        "detector": detector,
        "slots": slots,
        "text": {"title": title},
    }


CASH = _item("a", "Cash balance stepped up in the week of 13 Jul")
ORDERS = _item("b", "Order book stepped up in the week of 26 Oct")
GONE = _item("c", "Ravira Exports stopped invoicing on 9 Oct")


def test_title_led_by_another_item_says_item_one_once() -> None:
    # the title leads with what changed (ORDERS), so item 1 (CASH) opens the Situation, once
    head = "Order book stepped up in the week of 26 Oct."
    got = situation_text("", head, [CASH, ORDERS, GONE], [CASH, ORDERS, GONE])
    assert got.count("Cash balance stepped up") == 1
    assert got == (
        "Cash balance stepped up in the week of 13 Jul. Ravira Exports stopped invoicing on 9 Oct."
    )


def test_title_led_by_item_one_lists_the_others() -> None:
    head = "Cash balance stepped up in the week of 13 Jul."
    got = situation_text("", head, [CASH, ORDERS], [CASH, ORDERS])
    assert got == "Order book stepped up in the week of 26 Oct."


def test_a_net_hiding_gross_headline_isnt_followed_by_its_own_title() -> None:
    nhg = _item(
        "n",
        "South fell ₹1.4 Cr; West’s rise hid it",
        detector="net_hiding_gross",
        fall={"name": "South", "change": "-14000000"},
        rise={"name": "West", "change": "12100000"},
    )
    head = "Four customers now hold 62% of the order book."
    got = situation_text("", head, [nhg, GONE], [nhg, GONE])
    assert got.startswith("South fell ₹1.4 Cr while West’s growth hid it")
    assert "West’s rise hid it" not in got  # the same story isn't told twice
    assert got.endswith("Ravira Exports stopped invoicing on 9 Oct.")


def test_a_known_pause_goes_first() -> None:
    pause = "Skarfab is on a known pause to 1 Dec (your answer, 30 Oct)."
    head = "Cash balance stepped up in the week of 13 Jul."
    got = situation_text(pause, head, [CASH, ORDERS], [CASH, ORDERS])
    assert got == pause + " Order book stepped up in the week of 26 Oct."
