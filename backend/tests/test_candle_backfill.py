from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.schemas.candle import Candle
from app.services import candle_backfill


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


def test_wave_page_starts_uses_ten_pages() -> None:
    start_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    starts = candle_backfill.wave_page_starts(start_ms, end_ms=start_ms + 1_000_000_000, interval="1m")

    assert len(starts) == 10
    assert starts == [start_ms + index * 60_000_000 for index in range(10)]


def test_wave_page_starts_probes_real_binance_start_before_concurrency() -> None:
    assert candle_backfill.wave_page_starts(0, end_ms=1_000_000_000, interval="1m") == [0]


@pytest.mark.asyncio
async def test_fetch_wave_requests_every_page_start() -> None:
    calls = []

    class FakeClient:
        async def fetch_klines(self, **kwargs):
            calls.append(kwargs)
            return [make_candle(0)]

    results = await candle_backfill.fetch_wave(
        FakeClient(),
        symbol="BTCUSDT",
        interval="1m",
        end_ms=180_000_000,
        page_starts=[0, 60_000_000, 120_000_000],
    )

    assert sorted(results) == [0, 60_000_000, 120_000_000]
    assert [call["start_ms"] for call in calls] == [0, 60_000_000, 120_000_000]
    assert all(call["limit"] == 1000 for call in calls)


@pytest.mark.asyncio
async def test_persist_wave_stops_cursor_at_failed_page(monkeypatch) -> None:
    upserted = []

    async def fake_upsert(session, candles):
        upserted.append(candles)

    monkeypatch.setattr(candle_backfill, "upsert_candles", fake_upsert)
    progress = SimpleNamespace(
        interval="1m",
        next_start_ms=0,
        end_ms=180_000_000,
        inserted_count=0,
        status="running",
        finished_at=None,
    )
    first_page = [make_candle(index) for index in range(1000)]
    second_page = [make_candle(index) for index in range(1000, 2000)]
    first_start_ms = int(first_page[0].open_time.timestamp() * 1000)
    second_start_ms = int(second_page[0].open_time.timestamp() * 1000)

    failed = await candle_backfill.persist_wave(
        object(),
        progress,
        {
            first_start_ms: first_page,
            second_start_ms: "RuntimeError: rate limited",
            second_start_ms + 60_000_000: second_page,
        },
    )

    assert failed == second_start_ms
    assert progress.next_start_ms == second_start_ms
    assert progress.inserted_count == 1000
    assert upserted == [first_page]
