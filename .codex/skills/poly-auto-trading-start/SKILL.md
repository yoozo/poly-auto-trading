---
name: poly-auto-trading-start
description: Start and verify the local poly-auto-trading project. Use when the user asks to start, run, boot, serve, open, verify, or check the FastAPI backend and React/Vite frontend for the /Users/yoozo/Documents/poly-auto-trading workspace, including uv/Python 3.13 backend setup, npm frontend setup, health checks, and reporting local URLs.
---

# Poly Auto Trading Start

## Overview

Use this skill to start the local `poly-auto-trading` development stack. The project uses Python 3.13 + uv for the FastAPI backend and npm + React/Vite for the frontend dashboard.

Default workspace:

```text
/Users/yoozo/Documents/poly-auto-trading
```

## Quick Start

Run the bundled script from this skill:

```bash
python3.13 /Users/yoozo/Documents/poly-auto-trading/.codex/skills/poly-auto-trading-start/scripts/start_project.py
```

The script:

- Verifies the workspace exists.
- Verifies `uv`, `python3.13`, `npm`, and `node` availability.
- Runs `uv sync --dev` for backend dependencies.
- Runs `npm install` if `frontend/node_modules` is missing.
- Starts FastAPI on `http://localhost:8000`.
- Starts Vite on `http://localhost:5173`.
- Checks backend `/health`.
- Writes logs to `/private/tmp/poly-auto-trading/`.

## Expected URLs

- Backend API: `http://localhost:8000`
- Backend health: `http://localhost:8000/health`
- Frontend dashboard: `http://localhost:5173`

## Manual Fallback

If the script cannot run, start services manually:

```bash
cd /Users/yoozo/Documents/poly-auto-trading
uv sync --dev
uv run uvicorn app.main:app --reload --port 8000
```

In a second terminal:

```bash
cd /Users/yoozo/Documents/poly-auto-trading/frontend
npm install
npm run dev
```

## Completion Tracking

When work related to this project is completed, make sure the project documentation reflects what is done. In particular:

- Update `/Users/yoozo/Documents/poly-auto-trading/docs/development-phases.md` when a phase, milestone, or major requirement is completed.
- Update `/Users/yoozo/Documents/poly-auto-trading/docs/phase-1-real-data-plan.md` when a Phase 1 step is completed.
- Record completed items clearly, preferably with a short status note such as `已完成` plus the completion date or implementation summary.
- If the user only asks to start the project, do not mutate docs automatically; just remind them if completed work appears undocumented.
- If the user asks to implement or finish a requirement, update the relevant docs before the final response.

## Reporting

After starting, report:

- Whether backend and frontend processes started.
- Backend health check result.
- The backend and frontend URLs.
- Log file paths under `/private/tmp/poly-auto-trading/`.
- Any missing tool or failed command with the exact next fix.

Do not enable real trading or modify `.env` values while using this startup skill.
