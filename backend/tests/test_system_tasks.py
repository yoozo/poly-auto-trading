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


@pytest.mark.asyncio
async def test_system_tasks_batches_steps_and_reuses_ranges(monkeypatch) -> None:
    tasks = [
        SystemTask(
            id=7,
            task_type="kline_backfill",
            symbol="BTCUSDT",
            status="running",
            message="started",
            error="",
            total_inserted=10,
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            finished_at=None,
            task_metadata={},
        ),
        SystemTask(
            id=8,
            task_type="kline_backfill",
            symbol="BTCUSDT",
            status="completed",
            message="done",
            error="",
            total_inserted=20,
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            finished_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            task_metadata={},
        ),
    ]
    steps = [
        SystemTaskStep(
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
        ),
        SystemTaskStep(
            id=10,
            task_id=8,
            step_key="5m:1000",
            interval="5m",
            status="completed",
            start_ms=1000,
            cursor_ms=2000,
            end_ms=2000,
            inserted_count=20,
            raw_count=21,
            last_error="",
        ),
    ]

    class FakeScalars:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    class FakeSession:
        def __init__(self):
            self.scalars_calls = 0

        async def scalars(self, query):  # noqa: ANN001
            self.scalars_calls += 1
            return FakeScalars(tasks if self.scalars_calls == 1 else steps)

    class FakeSessionContext:
        def __init__(self):
            self.session = FakeSession()

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, exc_type, exc, traceback):  # noqa: ANN001
            return False

    ranges_calls: list[str] = []

    async def fake_ranges(session, symbol):
        ranges_calls.append(symbol)
        return {"1m": {"count": 10, "min_open_time": None, "max_open_time": None}}

    monkeypatch.setattr(routes_system_tasks, "AsyncSessionLocal", FakeSessionContext)
    monkeypatch.setattr(routes_system_tasks, "list_candle_ranges", fake_ranges)

    statuses = await routes_system_tasks.system_tasks("BTCUSDT")

    assert [status.id for status in statuses] == [7, 8]
    assert [step.step_key for status in statuses for step in status.steps] == ["1m:1000", "5m:1000"]
    assert ranges_calls == ["BTCUSDT"]


def test_latest_system_task_returns_idle_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_latest_task(session, *, task_type, symbol):  # noqa: ANN001
        return None

    monkeypatch.setattr(routes_system_tasks, "latest_task", fake_latest_task)

    response = make_client().get("/api/system-tasks/latest?task_type=kline_backfill&symbol=BTCUSDT")

    assert response.status_code == 200
    assert response.json()["status"] == "idle"
    assert response.json()["task_type"] == "kline_backfill"
