# Poly Auto Trading

Monorepo for a FastAPI backend and React admin frontend.

## Stack

- Backend: Python 3.13, uv, FastAPI, PostgreSQL, SQLAlchemy async, Alembic
- Frontend: Vite, React, TypeScript, Ant Design, ProComponents, lightweight-charts

## Layout

```txt
backend/   FastAPI API, database models, services
frontend/  React admin system and K-line views
```

## Local Services

```bash
just postgres
```

## Backend

```bash
just api-sync
just dev-api
```

## Configuration

Non-secret runtime settings live in local `config/app.yaml`; use `config/app.example.yaml`
as the committed template. Secrets stay in `.env`; use `.env.example` as the template for
required secret keys.

For a fresh setup:

```bash
cp config/app.example.yaml config/app.yaml
```

To migrate an existing all-in-one `.env` into the split format:

```bash
backend/.venv/bin/python scripts/migrate_env_to_yaml.py --dry-run
backend/.venv/bin/python scripts/migrate_env_to_yaml.py
```

The migration script writes local `config/app.yaml` and backs up the original `.env` before rewriting it.

## Frontend

```bash
just web-install
just dev-web
```

## Common Commands

```bash
just --list
just postgres
just dev-api
just dev-web
just check
```
