# SPDX-License-Identifier: Apache-2.0
"""The company's size, computed (#76, docs/plans/next-1126.md §3 step 1): net revenue over the
last 12 complete months from the run's series, in ₹ crore (another currency at the rate fixed per
release and written into the spec), the band, and each band number's computed figure. Code only,
the same output for the same inputs."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from othor.engine.store import Point
from othor.ontology import catalogue, size

RUN = dt.date(2026, 11, 1)


def months(n: int = 12, last: dt.date = dt.date(2026, 10, 1)) -> list[dt.date]:
    out = [last]
    while len(out) < n:
        p = out[-1]
        out.append((p.replace(day=1) - dt.timedelta(days=1)).replace(day=1))
    return sorted(out)


def spec(currency: str = "INR", **extra) -> dict:
    s = {
        "company": {"currency": currency},
        "metrics": {
            "net_revenue": {
                "name": "Net revenue",
                "status": "active",
                "unit": "inr",
                "kind": "flow",
                "headline": True,
                "family": "net_revenue",
                "breakdowns": ["customer"],
            },
            "cogs": {"name": "Cost of goods", "status": "active", "unit": "inr", "kind": "flow"},
            "active_customers": {
                "name": "Active customers",
                "status": "active",
                "unit": "count",
                "kind": "snapshot",
            },
        },
        **extra,
    }
    if currency != "INR":
        s["size"] = size.spec_rate(currency)
    return s


def revenue(per_month: Decimal, n: int = 12, metric: str = "net_revenue") -> list[Point]:
    return [Point(metric, "month", "", None, p, per_month) for p in months(n)]


def crore(n: str) -> Decimal:
    return Decimal(n) * Decimal("1e7") / 12


@pytest.mark.parametrize(
    ("yearly", "band"),
    [
        ("9.99", "under_10"),
        ("10", "10_100"),
        ("99.99", "10_100"),
        ("100", "100_1000"),
        ("1000", "over_1000"),
        ("0.5", "under_10"),
    ],
)
def test_the_band_from_twelve_months_with_its_edges(yearly: str, band: str) -> None:
    got = size.of(spec(), revenue(crore(yearly)), RUN)
    assert got is not None
    assert got["band"] == band
    assert Decimal(got["crore"]) == Decimal(yearly).quantize(Decimal("0.01"))
    assert got["months"] == 12 and got["through"] == "2026-10-01"
    assert got["metric"] == "net_revenue"


def test_an_incomplete_month_and_tonight_are_left_out() -> None:
    pts = revenue(Decimal("1e7"))
    pts += [Point("net_revenue", "month", "", None, dt.date(2026, 11, 1), Decimal("9e9"))]
    pts += [Point("net_revenue", "month", "", None, dt.date(2024, 1, 1), Decimal("9e9"))]
    pts[0] = pts[0]._replace(complete=False)
    got = size.of(spec(), pts, RUN)
    assert got is not None
    # the incomplete month is out, so the 12 complete ones reach back to the 2024 month
    assert got["from"] == "2024-01-01"


def test_fewer_months_are_annualised_and_too_few_give_no_band() -> None:
    got = size.of(spec(), revenue(Decimal("1e7"), n=8), RUN)
    assert got is not None and got["months"] == 8
    assert got["crore"] == "12.00" and got["band"] == "10_100"
    assert size.of(spec(), revenue(Decimal("1e7"), n=5), RUN) is None


def test_no_revenue_number_no_band() -> None:
    s = spec()
    del s["metrics"]["net_revenue"]
    assert size.revenue_metric(s) is None
    assert size.of(s, revenue(Decimal("1e7")), RUN) is None


def test_the_revenue_number_is_the_headline_one_not_a_variant() -> None:
    s = spec()
    s["metrics"]["mis_net_revenue"] = {
        "name": "Net revenue (MIS)",
        "status": "active",
        "unit": "inr",
        "family": "net_revenue",
    }
    s["metrics"]["sales_count"] = {"name": "Sales", "status": "active", "unit": "count"}
    assert size.revenue_metric(s) == "net_revenue"


def test_another_currency_converts_at_the_rate_fixed_in_the_spec() -> None:
    cat = catalogue.load()
    s = spec("USD")
    assert s["size"] == {
        "currency": "USD",
        "inr_rate": str(cat.rates["USD"]),
        "rates_as_of": cat.rates_as_of,
    }
    # $1.2 M a year at the fixed rate: ₹ crore = 1.2e6 × rate / 1e7
    got = size.of(s, revenue(Decimal("100000")), RUN)
    assert got is not None
    want = (Decimal("1.2e6") * cat.rates["USD"] / Decimal("1e7")).quantize(Decimal("0.01"))
    assert Decimal(got["crore"]) == want
    assert got["currency"] == "USD" and got["inr_rate"] == str(cat.rates["USD"])
    assert got["band"] == cat.band_for(want).key
    # a spec without the rate (not recompiled) gives no band, never a guess
    s.pop("size")
    assert size.of(s, revenue(Decimal("100000")), RUN) is None
    assert size.spec_rate("INR") is None  # an INR company's spec stays as it was


def test_the_figures_are_computed_from_the_series() -> None:
    pts = revenue(Decimal("4e7"))  # ₹48 Cr a year: ₹10–100 Cr
    for p in months():
        pts.append(Point("cogs", "month", "", None, p, Decimal("2.4e7")))
        pts.append(Point("active_customers", "month", "", None, p, Decimal(40)))
        # 12 customers: two big ones carry half, ten carry the rest; and the cap's "Other"
        for i in range(12):
            v = Decimal("1e7") if i < 2 else Decimal("2e6")
            pts.append(Point("net_revenue", "month", "customer", f"C{i:02d}", p, v))
        pts.append(Point("net_revenue", "month", "customer", "Other", p, Decimal("9e9")))
    got = size.of(spec(), pts, RUN)
    assert got is not None and got["band"] == "10_100"
    f = got["figures"]
    # the 10 largest: 2 × 1 Cr + 8 × 20 L = 3.6 Cr of 4 Cr a month
    assert f["top10_share"] == {
        "value": "0.9000",
        "count": 12,
        "more": True,  # the cap’s “Other” holds the rest: more customers than named
        "months": 12,
        "metric": "net_revenue",
    }
    assert f["gross_margin_pct"]["value"] == "0.4000"
    # a level (active customers) in it: the latest complete month
    assert f["revenue_per_active_customer"] == {
        "value": "1000000.0000",
        "month": "2026-10-01",
        "months": 1,
    }
    assert "collected_vs_invoiced" not in f  # no collections number: no figure
    assert got["parts"] == {
        "active_customers": "active_customers",
        "cost_of_goods": "cogs",
        "revenue": "net_revenue",
    }
    assert size.of(spec(), pts, RUN) == got  # same inputs, same output


def test_the_catalogue_loads_and_every_band_number_is_known() -> None:
    cat = catalogue.load()
    assert [b.key for b in cat.bands] == ["under_10", "10_100", "100_1000", "over_1000"]
    assert [b.start for b in cat.bands] == [0, 10, 100, 1000]
    for b in cat.bands:
        assert b.numbers and all(n in cat.numbers for n in b.numbers)
    for e in cat.numbers.values():
        assert all(p == "revenue" or p in cat.parts for p in e.parts())


@pytest.mark.parametrize(
    ("edit", "why"),
    [
        (("reason: Sales only help once the cash is in", "reason: 9 in 10 pay late"), "no numbers"),
        (
            ("      - new_customers\n", "      - new_customers\n      - made_up\n"),
            "unknown numbers",
        ),
        (
            (
                "build: {take: collections, over: invoiced}",
                "build: {take: collections, over: nope}",
            ),
            "nope",
        ),
        (
            (
                "unit: days\n    level: numbers\n    same: [stock_days",
                "unit: weeks\n    level: numbers\n    same: [stock_days",
            ),
            "unit",
        ),
    ],
)
def test_a_bad_edit_of_the_catalogue_fails_loudly(edit: tuple[str, str], why: str) -> None:
    raw = catalogue.FILE.read_text(encoding="utf-8")
    assert edit[0] in raw, edit[0]
    with pytest.raises(catalogue.CatalogueError, match=why):
        catalogue.parse(raw.replace(edit[0], edit[1], 1))
