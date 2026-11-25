# Changelog

Notable changes for people running Othor AI, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are the product's own
(the same number as the images and `/healthz`).

## [Unreleased]

## [0.39.0] - 2026-11-25

### Fixed
- Sweep: a lasting step in a number that isn't in rupees now puts at risk only the part of its linked rupees that the size of the step says, never all of them (#138). A share moves in points of the whole (scans away from the dealer's district 3.2% → 3.8% puts 0.6% of primary sales at risk, about ₹29 L of ₹48 Cr, not ₹48 Cr); a count, a duration or another number by how far it moved from where it was (orders 1,000 → 800 a week: 20%). It is never more than half the linked rupees. A step from nothing (a number that was 0) is still reported and says its money can't be sized; a step the way the number's goal wants puts no money at risk. The finding's "How is this calculated?" shows the linked rupees, the share and the limit. A small step can now fall under your floor for problems worth reporting. Earlier runs keep their figures.
- Replay: runs made before the Sweep changes of 24 and 25 Nov (the spread's start week, rupees at risk for a non-rupee step) and before the company's size was recorded replay byte for byte again; each run now records the versions of these checks it ran. The finding's "How is this calculated?" names the version of the check that made it.
- Sweep: a finding no longer closes just because more checks ran that night (#142). Adding breakdowns raises the number of checks, which raises the bar every finding must clear, so findings whose numbers hadn't changed closed overnight as "Cleared by data". Now a finding opens at the usual bar and, once open, stays open while it stays under a looser one (0.05 instead of 0.01); a new finding still needs the usual bar. One that does fall under it says so ("Below the bar: more checks ran, its numbers held", or "Weaker: below the bar to stay open"), and How is this calculated? shows both bars and how many checks ran. Nights before this change replay as they were.
- Goals: a goal in days keeps its own decimals: "at most 3.5 days" reads 3.5 d, and where it is heading 3.6 d, never "4 d" (#147).
- Goals: a goal on a count reads in whole numbers everywhere (the brief, Home, the Business Model, Forecasts and Discover): 2,27,200 target, heading for 2,09,773, never "2,27,200.0" (#148).
- Business Model: the Decided by Othor AI list names each record plainly ("Goal: First-year premium each month", "Number: Banca share"), never by its key (#149).
- Signed-in Home no longer fails on an install made from the image alone: PyYAML, which the numbers-for-your-size catalogue reads, is now a runtime dependency (it was only in the development set). CI now imports every shipped module with the production dependencies alone.

### Changed
- Pages stay fast with many companies signed in at once: each night's numbers are also kept in a small page index beside the run, so Sweep, a finding, the Business Model and Edit brief open a run that isn't in memory in milliseconds instead of rebuilding it (Sweep about 450 ms → 20 ms on the sample company). A run from before this release builds its index once, on its first page. The index is a cache: it is not part of the run's ledger or replay, and deleting it is harmless.
- othor.ai's web now runs as two processes, so one slow page no longer holds up the others. `othor web` can run several processes on one port: `--workers N` or `OTHOR_WEB_WORKERS` (default 1; othor.ai runs 2). The scheduler still runs only in `othor worker`.

## [0.38.0] - 2026-11-25

### Added
- External Signals (Enterprise Edition) has many more public series to test against your numbers: about 320 in all, up from 87. Suggested for you draws on them, each with one plain reason, and the chat adds one by name (“add rainfall in Mumbai”) to the nightly test; only links that pass the test are reported. The new kinds, each with its source credited:
  - the rupee against 29 currencies (up from 16), worked out from the European Central Bank's reference rates (free reuse with the source quoted), and the ECB's key interest rates (deposit facility, main refinancing);
  - overnight and 10-year government bond rates of large markets, from the OECD (CC BY 4.0), each named for what it is: "US overnight interbank rate (follows the Fed's policy rate)", never a policy rate; the RBI's repo rate as before;
  - 67 commodity prices (up from 25) from the World Bank's Pink Sheet (CC BY 4.0): crude oil, gas, coal, metals, fertilisers, grains, oils, sugar, cotton, rubber and more;
  - demand in export markets: retail sales, factory output, construction and producer prices in the EU and its member states (Eurostat, CC BY 4.0), retail sales, factory output and lead indicators of major markets (OECD, CC BY 4.0), and imports, industrial production and retail sales of markets in South and South-East Asia, the Gulf, Latin America and Africa (World Bank Global Economic Monitor, CC BY 4.0);
  - India's consumer prices, industrial production, exports, imports and share prices (OECD, CC BY 4.0);
  - electricity demand and generation, India's by source and large markets' demand (Ember, CC BY 4.0);
  - rainfall and temperature in 18 large Indian cities, beside the states' (NASA POWER, a model estimate; no restrictions on use, credit NASA LaRC POWER);
  - India's public holidays: working days in each month from the gazetted holidays for 2023 to 2027 (Department of Personnel and Training, built in, no download).
- Each public series is downloaded once for the whole install and shared by its companies; daily values older than five years are kept as the month's average.
- When a source can't be reached, External Signals says which month its stored values reach, and the test goes on with them.
- An install without internet access can take the same values from a file: `othor signals export <folder>` on a connected install, `othor signals import <folder>` on the other, with every file's checksum checked before anything is stored.
- data.gov.in can be read with the install's own free key (`OTHOR_DATA_GOV_IN_KEY`); no series uses it yet.
- othor.ai/learn/checks: a small picture for each check.

### Changed
- Settings: the tab where you choose the model is now called AI Model.

## [0.37.0] - 2026-11-25

### Added
- Any model: Settings › Othor AI lets the super admin or an admin choose the model Othor AI writes with. Pick a provider (Claude, OpenAI, Gemini, Azure OpenAI, Mistral, DeepSeek, Groq, Together AI, OpenRouter, Ollama on your own machine, or another model server such as LM Studio, vLLM or llama.cpp) and a model, for every kind of work at once or one kind at a time. Paste the company's own key: it is kept encrypted, shown afterwards only as "Key saved", and never logged or exported. Check this model asks it one question on made-up figures and scores the answer in place; Back to the usual model undoes the choice. Every change and check is in the Audit log.
- Work done on the company's own key or model server is outside the monthly AI allowance; it is still listed on AI usage.
- Chat answers and brief rewrites: the model now writes each figure as a placeholder and Othor AI puts the computed number in, so a right number can no longer appear under the wrong label ("3 were suppressed" when 3 were clean). A sentence with a number of its own is dropped, and each Action plan line may use only its own figures (#122).
- Each company's browser tab shows its own icon: its logo, or the first two letters of its name. When a logo won't read at tab size, Settings › Company › Logo suggests the letters, and the owner applies them.
- Suggested numbers for the company's size and goals (#76): from the last 12 months' net revenue Othor AI places the company in a size band and suggests the numbers companies of that size usually watch, the ones serving an off-track goal first, each with its figure and one plain reason. They show in the Business Model's To review, on Home's Suggested by Othor AI (with Not now and Undo) and under the checklist item they complete. Accept adds the number; one that needs another source offers Connect a source.
- Action plan and briefs link to each other (#110): Home's Action plan card names the briefs its lines come from, each line opens its own brief's Action plan, and a brief's Action plan leads back to today's plan across your briefs.
- Next 4 weeks opens Forecasts (#109): a week on Home opens that week on Forecasts, and the brief's What's ahead links each number to its chart.
- `make demo` ends with a one-time sign-in link (development only, 15 minutes, used once), and the demo company is in US dollars and New York time by default (`CURRENCY=INR` for rupees and India time).
- The README opens with a 30-second tour of the real app.
- othor.ai/demo: two new videos, People and Audit log.

### Fixed
- Sweep: a widening spread now says the week it really widened ("Dispatch time spread at Coimbatore tripled from 14 Sep"). Before, it always said the first week the check looks at (8 weeks before the night), so the date slid a week every week and could fall before the spread began; the rupees at risk run from the real start.
- A sentence from Othor AI that says a number moved the wrong way, or miscounts what ran, is dropped (chat) or keeps the template's words (brief) (#120).
- A number drafted from an uploaded workbook now finds its tab when the same workbook is connected as Google Sheets, instead of staying "No source" (#116).
- Times on partly refreshed parts of a page (People's Last signed in, Home's Updated line) use the company's own time zone (#125).
- People's suggestion to invite someone names each area in plain words, not its internal key (#126).
- README and CONTRIBUTING: on Windows, `make demo` needs a WSL 2 terminal (#127). `make seed` follows the sample databases' port settings, and `make dev` and `othor demo` print the ports in use.

## [0.36.0] - 2026-11-25

### Added
- External Signals: Othor AI suggests the outside numbers worth testing for your business, from the Business Model, each with one plain reason and the numbers it could lead: "Suggested for you" with Add to the test and Not now, a card on Home, and "what outside things affect my business?" in the chat. A catalogue of 87 public series (World Bank Pink Sheet commodities, ECB exchange rates, NASA POWER rainfall and temperature by state, the RBI repo rate), each with its licence and credit; only links that pass the test are reported. A new satellite-dish icon for External Signals.
- Settings › Company: rename the company in place; the web address stays the same; recorded in the Audit log.
- Blog: a post's pictures open full size on a click.

### Fixed
- The numbers check reads a date written "1 Nov 2026" against the 2026-11-01 it came from, so correct chat answers are no longer dropped (#123).
- The chat no longer takes the text "null" as a tool name (#119).
- The brief's (i) says how many sentences Othor AI reworded, instead of "rewritten" when nothing changed (#124).
- The company address is lowercased as you type (#115).
- The Audit log names the person removed from People.
- Names like TiO2, DSO and GST keep their capitals inside sentences.
- Look back's Read again reads today's years, not the old report's (#121).
- The website's editions table: the Audit log and its CSV are in every edition.

## [0.35.3] - 2026-11-25

### Fixed
- The numbers check on every model answer now reads numbers written as words: "Fourteen customers" is checked as 14, "twenty-one" as 21, "three hundred" as 300, so a number a model spells out can no longer reach a brief or the chat unless it was computed (#118). A lone "one" stays a word.

## [0.35.2] - 2026-11-25

### Changed
- Look back on young data: with fewer than two complete financial years, a report compares the latest complete months with the same months a year earlier (e.g. Jul–Oct 2025 against Jul–Oct 2026) instead of saying it can't tell. A connected read reaches the last full month when the older year is empty; reports made before keep their numbers; the budget still needs whole years.
- Discover: every group's dashboard, not only Collections, shows its numbers' parts on one scale, a weekday heatmap for a number kept by day, a sortable table by the first split (against the month before and the same month last year), and "By …" chips.

## [0.35.1] - 2026-11-24

### Fixed
- Google Sheets: a sheet whose dates are stored as text no longer fails its nightly read ("Cannot compare values of type VARCHAR and type DATE"); the date is converted in the window as it already was in the period.

## [0.35.0] - 2026-11-24

### Added
- Audit log: a page in the side menu (Setup, after AI usage) for the super admin and admins. Who did what, and when, newest first, in plain sentences: a person, Othor AI acting on someone's request ("Othor AI (asked by …)"), System or Othor support. The object is linked to its screen; a row with a before and after opens its details in place. Filters by who, kind and time, "Load more" in pages of 50, and Download CSV of the filtered rows. Othor AI's own drafting shows as "Othor AI wrote: …" with the task and its result, never the text.
- Othor AI chat: a read-only tool answers "who changed … last week?" from the same log.

### Changed
- Events now name the person by their user id where some actions wrote their email address; older rows still read as the person. A change Othor AI applies on someone's request is recorded as Othor AI acting for that person, and applying a draft Othor AI wrote says so.
- The export's `events.csv` gains plain "who" and "what" columns, worded as the Audit log shows them.

## [0.34.1] - 2026-11-24

### Changed
- Sweep: a lasting step in a number that isn't in rupees (truck fill, stock in kilograms, a count) now carries the rupees it puts at risk, through the number named as what it puts at risk (the same link the spread check uses), shown as "at risk". Before, such steps had no money and fell under the money floor. A number with no such link is unchanged.
- Business Model: a customer-like breakdown can say what one is called and what it does (for example partner banks that issue policies), so "still marked active but gone quiet" findings and their Action plan lines read in the business's own words. Without them, nothing changes.

## [0.34.0] - 2026-11-24

### Added
- People (Enterprise Edition): invite admins by email from Settings › People, with Resend, Revoke and Remove; the invitation's link opens a join page and a 6-digit code signs the person in. Othor AI suggests brief recipients who aren't in yet.

### Changed
- The person who set up the company is shown as the Super admin. Only the super admin can close the company, change the licence, export everything or remove people.
- The "Drafts only" note is no longer shown on any screen. Nothing Othor AI drafts changes anything until someone applies it, as before.

### Fixed
- Sign-in: after entering the code, the email step no longer shows again when the code form was sent twice (a paste or autofill, then Continue or Enter); it was most visible in Firefox.

## [0.33.13] - 2026-11-24

### Changed
- Morning Brief: a quiet night's title gives the count and how long ("Nothing new came up; 3 items from last week are still open, the largest for 12 days."); the items are listed below it.
- Action plan: a watch's name reads as a phrase ("dispatch time spread for Coimbatore", "the share of receivables over 60 days") and the limit as "above your limit of 1.6 days".
- Data sources: the table's cells stay on one line, and Tables, Schemas and Manage line up down the rows.

### Fixed
- Google Sheets: choosing a spreadsheet works in Edge and Firefox too ("The API developer key is invalid" before).
- Morning Brief: the sentences under the title no longer say the top item twice when the title leads with another one.
- Sweep: a widening spread still open keeps the start it was first reported with ("doubled from 17 Aug"), instead of a start that moved a week later every week.

## [0.33.12] - 2026-11-24

### Added
- `make demo`: one command from a fresh clone to a working sample company. It starts the app and its databases, loads Indigo Thread Co. into the sample MySQL and PostgreSQL, sets the company up as an owner would (sources connected read-only, Business Model confirmed), runs the night of 1 November 2026 and prints where to sign in. The README's "See it in 5 minutes" describes it.

## [0.33.11] - 2026-11-24

### Added
- Website: a long read, "Significant is not material": why a result that is unlikely to be chance can still be too small to act on, and how Othor AI's money floor keeps those out of the brief (the sample company's 1 November night, from 363 results to 8 findings).

## [0.33.10] - 2026-11-24

### Added
- Website: two posts, "Is this drop real, or just April?" (how a month is judged against its own time of year and the known patterns the owner has named) and "What the Morning Brief does between 2 and 6 AM" (one night, step by step).

### Fixed
- Data sources: a source with no rows on its usual quiet day (a sales warehouse on Sundays, a weekly sheet) reads Up to date instead of warning "Not updated since"; a gap on a usual day still warns.
- Local development: `make dev` finishes; the worker and the stylesheet watcher no longer run the web health check they can't pass.

### Fixed
- Data sources and Home: a source with no rows yesterday on a day it is usually quiet (a sales warehouse on Sundays, a sheet updated weekly) shows "Up to date" with its newest date, not a "Not updated since" warning. A gap on a usual day still warns.

## [0.33.9] - 2026-11-23

### Added
- Website: the other 24 posts from the old blog on othor.ai/history, revised and sourced: History of BI (Hollerith, Enigma, SABRE, Moneyball, Walmart, Netflix, Cambridge Analytica, ChatGPT and more), decision intelligence (von Neumann, the Berlin Airlift, Nash, Koopmans, Simon, decision support systems, interior-point methods and more) and COVID-19 dashboards. Every old blog.othor.ai address redirects to its own post. The history page has finer eras and compact rows.
- Website: links to othor.ai/compare from the home page's "A new BI" section and from /enterprise.
- Website: the 30 data visualizations post gains pictures for the parts that had none: public-domain and openly licensed originals with their credits, and charts drawn by Othor AI from public data, each saying so under it; Rosling's TED talk and NASA's Climate Spiral as videos that load only when played, and the Met Office's post on X quoted with its link.

### Changed
- Business Model: To review's Accept and Suggest edits at the row size. External Signals' review rows: Accept and Decline at the same size; Suggest edits removed there.
- Website: the blog's three history tracks are one "History" chip to othor.ai/history, and the chips wrap instead of breaking their words.
- Website: the blog posts list only the founder as their author; the technical paper's byline follows.
- Website: Learn › Home describes Home as it is now; proposals are on the Business Model page and questions in the Othor AI chat.

### Fixed
- Website: the open-source page and the Enterprise comparison no longer put Forecasts in both editions. The open core forecasts in every brief (What’s ahead); the Forecasts page is the Enterprise Edition’s. The comparison also lists the Weekly Digest.

## [0.33.8] - 2026-11-23

### Added
- Website: othor.ai/history, the history of intelligence: business intelligence, decision intelligence and data visualization on one timeline, by the year each story happened. Five posts from the old blog, revised and sourced: the Target pregnancy prediction, 30 data visualizations that changed the world, Kantorovich and linear programming, Bellman's principle of optimality, and Karmarkar's algorithm.
- Old blog.othor.ai addresses redirect permanently (301) to the new posts, the history page or the blog.

### Changed
- Business Model: To review at the top, with the proposals that were on Home, each with Accept and Suggest edits (which opens the chat on it); then tabs Business & Goals, Numbers, Names & Patterns and the level, its checklist open. The "to review on Home" button went.
- External Signals' proposals: Accept and Suggest edits.

### Fixed
- The same goal entered twice made two "at risk" proposals; it is now one, and a decision or Undo covers both.

## [0.33.7] - 2026-11-23

### Added
- Website: othor.ai/compare and a page for each of five other ways to know your numbers (BI with an AI copilot, chat AI on your data, AI-native analytics tools, automated insights and anomaly alerts, the analyst and spreadsheet way), each beside Othor AI: what it does well, where it stops, a side-by-side table, an example from the sample company's brief and a screenshot. Compared as of November 2026 from each vendor's public documentation, with a trademark line. Compare is in the Product menu and the footer.

## [0.33.6] - 2026-11-23

### Changed
- Home: Today's action plan sits below the briefs, above Suggested by Othor AI.
- Briefs: the Action plan comes last, just above Since last week, on the page, the read-only link, the emails and the PDF.
- The ⋯ menu at the top right on more pages: Sweep's History, and the Morning Brief list's Archive and Weekly Digest. On phones every ⋯ menu opens inside the screen.
- Tab bars wider than the page scroll sideways (mouse wheel or swipe), with a fade on the side that has more.
- The sub-processor register says Business Model, not Ontology, and lists Anthropic (Claude Code), which our engineers use to build and run the service.

### Fixed
- A number that reads a table set to ignored no longer stops the company's whole nightly run: that number is left out and shown as needing a source, with the reason. Setting a table to ignored now names the numbers that pause.

## [0.33.5] - 2026-11-22

### Added
- Website: an Action plan video on othor.ai/demo, right after Setup (54 s, the same voice and captions as the others).

## [0.33.4] - 2026-11-22

### Changed
- The Action plan's icon in the side menu is a flag (owner, 22 Nov 2026; was a tick).

## [0.33.3] - 2026-11-22

### Fixed
- External Signals: one genuine link among many candidates can now be suggested. The link test's
  p-value floor was 1/500 (499 permutations), so after the false-discovery pool over a company's
  candidate pairs (22 for the sample company) a single real link could never reach q ≤ 0.01. It
  now draws 9,999 seeded permutations, computed at once on ranks (same statistic, lags, held-out
  check and pool). Look back reports made before keep their method and replay byte for byte.

## [0.33.2] - 2026-11-22

### Changed
- What to do is now **Action plan** (owner, 22 Nov 2026): the side menu, the page at
  `/action-plan` (`/what-to-do` redirects, and links and holds made from it still return there),
  Home's "Today's action plan" card, and the section at the end of every brief, its emails and
  its PDF; Sweep says a hold was made "from the action plan".

### Added
- Action plan: Recently resolved, the last 30 days at the foot of the page. Each step that ended
  says how long it was open, when, and why: the numbers no longer show it, back on track, back
  inside its limit, explained by your answer; a step you muted or snoozed says so ("Muted 31 Oct").
  Home's card says how many resolved this week. Computed from the stored briefs; nothing new to tick.
- Website: the Action plan on Pricing (Free card), Product, Home, How it works, Learn (The Morning
  Brief) and llm-info.

## [0.33.1] - 2026-11-22

### Changed
- What to do: Watch opens in place, as a side panel, from a brief, Home and the What to do page
  (`?panel=watch`); Close and Start watching stay on that page. Brief cards have Watch beside
  Snooze and Mute, or "Watching · alert above …" once watched.
- What to do: figures in action sentences are bold; the person to ask is named once; the chosen
  brief tab is kept after a snooze, a mute or a panel; the dot before a brief chip is centred.
- Look back: value per customer names the group's size beside the best ("a median of ₹77.7 L
  across 11 customers"); a group under 5 customers is never called best, and reads "too few to
  call" on the card and its chart; a new groups table (Customers, median per customer); the same
  floor for "deductions rose the most".
- Contact us (website and the app's panel): no topic is chosen by default; the headline reads
  "How can we help?" until one is; links that name a topic open on it as before.
- othor.ai/demo: the External Signals video re-recorded so every scene matches its narration
  (the brief's Outside item, the External signals page with fetch times and a connection, Check
  now pressed, Home's suggestion); the invented sample company's links are staged for the
  video, the public figures are real.

### Fixed
- What to do: an action from a watch past its limit comes only from the brief's own numbers (a
  Dispatch brief no longer offers receivables or customer watches); a watch that follows one of
  the brief's findings folds into that finding's action.
- What to do: the watch sentence never reads "12%, above its 12% limit" (decimals are added
  until value and limit differ), the number's name is lower case mid-sentence, money and days
  in their units.
- What to do: a quiet brief offers at most one action from the largest finding still open for a
  week ("Still open · N days").
- What to do: plainer template sentences per check (no "call them", no capital after a colon,
  the item's title not repeated).
- Briefs: a headline number that misses its band by less than the rounding reads "in band",
  never "−0% vs band"; "close in 1 day" and "close today".
- Sweep and briefs: a step change's note reads "it has held since the week of …" (the date was
  missing). Replays of runs sealed before 0.33.1 show this one line differing.
- Look back: a report made from a Suggested Look back no longer fails with "The confirmed
  numbers have no night run to read from yet." (it was never given its years); each cause now
  has its own message.

## [0.33.0] - 2026-11-22

### Added
- What to do (#110): every brief ends with up to three actions (page, read-only views, both
  emails, PDF), picked by code from the brief's items, an off-track goal and breached watches,
  stored in the issue body (replay reproduces them); related findings fold into one action;
  template sentences per check, goal and watch; Othor AI's wording (`brief_rewrite` v2) is kept
  only where its figures match the template's.
- Home's "What to do today" card and a What to do page (side menu, after Home): every action
  across today's briefs once, tabs per brief in place, Start here · Also today · Nothing to do,
  and "N snoozed or muted · See them in Sweep →".
- Snooze, Mute and Watch on What to do rows and brief cards; `also=` holds every finding of a
  folded action at once; `back` returns to a brief, What to do or Home; a finding already
  watched (matched by the number watched, not only the finding) shows "Watching · …".
  Watch from these pages opens Sweep's Watch panel, which returns to where it was opened.
- Home's Next 4 weeks cards open the Forecasts page when it is licensed (#109).
- Blog: "The German tank problem", "Why Othor AI says what it can't tell you" and the long read
  "The most dangerous equation".

### Changed
- The finding panel: tabs Finding · How it's calculated · The number · Query · Run; one footer
  row (run line with its details behind an (i), Watch · Snooze · Mute as quiet buttons, Add to a
  brief as the one primary); one button per other tab.
- Sweep's Suppressed tab: a plain line per section; held findings with their date, where they
  were held (from 0.33) and Undo, snoozed first; known patterns with their finding count;
  proposals with "Review on Home"; Show all after five. Watching reads Above / Below / Inside
  the limit. Suppression proposals name what recurred ("the same dip"), not the check.
- A finding's question to ask comes from the brief's rule, so the panel and the brief agree.
- Website: the demo page's video frame (1px) and the Hosted pricing card (2px) take the Ask
  Othor AI gradient border.

### Fixed
- Watch this: the limit shows in money or days ("₹64 L", "2.4 days", not "6.4E+6") and is read
  back as typed; its chart uses the number's units with readable axis labels; plain words, plain
  owner hints.

## [0.32.2] - 2026-11-21

### Added
- Website: othor.ai/demo, a page of its own like an app: the intro tour and one short video per
  feature (Morning Brief, Sweep, Business Model, External Signals, Setup, Discover, Forecasts,
  Look back, Summary, Weekly Digest), recorded on the invented sample company with a voice-over
  and captions; the home page's second button reads "Watch Demo Videos".
- Website: the top menu has Security under Product, and Contact after Pricing.

## [0.32.1] - 2026-11-22

### Changed
- Every side-menu page opens with one plain line under its title; the grey second line moved
  behind the title's ⓘ where it held detail (#108).
- Settings in tabs: Company, MCP, Charts, Delivery, Your data, Licence; one at a time, switched in
  place, the tab kept in the address (`?tab=`).
- The materiality floor ("Problems worth reporting · over ₹5 L") moved from Settings to the
  Business Model, edited in place as money ("₹5 L", "5 lakh", "500000"); the same `materiality`
  record, so the night's spec is unchanged in kind; event `ontology.floor_changed`.
- Business Model's level bar lights one tick per checklist item done.
- Discover: Switch on's Undo sits in the computing box; the "is on" note comes once, with the
  dashboard.
- AI usage: token counts in short (256k of 500k); the table keeps exact counts.
- /contact opens with only the top bar, then the form.

### Fixed
- Discover: a group switched on with history draws its headline numbers and trend from the run's
  stored values at once; its write-up describes that movement (prompt `discover_summary@v2`) and
  writes each number's name as given.
- Money: a goal's range keeps three figures ($541–592M, not $540.8–592.3M).
- The live demo stream: costs follow the weeks the plants move, so margin per metre holds on every
  night.

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
