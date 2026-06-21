import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import indicator_backfill


@pytest.mark.asyncio
async def test_indicator_incremental_start_ms_uses_latest_snapshot(monkeypatch) -> None:
    latest = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    async def fake_get_latest_indicator_time(session, symbol, interval):
        assert symbol == "BTCUSDT"
        assert interval == "1h"
        return latest

    monkeypatch.setattr(indicator_backfill, "get_latest_indicator_time", fake_get_latest_indicator_time)

    start_ms = await indicator_backfill.incremental_start_ms(object(), symbol="BTCUSDT", interval="1h")

    assert start_ms == int(latest.timestamp() * 1000) + 60 * 60_000


@pytest.mark.asyncio
async def test_indicator_incremental_start_ms_bootstraps_when_empty(monkeypatch) -> None:
    async def fake_get_latest_indicator_time(session, symbol, interval):
        return None

    monkeypatch.setattr(indicator_backfill, "get_latest_indicator_time", fake_get_latest_indicator_time)

    assert await indicator_backfill.incremental_start_ms(object(), symbol="BTCUSDT", interval="1m") == 0


@pytest.mark.asyncio
async def test_indicator_backfill_steps_are_bounded_concurrent(monkeypatch) -> None:
    runner = indicator_backfill.IndicatorBackfillRunner()
    active = 0
    max_active = 0
    calls: list[int] = []

    monkeypatch.setattr(indicator_backfill, "INDICATOR_BACKFILL_CONCURRENCY", 2)

    async def fake_backfill_interval(*, task_id, progress_id):
        nonlocal active, max_active
        calls.append(progress_id)
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1

    runner._backfill_interval = fake_backfill_interval  # type: ignore[method-assign]

    await runner._backfill_steps(
        7,
        [
            SimpleNamespace(id=1, status="pending"),
            SimpleNamespace(id=2, status="pending"),
            SimpleNamespace(id=3, status="pending"),
            SimpleNamespace(id=4, status="completed"),
        ],
    )

    assert sorted(calls) == [1, 2, 3]
    assert max_active == 2
