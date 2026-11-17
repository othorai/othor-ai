# Changelog

Notable changes for people running Othor AI, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are the product's own
(the same number as the images and `/healthz`).

## [Unreleased]

## [0.23.0] - 2026-11-17

### Added
- Enterprise Edition: External Signals, public figures that may lead the business's own numbers:
  the rupee against the dollar, euro, pound and yen (worked out from the ECB's euro reference
  rates), the World Bank's monthly cotton price, and a state's monthly rainfall (a NASA POWER
  model estimate). Diesel (PPAC) is listed and off until PPAC's permission.
  - Reasons come from the Business Model's names, never rows; each candidate is tested on the
    company's own history (fitted on the earlier two thirds, confirmed on the later third, one
    false-discovery pool at q = 0.01). A link that holds is a Home proposal, "Outside link"; Apply
    writes a versioned record, with Undo.
  - Every night, a linked signal whose last month moved beyond its usual range is a finding in
    its own pool, and the brief's Outside section shows at most two, beside the four ranked items.
  - Data sources lists each feed, its source and terms, its status for the company and when it
    was last fetched. The instance fetches once a day from the three hosts itself; nothing of a
    company's is sent. The stored values are a run input, so replay never fetches.

### Changed
- The engine's one-finding-per-number rule applies within a detection pool; a plug-in check with
  nothing to test leaves no line in Sweep's coverage. Inside results are byte-identical.
- Charts: a phone's stacked dumbbell names get the row's width (they were cut short); the extra
  decimal for repeating values applies to small shares only.

## [0.22.0] - 2026-11-17

### Added
- Enterprise Edition: Discover, groups of numbers the business isn't watching yet, found in the
  connected sources by table and column names (Collections, Dispatch and fulfilment, Pricing and
  discounts, Customer health, Procurement), each with a twelve-month preview computed read-only
  from the source; Switch on adds its numbers to the Business Model as one version, with Undo.
- Edit brief: "Findings come from", the Business Model's areas, each on or off.
- Plug-in hooks in the open core: after a scan, after a run, on the minute; Home proposal kinds;
  stored run inputs that replay reads; separate detection pools; a plug-in record type's part of
  the spec; brief sections; menu badges; two more slots; extra outbound hosts, listed on System
  Health.

### Changed
- Look back and Discover list their reports and groups as cards, like the Morning Brief.
- Look back reads a file with a column for each year (FY24, FY25, FY26 …) as one amount by
  financial year; every analysis the file can run starts ticked; a plan that can't run says why.
- An error message no longer stays over the page: it closes on the next click or request.

### Fixed
- An open-core install could fail to export a company (an Enterprise Edition table was named
  in the export list).
- The image's stylesheet was built without the Enterprise Edition's templates, so some spacing
  was missing on its pages.

## [0.21.0] - 2026-11-17

### Added
- Enterprise Edition: Look back, a decision report from a file of past sales (what moved the
  total, returns, mix, customers kept and lost, value per customer, concentration, next year's
  budget), as a page, a PDF and an email.
- Emails can carry a file attachment (SMTP and Postmark relays).
- A source-host guard for shared installs (`OTHOR_SOURCE_HOST_GUARD`, off by default): a database
  source may not point at a private network address, checked at save and on every connection.

### Changed
- The Ontology is called the Business Model on every screen and in every email.
- Home's weather banner: a soft colour for each kind of day and a tick when all is clear.
- Columns with personal details are left out of uploaded files by name before any value is read.

## [0.20.0] - 2026-11-16

Othor AI has run as a hosted service since 0.1; this release makes the whole product something
you can run yourself.

### Added
- Self-hosting: the `othor` image, `compose.selfhost.yml` with Caddy for HTTPS (Let's Encrypt,
  or Caddy's own certificate authority or your certificate on an internal network), PostgreSQL
  16, a one-shot database migration, a backup script and [the guide](docs/self-host.md). One
  company per install.
- System Health: one button on Home opens every light, from the source databases through the
  night's run to the morning's briefs, each coloured by urgency, with a nightly replay check.
- What's ahead cards in every brief: each headline number's range ahead, with its line chart and
  a plain confidence word.
- Enterprise Edition: the `othor-ee` image, with several companies on one install under an offline
  licence key.

### Changed
- A brief's title leads with what changed since the previous one; the archive keeps one row per
  brief and day.
- Every time is shown in 12-hour AM/PM with the time zone.
- Sweep states its false-discovery control in plain words ("at most 1 in 100 could be chance");
  the rate and method sit behind an (i).
- The rising-variance chart labels its band "middle 80%".
- Emails and pages name the install's own support address, or the person who runs it.
