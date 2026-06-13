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
async def test_backfill_interval_paginates_from_latest_database_candle(monkeypatch) -> None:
    calls = []
    upserted = []
    latest = make_candle(0)

    class FakeBinanceClient:
        async def fetch_klines(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return [make_candle(index) for index in range(0, 1000)]
            return [make_candle(index) for index in range(1000, 1002)]

    async def fake_get_latest_candle(session, symbol, interval):
        return latest

    async def fake_upsert_candles(session, candles):
        upserted.append(candles)

    async def fake_list_candles(session, symbol, interval, limit):
        return [make_candle(index) for index in range(502, 1002)]

    monkeypatch.setattr(binance_monitor, "AsyncSessionLocal", FakeSessionLocal)
    monkeypatch.setattr(binance_monitor, "get_latest_candle", fake_get_latest_candle)
    monkeypatch.setattr(binance_monitor, "upsert_candles", fake_upsert_candles)
    monkeypatch.setattr(binance_monitor, "list_candles", fake_list_candles)
    monkeypatch.setattr(binance_monitor, "utc_now", lambda: datetime(2026, 1, 1, 16, 41, tzinfo=timezone.utc))

    monitor = binance_monitor.BinanceMonitor()
    monitor._client = FakeBinanceClient()
    binance_monitor.market_signal_pipeline._live_candles.clear()

    await monitor.backfill_interval("BTCUSDT", "1m")

    assert [call["start_ms"] for call in calls] == [1767225600000, 1767285600000]
    assert all(call["limit"] == 1000 for call in calls)
    assert [len(batch) for batch in upserted] == [1000, 2]
    assert len(binance_monitor.market_signal_pipeline._live_candles[("BTCUSDT", "1m")]) == 500


@pytest.mark.asyncio
async def test_backfill_interval_initializes_recent_window_when_database_is_empty(monkeypatch) -> None:
    calls = []
    fetched = [make_candle(index) for index in range(500)]

    class FakeBinanceClient:
        async def fetch_klines(self, **kwargs):
            calls.append(kwargs)
            return fetched

    async def fake_get_latest_candle(session, symbol, interval):
        return None

    async def fake_upsert_candles(session, candles):
        pass

    async def fake_list_candles(session, symbol, interval, limit):
        return fetched[-limit:]

    monkeypatch.setattr(binance_monitor, "AsyncSessionLocal", FakeSessionLocal)
    monkeypatch.setattr(binance_monitor, "get_latest_candle", fake_get_latest_candle)
    monkeypatch.setattr(binance_monitor, "upsert_candles", fake_upsert_candles)
    monkeypatch.setattr(binance_monitor, "list_candles", fake_list_candles)

    monitor = binance_monitor.BinanceMonitor()
    monitor._client = FakeBinanceClient()
    binance_monitor.market_signal_pipeline._live_candles.clear()

    await monitor.backfill_interval("BTCUSDT", "1m")

    assert calls == [{"symbol": "BTCUSDT", "interval": "1m", "limit": 500}]
    assert len(binance_monitor.market_signal_pipeline._live_candles[("BTCUSDT", "1m")]) == 500
