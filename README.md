# poly-auto-trading

FastAPI + React/Vite dashboard scaffold for a Polymarket BTC 5m/15m trading system.

## 开发计划

查看 [开发阶段计划](docs/development-phases.md)，了解数据采集、前端监控、Telegram 通知和自动交易的分阶段实现安排。

## Backend

```bash
cp config.example.yaml config.yaml
uv python install 3.13
uv sync --dev
uv run uvicorn app.main:app --reload --port 8000
```

Python dependencies are managed with `uv` from `pyproject.toml`. Commit `uv.lock` after running `uv sync --dev`.

## Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend expects the API at `http://localhost:8000`.
