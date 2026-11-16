# SPDX-License-Identifier: Apache-2.0
"""Indigo Thread Co. (fictional): people, plants, items and the customer roster.

Every name here is invented. Named accounts come from the design boards (4d, 4d1, 5a, 10c);
the rest are generated from fixed word lists so the roster is the same on every machine.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field

import numpy as np

D = dt.date

# Full horizon: generated once, then cut at `as_of`, so a date's data never depends on as_of.
START = D(2021, 4, 1)
ERP_START = D(2024, 4, 1)  # erp_db is the book of record from Apr 2024 (overlaps sales_dw)
END = D(2026, 12, 31)
RUN_DATE = D(2026, 11, 1)  # the run the boards show (they print "Sat"; 1 Nov 2026 is a Sunday)

COMPANY = {
    "slug": "indigothread",
    "name": "Indigo Thread Co.",
    "website": "indigothread.example",
    "fiscal_year_start_month": 4,
    "currency": "INR",
    "timezone": "Asia/Kolkata",
}
OWNER = {
    "name": "Uday Kamath",
    "email": "uk@indigothread.example",
    "google_account": "uday@indigothread.example",
}
PEOPLE = {
    "owner": OWNER,
    "sales": {"name": "Priya Menon", "role": "Head of Sales", "code": "PM"},
    "finance": {"name": "Leela Tandon", "role": "Head of Finance", "code": "LT"},
    "operations": {"name": "Aditya Naik", "role": "Operations (Coimbatore dispatch)", "code": "AN"},
}
GOALS_FY27 = {"net_revenue": 600_000_000, "gross_margin_per_m": 42.0, "dso_days": 45}

REGIONS = ["South", "West", "North", "East", "Export"]
PLANTS = {"Surat": "SU", "Coimbatore": "CB"}
FAMILIES = ["Cotton", "Poly-cotton blends", "Linen blends"]
YARD = 0.9144  # metres per yard (Coimbatore dispatch is measured in yards)


def stream(seed: int, *keys: object) -> np.random.Generator:
    """An independent, stable random stream per (seed, keys).

    Adding a customer doesn't shift any other customer's draws.
    """
    digest = hashlib.sha256(repr((seed, *keys)).encode()).digest()
    return np.random.Generator(np.random.PCG64(int.from_bytes(digest[:16], "big")))


@dataclass
class Item:
    code: str
    description: str
    family: str
    plant: str
    rate: float  # ₹ per metre


def items() -> list[Item]:
    out: list[Item] = []
    weaves = ["Poplin", "Twill", "Sateen", "Dobby", "Oxford", "Chambray", "Voile"]
    base = {"Cotton": 108.0, "Poly-cotton blends": 96.0, "Linen blends": 142.0}
    n = 0
    for plant, prefix in PLANTS.items():
        for fi, family in enumerate(FAMILIES):
            for wi, weave in enumerate(weaves):
                if (wi + fi + (plant == "Coimbatore")) % 3 == 2:
                    continue
                n += 1
                rate = base[family] + 4.0 * wi - 3.0 * (plant == "Coimbatore")
                out.append(
                    Item(
                        code=f"{prefix}-{100 + n * 7:04d}",
                        description=f"{family} {weave} {58 + wi * 2}in",
                        family=family,
                        plant=plant,
                        rate=round(rate, 2),
                    )
                )
    return out


@dataclass
class Customer:
    code: str
    name: str
    region: str
    currency: str  # INR | EUR
    segment: str  # key | mid | small
    kind: str  # poisson | cadence
    value: float  # typical invoice value, ₹
    per_month: float = 0.0  # poisson: invoices per month (FY27 level)
    gap: tuple[int, int] = (5, 8)  # cadence: gap range in days (inclusive)
    first: D = START
    last: D | None = None  # last invoice date (exact for cadence customers with a stop)
    resume: D | None = None  # invoicing resumes on this date (Skarfab pause)
    status: str = "active"
    city: str = ""
    terms: int = 30
    surat_share: float = 0.6
    tags: set[str] = field(default_factory=set)


# Named accounts (boards). Ravira Exports invoices every 4–9 days for 3+ years (P4).
KEY = [
    Customer(
        "C0001",
        "Ravira Exports",
        "South",
        "INR",
        "key",
        "cadence",
        1_120_000,
        gap=(5, 8),
        first=D(2023, 9, 4),
        last=D(2026, 10, 9),
        city="Tiruppur",
        tags={"top4", "ktn"},
    ),
    Customer(
        "C0002",
        "Tessora EU",
        "South",
        "EUR",
        "key",
        "cadence",
        1_456_000,  # less often, larger: same natural revenue as at 6–9 days (M6, decided 5 Nov)
        gap=(10, 18),
        first=D(2022, 1, 10),
        last=D(2026, 10, 8),
        city="Rotterdam",
        terms=45,
        tags={"top4", "ktn"},
    ),
    Customer(
        "C0003",
        "Skarfab",
        "South",
        "EUR",
        "key",
        "cadence",
        760_000,
        gap=(7, 10),
        first=D(2022, 6, 6),
        last=D(2026, 10, 6),
        resume=D(2026, 12, 2),
        city="Aarhus",
        terms=45,
        tags={"top4", "ktn"},
    ),
    Customer(
        "C0004",
        "Merai Apparels",
        "West",
        "INR",
        "key",
        "poisson",
        520_000,
        per_month=4.0,
        first=D(2022, 4, 1),
        city="Mumbai",
        tags={"top4", "mehta"},
    ),
]

# P5: 14 customers still marked active, silent for 90+ days; reorder gaps lengthened first.
QUIET = [
    ("Shree Varada Textiles", "South", D(2026, 7, 12), 6_200_000),
    ("Tidewell Garments", "South", D(2026, 7, 2), 4_800_000),
    ("Anvika Knit Works", "West", D(2026, 6, 28), 4_100_000),
    ("Sanvi Weaves", "South", D(2026, 7, 20), 2_600_000),
    ("Kavira Knitwear", "South", D(2026, 7, 8), 2_400_000),
    ("Navika Fashions", "West", D(2026, 6, 30), 2_250_000),
    ("Korana Clothing", "East", D(2026, 7, 25), 2_100_000),
    ("Devana Apparels", "South", D(2026, 6, 22), 1_950_000),
    ("Inara Creations", "North", D(2026, 7, 16), 1_800_000),
    ("Maravi Linens", "South", D(2026, 6, 18), 1_700_000),
    ("Tarini Garments", "West", D(2026, 7, 29), 1_600_000),
    ("Nilavi Exports", "South", D(2026, 7, 4), 1_550_000),
    ("Vihana Textiles", "North", D(2026, 6, 26), 1_500_000),
    ("Charu Knit Fab", "South", D(2026, 7, 1), 1_450_000),
]

EUR_NAMES = [
    ("Solvik Textil", "Export"),
    ("Haus Brenner Stoffe", "Export"),
    ("Vela Moda Srl", "West"),
    ("Atelier Saône", "Export"),
    ("Van Rijt Stoffen", "West"),
    ("Moda Castella", "North"),
    ("Kaskaro Textil", "Export"),
    ("Avonmere Cloth Co", "West"),
    ("Kurlan Weave", "North"),
    ("Dravia Fabrics", "Export"),
    ("Mondelo Têxteis", "West"),
    ("Skerry Apparel", "North"),
    ("Kempen Linnen", "South"),
    ("Firnhang Tuch", "East"),
    ("Moselweber", "Export"),
    ("Tessuti Ombra", "Export"),
    ("Wattmeer Textil", "West"),
    ("Maison Vallin", "Export"),
    ("Castelino", "North"),
    ("Tatrava Cloth", "Export"),
    ("Bernalp Stoff", "West"),
    ("Dalmara Moda", "South"),
    ("Scheldra Weefsels", "Export"),
    ("Elbtal Garn", "North"),
    ("Dordelle Tissus", "Export"),
    ("Narvia Weave", "East"),
]
DOMESTIC_A = [
    "Sahvira",
    "Gomavi",
    "Aravika",
    "Kutra",
    "Pallira",
    "Mysara",
    "Tharo",
    "Ganvi",
    "Yamira",
    "Sindra",
    "Hamvi",
    "Kanvira",
    "Udavra",
    "Satvira",
    "Kosra",
    "Perivra",
    "Brahmavra",
    "Mahanvi",
    "Sabarvi",
    "Tungavra",
    "Kalvira",
    "Avadra",
    "Magvira",
    "Vidarva",
    "Konvira",
    "Saurava",
    "Doavra",
    "Bundra",
]
DOMESTIC_B = [
    "Garments",
    "Knitwear",
    "Apparels",
    "Fashions",
    "Clothing",
    "Textiles",
    "Creations",
    "Overseas",
    "Fabrics",
    "Wear",
]
CITIES = {
    "South": ["Tiruppur", "Coimbatore", "Bengaluru", "Chennai", "Kochi", "Erode", "Madurai"],
    "West": ["Mumbai", "Surat", "Ahmedabad", "Pune", "Vapi", "Bhiwandi"],
    "North": ["Ludhiana", "Delhi", "Noida", "Gurugram", "Jaipur", "Panipat"],
    "East": ["Kolkata", "Bhubaneswar", "Howrah", "Guwahati"],
    "Export": ["Hamburg", "Lyon", "Milan", "Antwerp", "Gothenburg", "Porto"],
}
STATE_CODE = {
    "Tiruppur": "33",
    "Coimbatore": "33",
    "Chennai": "33",
    "Erode": "33",
    "Madurai": "33",
    "Bengaluru": "29",
    "Kochi": "32",
    "Mumbai": "27",
    "Pune": "27",
    "Bhiwandi": "27",
    "Surat": "24",
    "Ahmedabad": "24",
    "Vapi": "24",
    "Ludhiana": "03",
    "Delhi": "07",
    "Noida": "09",
    "Gurugram": "06",
    "Jaipur": "08",
    "Panipat": "06",
    "Kolkata": "19",
    "Howrah": "19",
    "Bhubaneswar": "21",
    "Guwahati": "18",
}
# Mid-size domestic accounts that carry the P2 receivables batch (unpaid since mid-June).
P2_BATCH = ["C0060", "C0061", "C0062", "C0063", "C0064", "C0065"]


def customers(seed: int) -> list[Customer]:
    out = [*KEY]
    n = len(out)

    def code() -> str:
        nonlocal n
        n += 1
        return f"C{n:04d}"

    r = stream(seed, "roster")
    for i, (name, region, last, _target) in enumerate(QUIET):
        g0 = int(r.integers(6, 10))
        out.append(
            Customer(
                code(),
                name,
                region,
                "INR",
                "mid",
                "cadence",
                200_000,
                gap=(g0, g0 + 2),
                first=D(2022 + i % 2, 3 + i % 6, 3 + i),
                last=last,
                city=CITIES[region][i % len(CITIES[region])],
                tags={"quiet"},
            )
        )
    for i, (name, region) in enumerate(EUR_NAMES):
        out.append(
            Customer(
                code(),
                name,
                region,
                "EUR",
                "mid",
                "poisson",
                float(r.uniform(260_000, 340_000)),
                per_month=float(r.uniform(3.3, 3.9)),
                first=D(2021 + i % 3, 4 + i % 8, 1),
                city=CITIES[region][i % 4] if region != "Export" else CITIES["Export"][i % 6],
                terms=45,
                surat_share=0.45,
            )
        )
    # Mid domestic accounts (₹2–5 L invoices).
    mids = [
        ("Sahvira Garments", "West"),
        ("Gomavi Apparels", "South"),
        ("Aravika Fashions", "North"),
        ("Kalvira Textiles", "East"),
        ("Saurava Fabrics", "West"),
        ("Perivra Knitwear", "South"),
        ("Avadra Clothing", "North"),
        ("Konvira Overseas", "West"),
        ("Kanvira Creations", "South"),
        ("Magvira Wear", "East"),
        ("Hamvi Garments", "South"),
        ("Doavra Textiles", "North"),
        ("Vaivra Textiles", "South"),
        ("Palvra Garments", "South"),
        ("Bhavira Fabrics", "South"),
        ("Amaravira Apparels", "South"),
        ("Noyvra Weaves", "South"),
        ("Kollivra Knits", "South"),
        ("Siruvra Textiles", "South"),
        ("Moyvra Garments", "South"),
        ("Pamvra Fabrics", "South"),
        ("Chitvra Apparels", "South"),
        ("Manivra Mills", "South"),
        ("Thamivra Wear", "South"),
        ("Kodavra Textiles", "South"),
        ("Varavra Garments", "South"),
        ("Gadivra Knits", "South"),
        ("Ponnavra Fabrics", "South"),
        ("Suruvra Apparels", "South"),
        ("Agavra Weaves", "South"),
        ("Kabvra Clothing", "South"),
        ("Hemavra Mills", "South"),
    ]
    while len(out) < 59:  # pad so the P2 batch codes land on mid accounts
        out.append(
            Customer(
                code(),
                f"{DOMESTIC_A[len(out) % 28]} Trading",
                "South",
                "INR",
                "small",
                "poisson",
                42_000,
                per_month=3.0,
                city="Erode",
            )
        )
    for i, (name, region) in enumerate(mids):
        south = region == "South"  # South's mid accounts carry 30% of October's drop (P1)
        out.append(
            Customer(
                code(),
                name,
                region,
                "INR",
                "mid",
                "poisson",
                float(r.uniform(260_000, 340_000)),
                per_month=float(r.uniform(2.2, 2.6)) * (1.15 if south else 1.0),
                first=D(2021, 4 + i % 9, 1),
                city=CITIES[region][i % 4],
                tags={"south_mid"} if region == "South" else set(),
            )
        )
    used = {c.name for c in out}
    regions_small = ["South"] * 9 + ["West"] * 6 + ["North"] * 5 + ["East"] * 4
    k = 0
    while sum(1 for c in out if c.segment == "small") < 64:
        a = DOMESTIC_A[k % len(DOMESTIC_A)]
        b = DOMESTIC_B[(k * 7 + k // len(DOMESTIC_A)) % len(DOMESTIC_B)]
        k += 1
        name = f"{a} {b}"
        if name in used:
            continue
        used.add(name)
        region = regions_small[k % len(regions_small)]
        out.append(
            Customer(
                code(),
                name,
                region,
                "INR",
                "small",
                "poisson",
                float(r.uniform(40_000, 64_000)),
                per_month=float(r.uniform(3.5, 6.5)),
                first=D(2021 + k % 4, 1 + k % 12, 1) if k % 4 else START,
                city=CITIES[region][k % len(CITIES[region])],
            )
        )
    # Customers that really did leave, and are marked inactive (the P5 filter must skip them).
    for i, (name, region, last) in enumerate(
        [
            ("Bundra Textiles Old", "North", D(2025, 2, 14)),
            ("Satvira Exports", "West", D(2025, 6, 30)),
            ("Kosra Garments", "East", D(2024, 11, 20)),
        ]
    ):
        out.append(
            Customer(
                code(),
                name,
                region,
                "INR",
                "small",
                "cadence",
                60_000,
                gap=(9, 12),
                first=D(2022, 1 + i, 10),
                last=last,
                status="inactive",
                city=CITIES[region][0],
            )
        )
    for c in out:
        if not c.city:
            c.city = CITIES[c.region][0]
    assert len({c.name for c in out}) == len(out)
    return out


def gstin(seed: int, c: Customer) -> str | None:
    """A well-formed, fictional GSTIN: state + PAN-like + entity + Z + check. None for abroad."""
    state = STATE_CODE.get(c.city)
    if c.region == "Export" or state is None:
        return None
    r = stream(seed, "gstin", c.code)
    letters = "".join(chr(65 + int(x)) for x in r.integers(0, 26, 5))
    digits = "".join(str(int(x)) for x in r.integers(0, 10, 4))
    check = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"[int(r.integers(0, 36))]
    return f"{state}{letters}{digits}{chr(65 + int(r.integers(0, 26)))}{int(r.integers(1, 10))}Z{check}"
