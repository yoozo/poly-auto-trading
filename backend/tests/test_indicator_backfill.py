from datetime import datetime, timezone

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
