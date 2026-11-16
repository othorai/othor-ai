# SPDX-License-Identifier: Apache-2.0
"""Chart specs for the /_components pages and the golden chart tests.

The headline values are the Indigo Thread sample company's (run of 1 Nov 2026): monthly net
revenue, P1 contributions (−1.40 / +1.21 / −0.19), P2 step (9% → 14% on 14 Aug), P3 shares
(48% → 62%), P7 quantiles (p10 1.6, p90 7.9). Weekly shapes and bands are illustrative
stand-ins until the engine computes them (M5–M7). In the product, every spec is built by the
engine from computed results; nothing here is shown outside /_components and chart tests.
"""

from __future__ import annotations

from othor.charts.spec import (
    Annotation,
    Axis,
    Band,
    Category,
    ChartSpec,
    Period,
    Series,
    Unit,
    YAxis,
)

CR = Unit(type="currency", code="INR", scale="cr", decimals=2)
PCT = Unit(type="percent", decimals=0)


def _weeks(start: str, values: list[float]) -> tuple[tuple[str, float], ...]:
    import datetime as dt

    d0 = dt.date.fromisoformat(start)
    return tuple(((d0 + dt.timedelta(weeks=i)).isoformat(), v) for i, v in enumerate(values))


def demo_specs() -> dict[str, ChartSpec]:
    tile_dates = [
        "2026-05-31",
        "2026-06-30",
        "2026-07-31",
        "2026-08-31",
        "2026-09-30",
        "2026-10-31",
    ]
    rev = [4.75, 4.90, 4.75, 4.46, 4.81, 4.62]
    specs = {
        "sparkline_below": ChartSpec(
            id="demo:tile:net-revenue",
            kind="sparkline",
            title="Net revenue · monthly",
            unit=CR,
            period=Period("month", "2026-05-01", "2026-10-31"),
            series=(
                Series("rev", "Net revenue", "negative", tuple(zip(tile_dates, rev, strict=True))),
            ),
            band=Band(
                "rev",
                0.8,
                tuple(zip(tile_dates, [4.6, 4.7, 4.75, 4.65, 4.85, 4.9], strict=True)),
                tuple(zip(tile_dates, [5.0, 5.1, 5.15, 5.05, 5.25, 5.3], strict=True)),
            ),
            alt_text="Net revenue, monthly, May–Oct 2026: ₹4.62 Cr in October, below the band.",
        ),
        "sparkline_neutral": ChartSpec(
            id="demo:tile:cash",
            kind="sparkline",
            title="Cash · monthly",
            unit=CR,
            series=(
                Series(
                    "cash",
                    "Cash",
                    "neutral",
                    tuple(zip(tile_dates, [1.6, 1.9, 1.7, 2.0, 2.2, 2.1], strict=True)),
                ),
            ),
            alt_text="Cash, monthly, May–Oct 2026: ₹2.10 Cr at the end of October.",
        ),
        "sparkline_on_track": ChartSpec(
            id="demo:tile:dso",
            kind="sparkline",
            title="DSO · monthly",
            unit=Unit("days", decimals=0),
            series=(
                Series(
                    "dso",
                    "DSO",
                    "positive",
                    tuple(zip(tile_dates, [52, 51, 50, 49, 47, 46], strict=True)),
                ),
            ),
            alt_text="DSO, monthly, May–Oct 2026: 46 days in October, on track for 45.",
        ),
        "bars_contribution": ChartSpec(
            id="demo:p1:net-gross",
            kind="bars_contribution",
            title="Net revenue change by region · Oct vs Sep",
            unit=CR,
            x=Axis("category"),
            categories=(
                Category("South", -1.40, "negative"),
                Category("West", 1.21, "positive"),
                Category("Total", -0.19, "neutral", total=True),
            ),
            alt_text="Net revenue change, October vs September 2026: South −₹1.40 Cr, West +₹1.21 Cr, total −₹0.19 Cr.",
        ),
        "step": ChartSpec(
            id="demo:p2:receivables-60",
            kind="step",
            title="Receivables over 60 days · share, weekly",
            unit=PCT,
            period=Period("week", "2026-06-05", "2026-10-30"),
            y=YAxis("%", zero_based=True),
            series=(
                Series(
                    "over60",
                    "Over 60 days",
                    "negative",
                    _weeks("2026-06-05", [0.09] * 10 + [0.14] * 12),
                ),
            ),
            annotations=(
                Annotation(
                    "vline", at="2026-08-14", label="14 Aug", role="negative", style="dashed"
                ),
            ),
            alt_text="Receivables over 60 days, weekly share, Jun–Oct 2026: stepped from 9% to 14% on 14 Aug and stayed there.",
        ),
        "bars_share": ChartSpec(
            id="demo:p3:order-book",
            kind="bars_share",
            title="Top 4 customers' share of the order book",
            unit=PCT,
            x=Axis("category"),
            categories=(
                Category("Today", 0.62, "neutral", label="Top 4 · 62%"),
                Category("June", 0.48, "muted", label="48%"),
            ),
            alt_text="Top 4 customers' share of the order book: 62% today, 48% in June.",
        ),
        "gap_block": ChartSpec(
            id="demo:p4:ravira",
            kind="gap_block",
            title="Ravira Exports · invoices, weekly",
            unit=CR,
            period=Period("week", "2026-08-03", "2026-11-01"),
            series=(
                Series(
                    "inv",
                    "Invoices",
                    "neutral",
                    _weeks(
                        "2026-08-03", [0.13, 0.11, 0.14, 0.12, 0.12, 0.10, 0.14, 0.13, 0.12, 0.11]
                    ),
                ),
            ),
            annotations=(
                Annotation(
                    "region",
                    from_="2026-10-12",
                    to="2026-11-01",
                    label="no invoices",
                    style="dashed",
                ),
            ),
            alt_text="Ravira Exports, weekly invoices Aug–Oct 2026: none since 9 Oct.",
        ),
        "line_spread": ChartSpec(
            id="demo:p7:dispatch-spread",
            kind="line",
            title="Dispatch time, Coimbatore · days",
            unit=Unit("days", decimals=1),
            period=Period("week", "2026-08-01", "2026-10-31"),
            series=(
                Series(
                    "avg",
                    "average 4.1 d",
                    "neutral",
                    _weeks(
                        "2026-08-01",
                        [4.1, 4.0, 4.1, 4.2, 4.1, 4.1, 4.0, 4.1, 4.2, 4.1, 4.0, 4.1, 4.1, 4.1],
                    ),
                ),
            ),
            band=Band(
                "avg",
                0.8,
                _weeks("2026-08-01", [3.1] * 6 + [2.6, 2.2, 1.9, 1.7, 1.6, 1.6, 1.6, 1.6]),
                _weeks("2026-08-01", [5.1] * 6 + [5.9, 6.6, 7.2, 7.6, 7.9, 7.9, 7.9, 7.9]),
                source="p10-p90",
                role="negative",
                label="middle 80%",
            ),
            annotations=(
                Annotation(
                    "vline",
                    at="2026-09-12",
                    label="12 Sep · spread starts widening",
                    role="negative",
                    style="dashed",
                ),
            ),
            alt_text="Coimbatore dispatch time Aug–Oct 2026: average held at 4.1 days while the spread from the lowest to the highest tenth widened from 12 Sep.",
        ),
        "line_decoupling": ChartSpec(
            id="demo:p6:freight",
            kind="line",
            title="Freight per metre and metres dispatched · index",
            unit=Unit("ratio", decimals=0),
            period=Period("week", "2026-08-01", "2026-10-31"),
            series=(
                Series(
                    "metres",
                    "metres dispatched",
                    "neutral",
                    _weeks(
                        "2026-08-01",
                        [100, 103, 99, 101, 104, 100, 102, 101, 99, 100, 101, 100, 102],
                    ),
                ),
                Series(
                    "fpm",
                    "freight per metre +19%",
                    "negative",
                    _weeks(
                        "2026-08-01",
                        [100, 102, 99, 100, 103, 100, 108, 113, 116, 118, 119, 118, 119],
                    ),
                ),
            ),
            annotations=(
                Annotation(
                    "region",
                    from_="2026-09-08",
                    to="2026-10-31",
                    label="from 8 Sep · r 0.88 → 0.31",
                ),
            ),
            alt_text="Freight per metre rose 19% from 8 Sep 2026 while metres dispatched stayed level; correlation fell from 0.88 to 0.31.",
        ),
        "line_forecast": ChartSpec(
            id="demo:p1:forecast",
            kind="line",
            title="Net revenue against the forecast band · weekly",
            unit=CR,
            period=Period("week", "2026-08-08", "2026-10-31"),
            series=(
                Series(
                    "rev",
                    "Net revenue",
                    "neutral",
                    _weeks(
                        "2026-08-08",
                        [1.10, 1.14, 1.08, 1.31, 1.05, 1.09, 1.12, 1.15, 1.18, 1.10, 1.21, 0.96],
                    ),
                ),
            ),
            band=Band(
                "rev",
                0.8,
                _weeks(
                    "2026-08-08",
                    [1.02, 1.04, 1.03, 1.06, 1.05, 1.05, 1.06, 1.07, 1.08, 1.08, 1.09, 1.10],
                ),
                _weeks(
                    "2026-08-08",
                    [1.22, 1.24, 1.23, 1.27, 1.25, 1.25, 1.27, 1.28, 1.29, 1.29, 1.31, 1.32],
                ),
            ),
            annotations=(Annotation("point", at="2026-10-24", value=0.96, role="negative"),),
            alt_text="Weekly net revenue Jun–Oct 2026 against the 80% forecast band: the last week is below the band.",
        ),
    }
    return specs


# --- /_components/stress (M11a): the components under real-data stress ---------------------
# Long customer names, 24 headline numbers, very large and very small ₹ values, negatives and
# empty states, every value through `fmt`. Playwright checks nothing overflows at 390 and 1440.

LONG = "Sri Venkateswara Textiles and Garment Exporters Private Limited (Tiruppur Unit II)"
VALUES = [48e10, 1.2345e11, 9.9e9, 4.62e7, -1.4e7, 2.16e6, 4999, 41.6, 0.4, 0.04, -0.04, 0]


def stress_ctx() -> dict[str, object]:
    from othor.core import fmt

    tiles = []
    for i in range(24):
        v = VALUES[i % len(VALUES)]
        tiles.append(
            {
                "label": f"Net revenue after credit notes and rebates · {LONG[: 20 + i]}",
                "value": fmt.inr(v),
                "role": "negative" if v < 0 else "positive",
                "comparison": {"kind": "band", "text": f"{fmt.inr(v, sign=True)} vs band"},
                "stale": "as of Fri" if i % 7 == 0 else None,
                "spark": None,
            }
        )
    rows = {
        "headers": ["Customer", "Plant", "Last invoice", "Last 12 months"],
        "rows": [
            [LONG + f" #{i}", "Coimbatore", "Fri 9 Oct", fmt.inr(VALUES[i % len(VALUES)])]
            for i in range(12)
        ],
        "note": "Ranked by the last 12 months.",
        "more": 4000,
        "noun": "customers",
        "total": 4012,
    }
    cards = [
        {
            "id": "00000000-0000-0000-0000-00000000000" + str(i),
            "name": "Leadership and board review for " + LONG,
            "default": i == 0,
            "ready": i % 2 == 0,
            "time": "06:00",
            "title": f"{LONG} fell {fmt.inr(-1.4e7)} while {LONG} grew {fmt.inr(48e10)}",
            "scope": "All plants · " + LONG,
            "recipients": 4012,
            "ask": "Priya Menon, Head of Sales and Exports (South and West regions)",
        }
        for i in range(3)
    ]
    return {"tiles": tiles, "rows": rows, "cards": cards, "long": LONG}
