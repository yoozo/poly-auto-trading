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
