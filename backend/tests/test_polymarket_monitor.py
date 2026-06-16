import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import websockets

from app.services import polymarket_monitor
from app.services.polymarket_monitor import (
    PolymarketMarketMonitor,
    calculate_next_refresh_delay,
    calculate_signal_refresh_delay,
    cancel_tasks,
)


def test_next_refresh_delay_uses_empty_retry_without_markets() -> None:
    now = datetime(2026, 6, 13, 5, 0, tzinfo=timezone.utc)

    delay = calculate_next_refresh_delay(
        now=now,
        next_boundary=None,
        market_count=0,
        fallback_seconds=60,
        boundary_window_seconds=3,
        empty_retry_seconds=5,
    )

    assert delay == 5


def test_next_refresh_delay_schedules_before_future_boundary() -> None:
    now = datetime(2026, 6, 13, 5, 0, tzinfo=timezone.utc)

    delay = calculate_next_refresh_delay(
        now=now,
        next_boundary=now + timedelta(seconds=20),
        market_count=4,
        fallback_seconds=60,
        boundary_window_seconds=3,
        empty_retry_seconds=5,
    )

    assert delay == 17


def test_next_refresh_delay_schedules_after_boundary_when_inside_window() -> None:
    now = datetime(2026, 6, 13, 5, 0, tzinfo=timezone.utc)

    delay = calculate_next_refresh_delay(
        now=now,
        next_boundary=now + timedelta(seconds=2),
        market_count=4,
        fallback_seconds=60,
        boundary_window_seconds=3,
        empty_retry_seconds=5,
    )

    assert delay == 5


def test_next_refresh_delay_uses_fallback_without_future_boundary() -> None:
    now = datetime(2026, 6, 13, 5, 0, tzinfo=timezone.utc)

    delay = calculate_next_refresh_delay(
        now=now,
        next_boundary=None,
        market_count=4,
        fallback_seconds=60,
        boundary_window_seconds=3,
        empty_retry_seconds=5,
    )

    assert delay == 60


def test_signal_refresh_delay_throttles_recent_refresh() -> None:
    now = datetime(2026, 6, 13, 5, 0, 10, tzinfo=timezone.utc)

    delay = calculate_signal_refresh_delay(
        now=now,
        last_refresh_at=now - timedelta(seconds=10),
        min_interval_seconds=30,
    )

    assert delay == 20


def test_signal_refresh_delay_allows_stale_refresh() -> None:
    now = datetime(2026, 6, 13, 5, 0, 31, tzinfo=timezone.utc)

    delay = calculate_signal_refresh_delay(
        now=now,
        last_refresh_at=now - timedelta(seconds=31),
        min_interval_seconds=30,
    )

    assert delay == 0


@pytest.mark.asyncio
async def test_wait_until_next_refresh_coalesces_repeated_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    monitor = PolymarketMarketMonitor()
    monitor._refresh_event.set()
    sleep_delays: list[float] = []

    async def fake_next_refresh_delay() -> float:
        return 60

    async def fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)
        monitor._refresh_event.set()

    monitor.next_refresh_delay = fake_next_refresh_delay  # type: ignore[method-assign]
    monitor.signal_refresh_delay = lambda: 20  # type: ignore[method-assign]
    monkeypatch.setattr(polymarket_monitor.asyncio, "sleep", fake_sleep)

    await monitor.wait_until_next_refresh()

    assert sleep_delays == [20]
    assert monitor._refresh_event.is_set()


@pytest.mark.asyncio
async def test_cancel_tasks_suppresses_connection_closed_from_done_task() -> None:
    async def closed_task() -> None:
        raise websockets.exceptions.ConnectionClosedError(None, None)

    task = asyncio.create_task(closed_task())
    await asyncio.sleep(0)

    await cancel_tasks(task)
