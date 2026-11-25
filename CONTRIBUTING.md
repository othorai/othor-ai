# Contributing to Othor AI

Thank you for helping. This page covers running Othor AI from source, the rules every change
keeps, and how contributions are accepted.

## Run it from source

You need Docker (with Compose) and [uv](https://docs.astral.sh/uv/). No Node.
On Windows, run them in a WSL 2 terminal (the checks are in the README's [See it in 5 minutes](README.md#see-it-in-5-minutes)).

```sh
make dev     # the app, a metadata Postgres, two sample databases and a test mailbox
make seed    # the invented sample company, Indigo Thread Co., into the sample databases
```

Then open http://localhost:8000. Emails land in the test mailbox at http://localhost:8026.
Host ports avoid the usual defaults: metadata Postgres 5442, sample PostgreSQL 5443, sample
MySQL 3307, mail 1026/8026, app 8000 (each can be changed with an `OTHOR_*_PORT` variable).

```sh
make test-fast                       # no containers needed
make test                            # everything (needs make dev)
make lint type contracts spdx css    # what CI checks first
othor run --company indigothread --date 2026-11-01
othor replay <run_id>                # must reproduce the run byte for byte
```

## The rules every change keeps

These are why Othor AI can be trusted with a business's numbers. A change that breaks one is
not accepted, however useful it is.

1. **AI drafts, code computes.** No number shown, emailed, stored or ranked may come from a
   model. Numbers are inserted into text from computed results.
2. **Same inputs, same numbers.** The nightly run is deterministic; `othor replay` reproduces a
   run byte for byte, and CI fails if it doesn't.
3. **Read-only, in place.** Sources are read with read-only credentials; a login that can write
   is refused. No raw rows go to a model. Personal columns are never read.
4. **The night needs no model.** Without one, the run, the detectors, the forecasts and the
   brief's numbers still work.
5. **Every figure has provenance:** its definition, SQL, test, parameters and run.
6. **Nothing a model drafts goes live until the owner applies it.**
7. **Self-hostable.** Nothing may depend on a service the customer's network couldn't run.
8. **Light.** Server-rendered pages with htmx and small local scripts; no front-end framework.

Code comments sometimes point to internal design notes (`docs/04`, `docs/plans/…`). Those aren't
in this repository; the code and its tests are the reference here.

## Sending a change

- Open an issue first for anything bigger than a small fix, so we can agree on the approach.
- Keep pull requests small, with tests. Engine code gets its tests first.
- Every source file starts with its SPDX line (`make spdx` checks; `python scripts/check_spdx.py --fix` adds it).
- **The Contributor Licence Agreement.** Before your first pull request is merged, you sign the
  [CLA](CLA.md) (a bot asks on the pull request). It lets Othor AI Private Limited license your
  contribution under the project's licences, including future ones.

## Questions

Ask in a GitHub issue. Security problems: please don't open an issue; see [SECURITY.md](SECURITY.md).
