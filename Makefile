# SPDX-License-Identifier: Apache-2.0
.PHONY: dev demo down seed migrate test test-fast css-check lint fmt type contracts spdx licences css css-watch ci

UV ?= uv

dev:  ## app + metadata Postgres + sample MySQL/Postgres + Mailpit
	@test -f .env || cp .env.example .env
	docker compose up -d --build --wait
	@echo "app http://localhost:8000/healthz · mail http://localhost:8026"

demo:  ## the whole thing in one go: dev stack, sample data, Indigo Thread set up, one night run, how to sign in
	$(MAKE) dev
	$(MAKE) seed
	docker compose exec -T app othor demo

down:
	docker compose down

SCALE ?= default

seed:  ## generate Indigo Thread into erp_db, sales_dw and the Sheets fixtures (SCALE=large for perf)
	$(UV) run othor seed --scale $(SCALE)

migrate:
	$(UV) run othor migrate

test:  ## unit + guards + sample company + integration (needs `make dev`)
	$(UV) run pytest

test-fast:  ## no containers needed
	OTHOR_TEST_SKIP_DB=1 $(UV) run pytest

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

type:
	$(UV) run mypy

test-large: ## the 4,000-table scan (after make seed SCALE=large)
	OTHOR_TEST_LARGE=1 $(UV) run pytest -s tests/integration/test_large_scan.py

contracts:
	$(UV) run lint-imports

css:  ## Tailwind v4 standalone (pinned, checksum-verified): static/css/app.<sha8>.css + manifest
	$(UV) run python scripts/build_css.py

css-check:  ## fail if the built CSS is missing or stale (CI and the image build, M12b)
	$(UV) run python scripts/build_css.py --check

css-watch:  ## rebuild on change (make dev also runs this in the css service)
	$(UV) run python scripts/build_css.py --watch

spdx:  ## every source file names its licence (M20.5; --fix adds the missing lines)
	$(UV) run python scripts/check_spdx.py

licences:  ## every shipped package and asset has an allowed licence; writes THIRD_PARTY_LICENSES
	$(UV) run python scripts/check_licences.py --write THIRD_PARTY_LICENSES

ci: lint type contracts spdx licences css css-check test
