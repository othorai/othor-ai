# Sample company: Indigo Thread Co. (fictional)

The generator behind `make seed`. It's pure and deterministic: `generate(seed, as_of)` returns
the same bytes every time (hash pinned in `expected.json`), and a later `as_of` never changes
earlier data. See `docs/11-testing.md` for the company and the planted patterns P1–P14 and
C1–C10. `expected.json` holds every planted figure, and `measures.py` measures them the way a
direct SQL check would.

| File | What |
|---|---|
| `company.py` | People, plants, items, the customer roster (named accounts from the boards) |
| `build.py` | The timeline: invoices, calibration, orders, dispatch, receivables, ledgers, cash, approvals, deals |
| `generate.py` | `generate()` and the as-of renderers for erp_db and sales_dw |
| `sheets.py` | MIS-Weekly and Order tracker FY27 as Sheets API JSON (`x_` fields aren't in the API) |
| `extras.py` | Personal-data tables (all values invented), backups, logs, fillers; `PERSONAL` lists every personal column |
| `large.py` | `make seed SCALE=large` volume (below) |
| `load.py` | Load into MySQL `erp_db`, PostgreSQL `sales_dw` and `data/sample/sheets/` |

## Scale

- **default** (`make seed`): about 20k invoice rows, seeded in about 20 s. This is what CI and the
  test suite use. Don't pad it.
- **large** (`make seed SCALE=large`): for performance and stress tests (M2 scan, M11). Every
  table the planted patterns use stays exactly as in the default seed (same data hash). It adds:
  - the ignored, backup and log tables at the scan boards' row counts (`etl_log` 1.2M,
    `stg_orders` 2.9M, …);
  - schema `perf`, with fact tables at board size (`fact_invoice` 1.2M, `fact_invoice_line`
    6.9M, `fact_order` 2.9M, `fact_dispatch` 3.1M), cycled from real rows with fresh keys;
  - schema `scan_stress`, with 4,000 small tables for the scan layout.

  Expect several GB in the sales_dw volume. A later default seed drops `perf` and
  `scan_stress` again. Screens show whatever the scan computes; nothing is tuned to a board count.

## Deliberate quirks (they are the test)

- **GST in the MIS (P9).** erp_db is net of GST: it charges 5% GST on domestic invoices and 0% on
  exports, in separate columns. The MIS-Weekly "Sales incl. GST" tab adds a flat 18% to all
  sales, which is how the board's October gap of ₹0.83 Cr arises (₹4.62 Cr × 18%). Before setup,
  this is a realistic cross-source contradiction (Needs confirmation). The GST rule confirmed in
  setup resolves it.
- **Coimbatore dispatch is in yards.** `fact_dispatch.dispatch_mtrs` holds yards when `uom = 'YD'`.
- **Two systems, one revenue.** sales_dw holds invoices from Apr 2021 and erp_db from Apr 2024.
  They overlap from Apr 2024; erp_db is the book of record.
- **Region names differ by source.** Tessora EU and Skarfab are `South` (sales office) in the
  databases but "EU export" in the Order tracker.
- **Order tracker · Open orders.** Title rows 1–2, header on row 3, data to row 88, TOTAL in row
  89, Customer merged in rows 4–5 and 40–41, blank column F, "Value (₹ L)", mixed date formats.
- **Order tracker · tabs.** Six tabs, as in docs/11: Open orders, Deals (1,980 closed deals,
  312 with no created date), Activities, Products, Contacts (excluded, personal data) and Summary
  (ignored, pivot of Open orders). There is no Notes tab here; the only Notes tab is MIS-Weekly's
  (ignored, free text). Board 10b's tab list is illustrative; the picker shows these real tabs.
- **Boards are illustrative.** Where a board's figure differs from this data, the data wins and
  the UI review records it as a sample-data difference (CLAUDE.md).
- **Personal data** in `customers.contact_*`, `public.employee`, `crm_contacts`,
  `invoice_contacts` and the Contacts tab. It's never read by Othor AI.
- **Board 5a says "metres are flat" (P6).** In this data metres rise about 20% after 8 Sep. The planted
  fact is that freight decoupled from metres (r 0.88 → 0.31) and freight per metre is up 19%.
  Finding text is built from computed values, never copied from a board.
