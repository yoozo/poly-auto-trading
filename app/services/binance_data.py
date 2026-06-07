from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import websockets

from app.clients.binance_client import BinanceClient
from app.core.config import settings
from app.schemas import Candle, Interval
from app.services.indicators import build_indicator_snapshot
from app.services.state_store import StateStore, state_store


BINANCE_INTERVALS: tuple[Interval, ...] = ("1m", "5m", "15m", "30m", "1h", "4h")


class BinanceDataService:
    def __init__(self, store: StateStore = state_store) -> None:
        self._store = store
        self._client = BinanceClient(rest_base_urls=settings.effective_binance_rest_base_urls)

    async def backfill_all_intervals(self) -> None:
        self._store.set_service_health("binance_rest", "backfilling")
        try:
            for interval in BINANCE_INTERVALS:
                candles = await self._client.fetch_klines(
                    symbol=settings.binance_symbol,
                    interval=interval,
                    limit=settings.candle_history_limit,
                )
                closed_candles = [candle for candle in candles if candle.is_closed]
                self._store.upsert_candles(settings.binance_symbol, interval, closed_candles)
            self._refresh_indicators()
            self._store.set_service_health("binance_rest", "running")
        except Exception as exc:
            self._store.set_service_health("binance_rest", "error", last_error=str(exc))

    async def run_ws_forever(self) -> None:
        backoff_seconds = 1.0
        while True:
            for base_url in settings.effective_binance_ws_base_urls:
                try:
                    await self._run_ws_once(base_url)
                    backoff_seconds = 1.0
                except asyncio.CancelledError:
                    self._store.set_service_health("binance_ws", "stopped")
                    raise
                except Exception as exc:
                    self._store.set_service_health("binance_ws", "reconnecting", last_error=f"{base_url}: {exc}")
                    await asyncio.sleep(backoff_seconds)
                    backoff_seconds = min(backoff_seconds * 2, 30.0)

    async def _run_ws_once(self, base_url: str) -> None:
        url = self._build_combined_stream_url(base_url)
        self._store.set_service_health("binance_ws", "reconnecting")
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as websocket:
            self._store.set_service_health("binance_ws", "connected")
            async for raw_message in websocket:
                candle = self._parse_ws_message(raw_message)
                if candle is None or not candle.is_closed:
                    continue
                self._store.upsert_candles(settings.binance_symbol, candle.interval, [candle])
                self._refresh_indicators()

    def _build_combined_stream_url(self, base_url: str) -> str:
        streams = "/".join(
            f"{settings.binance_symbol.lower()}@kline_{interval}"
            for interval in BINANCE_INTERVALS
        )
        return f"{base_url.rstrip('/')}/stream?streams={streams}"

    def _parse_ws_message(self, raw_message: str | bytes) -> Candle | None:
        payload = json.loads(raw_message)
        data = payload.get("data", payload)
        if data.get("e") != "kline":
            return None

        kline: dict[str, Any] = data.get("k", {})
        interval = kline.get("i")
        if interval not in BINANCE_INTERVALS:
            return None

        close_time = _from_ms(kline["T"])
        return Candle(
            symbol=data.get("s", settings.binance_symbol).upper(),
            interval=interval,
            open_time=_from_ms(kline["t"]),
            close_time=close_time,
            open=float(kline["o"]),
            high=float(kline["h"]),
            low=float(kline["l"]),
            close=float(kline["c"]),
            volume=float(kline["v"]),
            is_closed=bool(kline.get("x")) and close_time <= datetime.now(timezone.utc),
        )

    def _refresh_indicators(self) -> None:
        candles_by_interval = {
            interval: self._store.get_candles(settings.binance_symbol, interval, settings.candle_history_limit)
            for interval in BINANCE_INTERVALS
        }
        snapshot = build_indicator_snapshot(settings.binance_symbol, candles_by_interval)
        self._store.set_indicator_snapshot(snapshot)


def _from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


binance_data_service = BinanceDataService()
