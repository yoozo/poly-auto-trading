from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

from app.api import routes_system_tasks
from app.db.models import SystemTask, SystemTaskStep
from app.main import create_app
from conftest import login_test_client


def make_client() -> TestClient:
    app = create_app(enable_lifespan=False)
    client = TestClient(app)
    login_test_client(client)
    return client


@pytest.mark.asyncio
async def test_serialize_system_task_includes_steps_and_ranges(monkeypatch) -> None:
    task = SystemTask(
        id=7,
        task_type="kline_backfill",
        symbol="BTCUSDT",
        status="running",
        message="started",
        error="",
        total_inserted=10,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        finished_at=None,
        task_metadata={"source": "test"},
    )
    step = SystemTaskStep(
        id=9,
        task_id=7,
        step_key="1m:1000",
        interval="1m",
        status="running",
        start_ms=1000,
        cursor_ms=1000,
        end_ms=2000,
        inserted_count=10,
        raw_count=11,
        last_error="",
    )

    class FakeScalars:
        def all(self):
            return [step]

    class FakeSession:
        async def scalars(self, query):  # noqa: ANN001
            return FakeScalars()

    async def fake_ranges(session, symbol):
        assert symbol == "BTCUSDT"
        return {"1m": {"count": 10, "min_open_time": None, "max_open_time": None}}

    monkeypatch.setattr(routes_system_tasks, "list_candle_ranges", fake_ranges)

    status = await routes_system_tasks.serialize_system_task(FakeSession(), task)

    assert status.id == 7
    assert status.task_type == "kline_backfill"
    assert status.steps[0].step_key == "1m:1000"
    assert status.steps[0].interval == "1m"
    assert status.steps[0].start_ms == 1000
    assert status.steps[0].end_ms == 2000
    assert status.steps[0].raw_count == 11
    assert status.candle_ranges["1m"]["count"] == 10


def test_latest_system_task_returns_idle_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_latest_task(session, *, task_type, symbol):  # noqa: ANN001
        return None

    monkeypatch.setattr(routes_system_tasks, "latest_task", fake_latest_task)

    response = make_client().get("/api/system-tasks/latest?task_type=kline_backfill&symbol=BTCUSDT")

    assert response.status_code == 200
    assert response.json()["status"] == "idle"
    assert response.json()["task_type"] == "kline_backfill"
