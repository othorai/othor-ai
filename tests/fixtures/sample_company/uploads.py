# SPDX-License-Identifier: Apache-2.0
"""The owner's files for the Upload step (docs/11 "Uploads"), generated from the sample company.

- MIS-Weekly-27Oct.xlsx: the MIS workbook (tabs Sales with GST, Receivables, Notes, Old-2024)
- Board-review-Q2-FY27.pdf: a text PDF, goals on slide 4
- Sales-dashboard-Oct.png: a dashboard screenshot (an image: read only with the model)
- receivables_report.sql: the receivables SQL (the 60-day filter on line 12)
- sales_dw data dictionary.pdf: a text PDF with field definitions and business rules

All text and figures come from the generator and company constants; nothing is hand-typed.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.fixtures.sample_company import company as co
from tests.fixtures.sample_company.generate import SampleCompany

ROOT = Path(__file__).resolve().parents[3]
FONT = ROOT / "othor/web/static/fonts/ttf/Inter-400.ttf"
FONT_BOLD = ROOT / "othor/web/static/fonts/ttf/Inter-600.ttf"

MIS = "MIS-Weekly-27Oct.xlsx"
BOARD = "Board-review-Q2-FY27.pdf"
DASH = "Sales-dashboard-Oct.png"
RECV_SQL = "receivables_report.sql"
DICTIONARY = "sales_dw data dictionary.pdf"
ALL = (MIS, BOARD, DASH, RECV_SQL, DICTIONARY)


def _num(v: str) -> object:
    t = v.replace(",", "")
    try:
        return float(t) if "." in t else int(t)
    except ValueError:
        return v


def write_mis(company: SampleCompany, path: Path) -> Path:
    from openpyxl import Workbook

    doc = next(d for d in company.sheets.values() if d["properties"]["title"] == "MIS-Weekly")
    wb = Workbook()
    wb.remove(wb.active)
    for sheet in doc["sheets"]:
        ws = wb.create_sheet(sheet["properties"]["title"])
        for i, row in enumerate(sheet["values"]):
            ws.append([v if i == 0 else _num(v) for v in row])
    wb.properties.creator = co.PEOPLE["finance"]["name"]
    wb.save(path)
    return path


def _pdf(pages: list[tuple[str, list[str]]], path: Path, title: str) -> Path:
    from fpdf import FPDF

    pdf = FPDF(orientation="L", format="A4")
    pdf.set_creation_date(dt.datetime(2026, 10, 27, 9, 0, tzinfo=dt.UTC))  # same bytes each run
    pdf.add_font("Inter", "", str(FONT))
    pdf.add_font("Inter", "B", str(FONT_BOLD))
    pdf.set_title(title)
    for heading, lines in pages:
        pdf.add_page()
        pdf.set_font("Inter", "B", 22)
        pdf.cell(0, 16, heading, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Inter", "", 14)
        for line in lines:
            pdf.multi_cell(0, 9, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))
    return path


def write_board(path: Path) -> Path:
    g = co.GOALS_FY27
    crore = g["net_revenue"] // 10_000_000
    pages = [
        ("Board review · Q2 FY27", ["Indigo Thread Co. · prepared by Leela Tandon, Finance"]),
        (
            "Highlights",
            [
                "Export demand from EU garment makers held up in Q2.",
                "Coimbatore dispatch times widened in September.",
            ],
        ),
        ("Markets", ["EU and US export; domestic trade in the South and West."]),
        (
            "Goals FY27",
            [
                f"Net revenue: ₹{crore} Cr in FY27, by 31 Mar 2027.",
                f"Gross margin per metre above ₹{g['gross_margin_per_m']:.0f}.",
                f"DSO under {g['dso_days']} days by 31 Mar 2027.",
            ],
        ),
        (
            "Priorities",
            ["1. Recover South accounts.", "2. Order book quality.", "3. Receivables discipline."],
        ),
    ]
    return _pdf(pages, path, "Board review Q2 FY27")


def write_dictionary(path: Path) -> Path:
    filler = ["Field definitions continue on the next page."]
    pages: list[tuple[str, list[str]]] = [("sales_dw data dictionary", ["Owner: data team · v3"])]
    pages += [(f"Section {i}", filler) for i in range(2, 7)]
    pages += [
        (
            "Invoices (page 7)",
            [
                "fact_invoice.net_value: invoice value in ₹; net_value already excludes GST.",
                "Rebates are booked in erp_db cost_ledger account 4410 and reduce net revenue.",
                "status X means cancelled; cancelled invoices are left out of every report.",
            ],
        ),
        ("Customers (page 8)", ["EXP in dim_customer.region means Export."]),
    ]
    pages += [(f"Section {i}", filler) for i in range(9, 12)]
    pages += [
        (
            "Dispatch (page 12)",
            [
                "Metres shipped are in sales_dw.fact_dispatch.dispatch_mtrs.",
                "Coimbatore records dispatch in yards (uom YD); 1 yard = 0.9144 metres.",
            ],
        )
    ]
    return _pdf(pages, path, "sales_dw data dictionary")


def write_dashboard(path: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1200, 700), "white")
    d = ImageDraw.Draw(img)
    big = ImageFont.truetype(str(FONT_BOLD), 30)
    small = ImageFont.truetype(str(FONT), 20)
    d.text((40, 30), "Sales dashboard · October", font=big, fill="#1F2A37")
    panels = [
        "Order book value",
        "Orders booked",
        "Win rate",
        "Average order value",
        "Dispatch time",
        "Export share",
        "Active customers",
        "Returns rate",
        "Collections",
    ]
    for i, name in enumerate(panels):
        x, y = 40 + (i % 3) * 380, 110 + (i // 3) * 190
        d.rectangle((x, y, x + 350, y + 160), outline="#DDE2E8", width=2)
        d.text((x + 20, y + 20), name, font=small, fill="#5B6B7B")
        d.text((x + 20, y + 70), "by region · stage", font=small, fill="#8A96A3")
    img.save(path, format="PNG", optimize=False)
    return path


RECEIVABLES_SQL = """\
-- receivables_report.sql · Finance · used for the weekly ageing tab in the MIS
-- Receivables over 60 days, in ₹ and as a share of open receivables.
-- erp_db (MySQL). Amounts exclude GST.
SELECT
  c.region,
  SUM(CASE WHEN r.paid_date IS NULL THEN r.amount - r.paid_amount ELSE r.amount END) AS open_amount,
  SUM(CASE WHEN DATEDIFF(CURRENT_DATE, r.due_date) > 60
           THEN r.amount - r.paid_amount ELSE 0 END) AS over_60
FROM erp_db.receivables r
JOIN erp_db.customers c ON c.customer_id = r.customer_id
WHERE r.status = 'O'
  AND DATEDIFF(CURRENT_DATE, r.due_date) > 60
GROUP BY c.region;
"""


def write_sql(path: Path) -> Path:
    path.write_text(RECEIVABLES_SQL)
    return path


def write_uploads(company: SampleCompany, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    return [
        write_mis(company, out / MIS),
        write_board(out / BOARD),
        write_dashboard(out / DASH),
        write_sql(out / RECV_SQL),
        write_dictionary(out / DICTIONARY),
    ]
