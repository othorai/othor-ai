# Changelog

Notable changes for people running Othor AI, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are the product's own
(the same number as the images and `/healthz`).

## [Unreleased]

## [0.32.0] - 2026-11-21

### Added
- Summary 1.1: a dark summary card at the top (the period's lead change, numbers that moved
  together and which leads which, from Sweep's stored pair and lead/lag results, the week a line
  shifted, and look-alike numbers with a one-click merge draft in the Business Model). Lines mark
  their high and low with values and hover values; a tiny starting value reads "from X to Y"
  instead of a huge %; rebounds and big jumps worded honestly; "No data yet" rows fold into one
  line. Template words without a model; every figure inserted by code.
- Business Model 2.4, the levels: the level card and checklist on the Business Model page (Must
  have, Good to have, Full potential), each missing item with what it unlocks and its sentence
  pre-filled in the chat; drafts found in the files and sources, each one Apply; onboarding ends on
  the level card; the sample company's complete Business Model as an example PDF.
- Chat 1.3: setup decisions open with Othor AI's suggested answer and why, with Apply and Suggest
  edits; no pick without a model. A definition that needs a new number (net revenue less rebates
  from a ledger account) proposes the number and the formula together, one Apply.

### Changed
- Morning Brief 2.4: a day with no finding says so plainly, then the ordinary picture from the
  brief's numbers (against the week or month before, the usual range, the largest and smallest
  part, goal progress), never worded as a finding; a quiet day with items still open says how many.
  On the page, the card, the PDF and the email.
- Weekly Digest 1.1: every issue carries stories from the ordinary numbers, busy week or quiet.
- Money: one rule everywhere (brief, goal lines, Sweep, email, PDF): three significant figures,
  the unit by size, no trailing zeros (₹9.8 L, ₹60,000, $46.2M), a range's ends in one unit.
- Pricing: each plan says who it is for; Hosted lists its modules one by one with their icons,
  the Free card the open core's; On-premise starts from Everything in Hosted, with custom AI
  models; the Hosted button reads "14-day trial".
- Privacy policy: the contributor licence agreement line (CLA Assistant records the GitHub
  username and signing time).

## [0.31.0] - 2026-11-21

### Added
- Forecasts (Enterprise Edition): a page of its own after Summary. What's ahead in plain words,
  each goal's chance of being met with its range, the next 4 weeks, any number's line with where
  it stops being safe to plan on (the company's existing ±35% setting), every number's next
  period, range, how far ahead it can be called and its track record, and what the forecasts
  assume. The nightly forecast step now stores each number 12 periods ahead (`horizon` in the
  run's forecasts.json) from the same fit, so every other reader keeps its numbers. Othor AI words
  the band and goal lines once per run after the run seals (prompt `forecasts@v1`, every figure
  checked); templates otherwise. The chosen window stays in the address. Licence feature
  `forecasts`.
- The line chart's `horizon` annotation ("can see to here", the range past it lighter).
- /llms.txt and /llm-info are live.

### Changed
- One forecast for every goal: the brief's tiles, goal lines, What's ahead, Discover and Forecasts
  read the v2 forecast, and one rule labels a goal (80% or more on track, 20% or less off track,
  at risk between).
- Sweep's checks as squares, always 12; External Signals' check ("Ext") is the 12th, grey where
  it isn't on.
- Since last week: "Also still open" for the open items not in today's brief; its chart from 5
  nights.
- Home: a brief whose top item already leads another card shows its own next item.

## [0.30.2] - 2026-11-21

### Changed
- Sweep: the checks are a block of squares, four across, with each check's short name, instead of
  the ring; always 12, External Signals' Outside last (grey where it isn't on, with how to turn it
  on). The card reads "Checks · 10 / 12".
- Number cards are back in light bordered cards with plain labels on Sweep, AI usage, the Archive
  and Big Picture.

### Fixed
- Home: after Run again now, a brief whose rerun made no issue shows that day's 06:00 issue instead
  of "No issue yet".

## [0.30.1] - 2026-11-21

### Changed
- Plain Facts is named **Weekly Digest** on screen, in the email subject and the PDF, and has its
  own side-menu item below Discover (Enterprise Edition); its pages move to `/weekly-digest`. The
  Home button is gone; Morning Brief's link and the Settings › Delivery switch stay.

### Fixed
- The licence signer now accepts `plain_facts`, so a licence can name the Weekly Digest; a test
  checks that every enterprise feature can be signed.

## [0.30.0] - 2026-11-20

### Added
- Plain Facts (Enterprise Edition): the week's business as a newspaper's data page, every Monday
  at the brief time, by email with the same page attached as a PDF, and in the app with an archive.
  Code picks the theme (a goal at risk, or the biggest move it can explain) and builds 3 to 5
  sections with a chart each, big-number callouts and the round-up (what Sweep found, what reached
  a brief, goals at risk, what's ahead). Othor AI writes the words in one call per issue, every
  figure checked; without a model the issue goes out in template words. On for every licensed
  company; Settings › Delivery switches it off. New tables `plain_facts_issue` and
  `plain_facts_choice` (migration `e0009_plain_facts`), in the export.
- Setup: the "Drafting from your files" box shows how long it has been working, and a calm note
  past 90 seconds.

### Removed
- The weekly digest email: Plain Facts carries its counts and lists in the round-up.

## [0.29.4] - 2026-11-20

### Added
- A new brief shows its first issue at once, made from last night's results ("From last night's
  results · first scheduled issue tomorrow"); nothing is emailed until Send now. The night's own
  issues and replay are unchanged.
- Switching on a Discover group computes its numbers right away: the group page says "Computing
  your collection · about a minute" and fills in without a reload; Try again if the run fails.
- Home: "Suggested by Othor AI", up to three cards from Discover and Look back, each with Review
  or Make this report and Not now; the rest a link away.

### Changed
- Switch on opens the group's page (from Discover or Home); what was just done shows in the dark
  toast with an outlined Undo.

## [0.29.3] - 2026-11-20

### Changed
- Look back (Enterprise Edition): a made report reads like a Discover group's page: the answer
  first in one band (look at next, biggest driver, what it means), headline numbers, a card per
  section with its chart, what Othor AI would have caught, a sortable table of the biggest moves
  behind the change, and how it was made. Reading, planned, running and failed share the same
  header; the PDF and the email follow the new order. Nothing new is computed and no new model
  call is made: the page shows what the report already holds.
- The green-to-cyan button is now for Big Picture only: Look back's Make this report and Make
  the plan, and Discover's Switch on, are the blue button; New brief's Add goal is the soft blue
  one; Ask about this group is the quiet outlined button, like the top bar's Ask Othor AI.

## [0.29.2] - 2026-11-20

### Changed
- A brief with picked numbers shows items on its own numbers first; findings on the numbers
  they're directly computed from fill the places left. Leadership (the default brief) still sees
  the whole company. A focused brief with nothing tonight says "Nothing new on {brief} ·
  {default} has n items".
- New brief picks only the numbers that answer the question (no padding to six), names what no
  number measures yet ("Not watched yet: dispatch time · Find it in Discover"), and offers a
  target you stated ("keep DSO under 45 days") as a draft goal in one click; the goal's figure
  comes from your own words. Prompt `brief_draft` v4.

## [0.29.1] - 2026-11-20

### Changed
- Toasts are a dark bar with white words, so they're noticed over the light top bar; every action
  in a toast (Undo, Retry, Refresh everything) is a small outlined button, never underlined text.
- The ⋯ menu at the top right: room around and between its rows, a hover background on each row,
  and a clearer shadow.

### Fixed
- After Create brief (and any other action done in place), the toast now leaves on its own and
  doesn't come back on Back: the page's `?created=` leaves the address.

## [0.29.0] - 2026-11-20

### Added
- Discover (Enterprise Edition): a switched-on group's page is a dashboard: headline numbers,
  the trend with what's ahead, where the change is, how the amount owed moved over the month,
  overdue by age, when money comes in, the parts on one scale, the goal, what Sweep found with
  the group's own cross-check, a sortable customer table and how it was made. Chips (window,
  split, compare with last year) change it in place.
- "What's happening here": a short write-up by Othor AI, made on the first open after a night
  and by Write summary / Rewrite; it sees the names of the numbers and their breakdowns with
  their totals, never rows; every figure in it is checked. Without a model, the same band from
  the computed results.
- Ask about this group: the Othor AI chat about one group.
- Collections drafts more: amount owed, over 60 days, customers paying late, new invoices,
  collected, credit notes and bank credits, split by customer, region and days past due, with
  the check "Bank credits match collections". Switched on in one Business Model version.
- Chart kits 1.0: shares stacked to 100%, a weekday heatmap, a goal bullet, small multiples, and
  a line whose forecast range reaches past the last actual.
- Breakdowns can be computed (an age band from the due date and the run's date); compiler m29.1.
- A new table, `discover_summary` (migration `e0008_discover_summary`), included in exports.

- Discover: Remove a group from its ⋯ menu. A group that's on is turned off in one Business Model
  version; it then leaves Discover and, if its tables still hold it, is offered again under
  Suggested with today's numbers (a group switched on before 0.29 gets the new dashboard this way).

### Changed
- DSO shows as "Days to get paid" on the dashboard, with "DSO" behind its (i).

### Fixed
- Every (i) opens in normal case, never in the capitals of the label beside it.

## [0.28.5] - 2026-11-20

### Added
- Morning Brief: "Since last week" at the foot of every brief (page, PDF, and a short version in
  the email): what was reported, what was fixed and how (back to normal, a known pattern, set
  aside), what's still open and for how long, better or worse, a chart of open items with each
  fix marked, and at most one suggestion (snooze a long-open item). All from stored nights: no
  model, no source read; replayed byte for byte.
- Sweep: the detectors as the first of four cards, a ring with each detector's line behind it.

### Changed
- Emails in the calm style: no card frame, section titles in caps, hairlines instead of boxes,
  one solid button.
- Toasts: quieter, over the top bar while they show, the action as quiet text; they leave on
  their own (6 s, 12 s with Undo). "Run again now" reads "Refresh everything".
- New brief: the question box as the chat's, suggestions with where each came from, no preview of
  tomorrow's brief, Change anything on one line.
- The brief's subject says how long an item has been open ("Still open, day 9 · …").
- Enterprise: External signals has its own page (To review, Connected, Watching for connections).

### Fixed
- A brief created today opens before its first issue and says when the first one arrives.
- An item that changed recently no longer leaves the brief's top items early.
- Enterprise: the External signals check no longer stops on a month with no value, and a stopped
  check says so and can be asked again.

## [0.28.4] - 2026-11-19

### Changed
- Morning Brief: the brief's name with an (i) that opens a short list (run, sources, how items
  are ordered, what changed since yesterday), the day and time under it; Question to ask today
  only at the foot, naming whom to ask even before a team is set up; one heading style; Snooze
  and Mute as icons that say what they do; the two item cards line up side by side.
- An item unchanged for a week steps out of the brief's top items into a "Still open" list; it
  comes back the night it changes. The opening paragraph names each item by its own title.
- Brief wording: one customer reads singular; the slowest tenth says up, down or about the same;
  forecast misses in the number's own unit and plain words; ranges written one way; metres whole.
- Summary in the calm style: quiet controls, theme-blue lines, the change on the right; money
  charts under ₹1 Cr drawn in ₹ lakh. Simple / Full detail uses the same switch.
- Ask Othor AI: answers as formatted text with a plain table; opens at its latest answer; each
  reply dated; a word that fits several numbers lists them; dates read "18 Nov 2026".
- Sweep: one History button; History shows the last 10 nights and more in place.
- Home: no Recently accepted list.

### Added
- Outside signals (Enterprise Edition): Turn off / Turn on per signal, and Check for links now.

### Fixed
- Suggest a goal says when a number's history is too short and reads the number named, instead
  of asking which number again.
- The 05:00 ops check counts a night done once any of its nightly runs sealed.
- Chart labels say a value once.

## [0.28.3] - 2026-11-19

### Fixed
- A number's history survives Business Model answers that don't change its values. Each series
  now has its own meaning (the total leaves out its breakdowns; a breakdown leaves out its name,
  cap and who is active): an answer such as the customer list, or a breakdown added, no longer
  restarts every series of the number. When a meaning truly changes, that series is read again
  in full instead of keeping only the last weeks. Forecasts wait only after a real change.
- Suggested answers on Home and in the chat set only the field they add on the record as it is
  when applied, never a copy of the whole record from the night they were found.

### Added
- `othor admin reread <slug> [--metric …]`: the next run reads those numbers again in full from
  the read-only sources (brings back history cut short before this release).

## [0.28.2] - 2026-11-19

### Changed
- Buttons: the page's main action stays the solid theme cyan; secondary actions that repeat on
  rows are soft cyan (`btn soft`: Home's Accept, Data sources' Manage); "Ask Othor AI" is a quiet
  AI button (white, a gradient edge) wherever it appears; an AI button that's off is plain grey.
- Top bars: no frames but the ⋯ menu's; Intelligence and System Health as plain text with thin
  dividers, their colour only in the ring and the count.

## [0.28.1] - 2026-11-19

### Added
- Sweep history (`/sweep/history`): every night since the first, a month at a time, from what
  each night stored (tests, new, open, resolved, money at stake, replay result); any past run
  opens at `/sweep?run=N`, not only the last seven.

### Changed
- A calmer look on every screen: tables without the grey header band, one card shape, roomier
  buttons (`--text-button`), a quieter side menu, onboarding and sign-in; numbers shown for
  information read label first with no box; Home leads with the morning's brief on one dark band.
- Sweep's words of time follow the run being read ("today", "yesterday" or its date).

## [0.28.0] - 2026-11-19

### Added
- Goals in the Business Model: a Goals section with each goal's state for the night, its chance
  and where the number is heading, and the briefs that watch it.
- Add a goal in one line in the Othor AI chat (prompt `goal_parse` v1): the owner's own figure
  and date are kept, the rest is suggested by code (targets from the company's own numbers:
  where it's heading, a stretch, last year's). One card with Apply and Suggest edits.
- Suggest a goal (no model) and a nightly Home question while a company has no goal.
- A goal's own chat changes it in a line, or removes it ("remove it", read by code).
- Setup drafts goals from targets quoted in uploaded files (prompt `goal_from_targets` v1),
  shown in the Business Model draft with their source and written at Confirm.

### Changed
- A fresh chat puts its reply box right under the opening; no closing line under the buttons.

## [0.27.5] - 2026-11-19

### Security
- Enterprise: MCP sign-in hardened (the consent screen shows where it sends you back and flags
  unrecognised apps; sign-in codes and refresh tokens can't be used twice; limits on client
  registration and token requests).

## [0.27.4] - 2026-11-19

### Fixed
- The brief's weather says Recovering only when a finding's own test ran and stopped firing; a
  finding closed because its check couldn't run is said plainly ("Two findings couldn't be
  checked tonight"). A same-day rerun says "resolved today". Runs sealed before 0.27.4 keep the
  old rule, so every run still replays byte for byte.

## [0.27.3] - 2026-11-19

### Added
- Ask: a `big_picture` tool returns the latest Big Picture report as stored (no model call).

### Changed
- Ask: the numbers tool says what each number is (a total for a period, a balance on a day, or
  worked out from other numbers), its period and its change on the last period.
- Data sources: Status shows the connection's health with when it was last read; a new Last
  updated column shows whether new data arrived.

## [0.27.2] - 2026-11-19

### Fixed
- System Health: Fresh says "daily sources" when it counts fewer than every source; Numbers no
  longer says "All N numbers computed" when some have no data yet; the System Health card in Ask
  carries each light's details.
- Intelligence's "Waiting on you" counts a check only while its question is open or set aside.

## [0.27.1] - 2026-11-19

### Changed
- No changes in the open core.

## [0.27.0] - 2026-11-19

### Added
- Enterprise: Othor MCP 1.0, the read-only tools for Claude, ChatGPT and other AI clients, with
  keys in Settings › MCP for AI and OAuth sign-in.
- Plug-ins can add an ASGI endpoint at an exact path (`Plugin.asgi`) and lifetime context
  managers (`Plugin.lifespans`); the tool gate's audit row takes extra fields; sign-in can
  return to the page that sent you there (a local path only).

### Security
- New dependency: `mcp==2.3.0` (the official MCP SDK) and its locked dependencies.

## [0.26.2] - 2026-11-18

### Added
- Ask answers four more questions from what the night computed: today's brief, System Health,
  Intelligence (how much Sweep can check, and what would raise it) and what's ahead.
- Questions for you: a pair question shows the two numbers' last 12 weeks side by side; a
  "which system is right" question shows the records as each source has them; both from the
  night's stored results, never read live or sent to a model.
- "More" on a question opens it alone in the same panel, with more rows and where it comes from.
- After Apply, the message says how Intelligence moves ("Intelligence 72% → 81%") when it does.

### Changed
- Every question carries Othor AI's suggestion by default, with Apply and Suggest edits only;
  no option lists and no "Needs you". Where Othor AI can't pick a side, its suggestion is
  "Skip for now": the question is set aside for a week and nothing changes.

## [0.26.1] - 2026-11-18

### Changed
- Onboarding's chats (Fix with Othor AI, the Conflict, the Sheets tab fix) work like the Othor AI
  chat: Othor AI's suggestion by default with Apply (its value computed, then applied in one
  click) and Suggest edits for anything else; no other choice buttons beside a suggestion.
- "My pick" reads "My suggestion"; blue is for buttons only in every chat (the owner's messages,
  badges, the avatar and links are neutral); Send is a normal-size button; no lines under the
  reply box.
- A change asked for in Ask is drafted at once in the Business Model chat, to review and apply.
- Business Model: the Tell Othor AI line went; "Chat to update" by the title opens the chat
  ("Update your Business Model"); provenance and history say "Othor AI chat".
- Home: Ask Othor AI left of Big Picture, which has its own icon. Look back: New report is the
  plain primary button.
- Data sources: Status shows the connection's health again; Last read is a chip, amber with the
  date the data stopped; the Newest data column went.

### Fixed
- The questions chat no longer shows the drafts note or a reply box once nothing is pending.

## [0.26.0] - 2026-11-18

### Added
- One Othor AI chat (`?panel=othor&subject=…`) for every purpose, opened in place from
  "Ask Othor AI" in every top bar: Questions for you as one message (Othor AI's picks, Needs you,
  one Apply = one Business Model version), Business Model changes (`ontology_change` v4, up to 5
  per message, each through the existing checks; today's values before and after read live),
  Home's proposals (answered from their evidence; Apply / Decline), and Ask v0.1.
- Ask v0.1 on a read-only tool registry (`othor.tools`, MCP-ready): numbers, metric, findings,
  sweep_coverage, business_model_history, questions; one gate (role, closed argument schemas, a
  `tool.called` event); prompts `ask_route` v1 and `ask_answer` v1; without a model, the
  question's words pick the tool.
- Live previews: a few real rows behind a choice, built by code from its names through the
  compiler's scope and run by the core's one reader (`othor.connectors.read`); sent `no-store`,
  never stored or sent to a model.
- A source's plain name (`source.display_name`, migration `0045_source_display_name`), suggested
  from its tables' labels and saved on Data sources › Manage.

### Changed
- The old Questions panel, the Business Model's change panel and Home's "Chat about this" panel
  are replaced by the chat; their old links open it.
- Onboarding's chats draw their drafts with the chat's parts.
- Home: Sweep as a table (Finding · Status · Used in · Impact); the Questions section removed.
- Data sources: Home's status words ("Updated", "Not updated since …") with a warning dot, and why
  a source has no newest date.
- AI usage: a menu item above Settings with this month's tokens (the side card removed).
- Discover: "Look again" is "Refresh suggestions".

## [0.25.0] - 2026-11-18

### Added
- Smooth lines on every chart (pages, emails, PDFs): a monotone curve through every value that
  never passes one; forecast bands curve with it; step charts stay stepped.
- Values on lines: each line's last value beside its name, and where they fit its highest, its
  lowest and a finding's own points (three a line, six a chart); hover any point on a page for its
  date and value.
- Settings › Charts: Smooth lines (on) and Values on lines (Key points · Off), a render-time choice
  that never enters a chart's spec, so stored results keep their bytes. Migration
  `0044_company_chart_style`.
- Every page's secondary actions in one ⋯ menu at the top right.

### Changed
- One type scale by role (label, meta, row, body, card, page, figure, lead); every size, line height
  and letter spacing in the CSS is a token, and a guard keeps it so.
- Home: the next four weeks sit under the briefs, titled "Next 4 weeks".
- Charts: the waterfall's unit and "axis from" no longer overlap; an end label slides along its line
  rather than sit on another.

### Fixed
- Enterprise Edition, Look back: a report made while the owner is on another page says so there,
  with its headline and Open.

## [0.24.2] - 2026-11-18

### Fixed
- Enterprise Edition, Look back: a finished report brings its top bar (PDF, Email, Regenerate)
  without a reload; the progress asks again at once when the owner comes back to the tab or page;
  a grouping named like the customer counts as the customer when none is marked (customers kept,
  lost and new, concentration and the budget now run); the PDF's sections flow instead of a page
  each.

## [0.24.1] - 2026-11-18

### Changed
- Enterprise Edition, Look back reports go deeper: every report opens with how much the number
  changed (years, months, halves); after the first results Othor looks further, up to four
  drill-downs inside the group that drove the change (picked by a model from options code lists,
  computed by code, frozen with the report so replay holds; without a model, the largest rise and
  fall). The outside check is the last section, an extra, never in the headline or summary.
  A suggested report plans every analysis the data can run. Apply sits on the steps' line.

### Fixed
- Look back on connected data: a growth question reads the sales number (it could read cash
  collected); a number worked out from others maps to the one it is built on and its subtracted
  parts count as deductions; a grouping on the number's own table is kept; no deductions section
  when nothing is deducted.

## [0.24.0] - 2026-11-18

### Added
- Enterprise Edition, Look back on connected data: the owner asks only a question; Othor picks a
  confirmed money number, the tables its confirmed join rules reach, its groupings and its
  deduction variant (stacked as minus; a filtered number only with the deductions its own filter
  reaches), counts the window's records and reads nothing until Apply. Then one read-only query
  (`othor/ontology/compiler/rows.py`: the compiler's scope, filter and personal-column guards, a
  500,000-row cap). The plan's Change switches to another number's records or to the confirmed
  numbers' monthly totals (no record read). Regenerate keeps the table; Read again reads anew.
- Suggested Look backs: up to three after each sealed night, from computed results only (a year or
  quarter closed, a finding open 8 weeks, a group's share that moved, a goal off track, the top five
  customers carrying more); the model only words the question, numbers from the evidence only.
  Make this report plans it; Dismiss is kept.
- What outside moved (with External Signals): each outside figure with a reason, tested on the
  report's own months (also the same month), its effect as a range in its own waterfall; the rest
  "tested, no link". On uploads too.
- What Othor AI would have caught: the nightly detectors replayed at each month-end on a connected
  report's records, the first month each finding would have fired.

### Changed
- Every Look back report's table is kept encrypted (DuckDB Parquet, AES-GCM, a per-company key);
  existing clear tables are sealed in place by the worker. Migration `e0004_look_back_source`.
- The analyses name the number in their sentences ("Net revenue went from …").
- External Signals' link test takes `min_lag` (the nightly test is unchanged, 1 to 6 months).

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
