SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

BACKEND_DIR := backend
FRONTEND_DIR := frontend
UV_CACHE := .uv-cache
NPM_CACHE := .npm-cache

DB_NAME ?= poly_auto_trading
DB_USER ?= postgres
DB_PASSWORD ?= postgres
DB_HOST ?= localhost
DB_PORT ?= 5432

API_HOST ?= 127.0.0.1
API_PORT ?= 8000
WEB_HOST ?= 0.0.0.0
WEB_PORT ?= 5173
POLYMARKET_EVENT_INPUT := $(or $(SLUG),$(word 2,$(MAKECMDGOALS)))

ifneq ($(filter poly-event,$(MAKECMDGOALS)),)
%:
	@:
endif

.PHONY: help install install-api install-web dev dev-api dev-web \
	db-up db-create migrate migrate-down migrate-current migrate-history \
	lsof poly-event test test-api test-web lint lint-api build build-web check clean clean-api clean-web

help: ## Show available commands.
	@awk 'BEGIN {FS = ":.*##"; printf "\nCommands:\n"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: install-api install-web ## Install backend and frontend dependencies.

install-api: ## Install backend dependencies with uv.
	cd $(BACKEND_DIR) && uv sync --cache-dir $(UV_CACHE)

install-web: ## Install frontend dependencies with npm.
	cd $(FRONTEND_DIR) && npm install --cache $(NPM_CACHE)

dev-api: ## Start FastAPI dev server.
	cd $(BACKEND_DIR) && uv run --cache-dir $(UV_CACHE) uvicorn app.main:app --reload --host $(API_HOST) --port $(API_PORT)

dev-web: ## Start Vite dev server.
	cd $(FRONTEND_DIR) && npm run dev -- --host $(WEB_HOST) --port $(WEB_PORT)

dev: ## Start API and web dev servers.
	@echo "Starting API at http://$(API_HOST):$(API_PORT)"
	@echo "Starting web at http://$(WEB_HOST):$(WEB_PORT)"
	@( \
		trap 'pids=$$(jobs -p); [ -z "$$pids" ] || kill $$pids' INT TERM EXIT; \
		$(MAKE) dev-api & \
		sleep 2; \
		$(MAKE) dev-web & \
		wait \
	)

db-up: ## Start PostgreSQL with docker compose.
	docker compose up -d postgres

db-create: ## Create local PostgreSQL database if missing.
	cd $(BACKEND_DIR) && uv run --cache-dir $(UV_CACHE) python -c 'import asyncio, asyncpg; exec("""async def main():\n    conn = await asyncpg.connect(user="$(DB_USER)", password="$(DB_PASSWORD)", database="postgres", host="$(DB_HOST)", port=$(DB_PORT))\n    exists = await conn.fetchval("select 1 from pg_database where datname='\''$(DB_NAME)'\''")\n    if not exists:\n        await conn.execute("create database $(DB_NAME)")\n    await conn.close()\n"""); asyncio.run(main())'

lsof: ## Show processes listening on API, web, and database ports.
	@for port in $(API_PORT) $(WEB_PORT) $(DB_PORT); do \
		echo "== port $$port =="; \
		lsof -nP -iTCP:$$port -sTCP:LISTEN || true; \
	done

poly-event: ## Resolve a Polymarket event slug/URL. Usage: copy event URL, then run make poly-event
	@input="$(POLYMARKET_EVENT_INPUT)"; \
	if [ -z "$$input" ]; then \
		if command -v pbpaste >/dev/null 2>&1; then \
			input="$$(pbpaste)"; \
		elif command -v wl-paste >/dev/null 2>&1; then \
			input="$$(wl-paste)"; \
		elif command -v xclip >/dev/null 2>&1; then \
			input="$$(xclip -selection clipboard -o)"; \
		fi; \
	fi; \
	input="$$(printf '%s' "$$input" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$$//')"; \
	test -n "$$input" || (echo "Usage: copy a Polymarket event URL/slug, then run make poly-event" >&2; exit 2); \
	echo "Polymarket input: $$input" >&2; \
	slug="$$input"; \
	slug="$${slug#https://}"; \
	slug="$${slug#http://}"; \
	slug="$${slug#www.}"; \
	slug="$${slug#polymarket.com/}"; \
	if [[ "$$slug" == event/* ]]; then \
		slug="$${slug#event/}"; \
	elif [[ "$$slug" == */event/* ]]; then \
		slug="$${slug#*/event/}"; \
	fi; \
	slug="$${slug%%\?*}"; \
	slug="$${slug%%\#*}"; \
	slug="$${slug%%/*}"; \
	test -n "$$slug" || (echo "Could not parse Polymarket event slug from clipboard/input." >&2; exit 2); \
	echo "Polymarket slug: $$slug" >&2; \
	payload="$$(curl -s "https://gamma-api.polymarket.com/events/slug/$$slug")"; \
	if ! printf '%s' "$$payload" | jq -e '.markets | type == "array" and length > 0' >/dev/null; then \
		echo "Polymarket event lookup did not return markets." >&2; \
		echo "Parsed slug: $$slug" >&2; \
		echo "Clipboard/input must be a Polymarket event URL or event slug." >&2; \
		raw_response="$${payload:0:500}"; \
		test -n "$$raw_response" || raw_response="<empty>"; \
		printf 'Raw response: %s\n' "$$raw_response" >&2; \
		exit 5; \
	fi; \
	printf '%s' "$$payload" \
		| jq '.markets[] | {question, conditionId, outcomes: (.outcomes | fromjson), clobTokenIds: (.clobTokenIds | fromjson), negRisk, tickSize: .orderPriceMinTickSize}'

migrate: ## Run Alembic migrations.
	cd $(BACKEND_DIR) && uv run --cache-dir $(UV_CACHE) alembic upgrade head

migrate-down: ## Roll back one Alembic migration.
	cd $(BACKEND_DIR) && uv run --cache-dir $(UV_CACHE) python -m alembic downgrade -1

migrate-current: ## Show current Alembic revision.
	cd $(BACKEND_DIR) && uv run --cache-dir $(UV_CACHE) python -m alembic current

migrate-history: ## Show Alembic migration history.
	cd $(BACKEND_DIR) && uv run --cache-dir $(UV_CACHE) python -m alembic history

test: test-api test-web ## Run backend tests and frontend build check.

test-api: ## Run backend tests.
	cd $(BACKEND_DIR) && uv run --cache-dir $(UV_CACHE) pytest

test-web: ## Run frontend TypeScript/build check.
	cd $(FRONTEND_DIR) && npm run build

lint: lint-api ## Run linters.

lint-api: ## Run backend ruff check.
	cd $(BACKEND_DIR) && uv run --cache-dir $(UV_CACHE) ruff check app tests

build: build-web ## Build production frontend.

build-web: ## Build frontend assets.
	cd $(FRONTEND_DIR) && npm run build

check: lint test ## Run lint and tests.

clean: clean-api clean-web ## Remove local build/cache artifacts.

clean-api: ## Remove backend cache artifacts.
	find $(BACKEND_DIR) -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +

clean-web: ## Remove frontend build artifacts.
	rm -rf $(FRONTEND_DIR)/dist
