from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.candle import Candle
from app.services import binance_monitor


def make_candle(index: int) -> Candle:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    return Candle(
        symbol="BTCUSDT",
        interval="1m",
        open_time=start,
        close_time=start + timedelta(minutes=1),
        open=100 + index,
        high=101 + index,
        low=99 + index,
        close=100 + index,
        volume=1,
        is_closed=True,
    )


class FakeSessionLocal:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
async def test_backfill_once_syncs_live_windows_without_starting_full_task(monkeypatch) -> None:
    calls = {"synced": [], "listed": []}
    cached = [make_candle(index) for index in range(500)]

    class FakeCandleSyncService:
        async def ensure_latest_window(self, session, *, symbol, interval, limit):
            calls["synced"].append({"symbol": symbol, "interval": interval, "limit": limit})

    async def fake_list_candles(session, symbol, interval, limit):
        calls["listed"].append({"symbol": symbol, "interval": interval, "limit": limit})
        return cached[-limit:]

    monkeypatch.setattr(binance_monitor.settings, "binance_symbol", "BTCUSDT")
    monkeypatch.setattr(binance_monitor.settings, "binance_intervals", ["1m", "5m"])
    monkeypatch.setattr(binance_monitor, "AsyncSessionLocal", FakeSessionLocal)
    monkeypatch.setattr(binance_monitor, "candle_sync_service", FakeCandleSyncService())
    monkeypatch.setattr(binance_monitor, "list_candles", fake_list_candles)
    binance_monitor.market_signal_pipeline._live_candles.clear()

    monitor = binance_monitor.BinanceMonitor()
    await monitor.backfill_once()

    assert calls["synced"] == [
        {"symbol": "BTCUSDT", "interval": "1m", "limit": 500},
        {"symbol": "BTCUSDT", "interval": "5m", "limit": 500},
    ]
    assert calls["listed"] == [
        {"symbol": "BTCUSDT", "interval": "1m", "limit": 500},
        {"symbol": "BTCUSDT", "interval": "5m", "limit": 500},
    ]
    assert len(binance_monitor.market_signal_pipeline._live_candles[("BTCUSDT", "1m")]) == 500
    assert len(binance_monitor.market_signal_pipeline._live_candles[("BTCUSDT", "5m")]) == 500


@pytest.mark.asyncio
async def test_refresh_live_window_reads_database_only(monkeypatch) -> None:
    calls = {"synced": [], "listed": []}
    cached = [make_candle(index) for index in range(20)]

    class FakeCandleSyncService:
        async def ensure_latest_window(self, session, *, symbol, interval, limit):
            calls["synced"].append({"symbol": symbol, "interval": interval, "limit": limit})

    async def fake_list_candles(session, symbol, interval, limit):
        calls["listed"].append({"symbol": symbol, "interval": interval, "limit": limit})
        return cached

    monkeypatch.setattr(binance_monitor, "AsyncSessionLocal", FakeSessionLocal)
    monkeypatch.setattr(binance_monitor, "candle_sync_service", FakeCandleSyncService())
    monkeypatch.setattr(binance_monitor, "list_candles", fake_list_candles)
    binance_monitor.market_signal_pipeline._live_candles.clear()

    monitor = binance_monitor.BinanceMonitor()
    await monitor.refresh_live_window("BTCUSDT", "1m")

    assert calls["synced"] == [{"symbol": "BTCUSDT", "interval": "1m", "limit": 500}]
    assert calls["listed"] == [{"symbol": "BTCUSDT", "interval": "1m", "limit": 500}]
    assert binance_monitor.market_signal_pipeline._live_candles[("BTCUSDT", "1m")] == cached
