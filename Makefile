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

.PHONY: help install install-api install-web dev dev-api dev-web \
	db-up db-create migrate migrate-down migrate-current migrate-history \
	lsof test test-api test-web lint lint-api build build-web check clean clean-api clean-web

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
	cd $(FRONTEND_DIR) && npm run dev -- --host $(WEB_HOST)

dev: ## Show dev server commands.
	@echo "Run these in two terminals:"
	@echo "  make dev-api"
	@echo "  make dev-web"
	@echo ""
	@echo "Stop each server with Ctrl+C in its own terminal."

db-up: ## Start PostgreSQL with docker compose.
	docker compose up -d postgres

db-create: ## Create local PostgreSQL database if missing.
	cd $(BACKEND_DIR) && uv run --cache-dir $(UV_CACHE) python -c "import asyncio, asyncpg; \
	async def main(): \
	    conn = await asyncpg.connect(user='$(DB_USER)', password='$(DB_PASSWORD)', database='postgres', host='$(DB_HOST)', port=$(DB_PORT)); \
	    exists = await conn.fetchval(\"select 1 from pg_database where datname='$(DB_NAME)'\"); \
	    await conn.execute('create database $(DB_NAME)') if not exists else None; \
	    await conn.close(); \
	asyncio.run(main())"

lsof: ## Show processes listening on API, web, and database ports.
	@for port in $(API_PORT) $(WEB_PORT) $(DB_PORT); do \
		echo "== port $$port =="; \
		lsof -nP -iTCP:$$port -sTCP:LISTEN || true; \
	done

migrate: ## Run Alembic migrations.
	cd $(BACKEND_DIR) && uv run --cache-dir $(UV_CACHE) python -m alembic upgrade head

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
