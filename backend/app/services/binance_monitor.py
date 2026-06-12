from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.schemas.candle import Candle
from app.services.binance_client import BinanceClient
from app.services.candle_store import upsert_candles
from app.services.indicators import calculate_indicator_points
from app.services.market_ws_hub import market_ws_hub
from app.services.service_health import service_health_store

logger = logging.getLogger(__name__)


class BinanceMonitor:
    def __init__(self) -> None:
        self._client = BinanceClient()
        self._tasks: list[asyncio.Task] = []
        self._live_candles: dict[tuple[str, str], list[Candle]] = {}

    async def start(self) -> None:
        if not settings.binance_ws_enabled:
            service_health_store.set("binance_ws", "idle")
            return
        self._tasks = [
            asyncio.create_task(self.backfill_loop(), name="binance-backfill"),
            asyncio.create_task(self.ws_loop(), name="binance-ws"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
        service_health_store.set("binance_ws", "stopped")

    async def backfill_loop(self) -> None:
        while True:
            try:
                await self.backfill_once()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Binance backfill failed")
                service_health_store.set("binance_rest", "error", last_error=str(exc))
                await asyncio.sleep(15)

    async def backfill_once(self) -> None:
        service_health_store.set("binance_rest", "running", metadata={"operation": "backfill"})
        for interval in settings.binance_intervals:
            candles = await self._client.fetch_klines(
                symbol=settings.binance_symbol,
                interval=interval,  # type: ignore[arg-type]
                limit=settings.candle_history_limit,
            )
            async with AsyncSessionLocal() as session:
                await upsert_candles(session, candles)
            self._replace_live_candles(settings.binance_symbol, interval, candles)

    async def ws_loop(self) -> None:
        backoff = 1.0
        endpoint_index = 0
        while True:
            base_urls = settings.binance_ws_base_urls
            if not base_urls:
                service_health_store.set(
                    "binance_ws",
                    "error",
                    last_error="No Binance WebSocket endpoints configured",
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            base_url = base_urls[endpoint_index % len(base_urls)]
            try:
                await self._ws_once(base_url)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                next_base_url = base_urls[(endpoint_index + 1) % len(base_urls)]
                logger.exception(
                    "Binance websocket endpoint failed; switching endpoint",
                    extra={"endpoint": base_url, "next_endpoint": next_base_url},
                )
                service_health_store.set(
                    "binance_ws",
                    "reconnecting",
                    last_error=str(exc),
                    metadata={"endpoint": base_url, "next_endpoint": next_base_url},
                )
                endpoint_index += 1
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _ws_once(self, base_url: str) -> None:
        url = build_combined_stream_url(base_url, settings.binance_symbol, settings.binance_intervals)
        service_health_store.set("binance_ws", "connecting", metadata={"endpoint": base_url})
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as websocket:
            service_health_store.set("binance_ws", "running", metadata={"endpoint": base_url})
            async for raw_message in websocket:
                candle = parse_ws_candle(raw_message)
                if candle is None:
                    continue
                candles = self._merge_live_candle(candle)
                indicator_points = calculate_indicator_points(candles, candle.interval)
                await market_ws_hub.broadcast(
                    candle.symbol,
                    candle.interval,
                    {
                        "type": "market.candle",
                        "symbol": candle.symbol,
                        "interval": candle.interval,
                        "candle": candle.model_dump(mode="json"),
                        "indicator": indicator_points[-1].model_dump(mode="json") if indicator_points else None,
                    },
                )

    def _replace_live_candles(self, symbol: str, interval: str, candles: list[Candle]) -> None:
        key = (symbol.upper(), interval)
        self._live_candles[key] = candles[-settings.candle_history_limit :]

    def _merge_live_candle(self, candle: Candle) -> list[Candle]:
        key = (candle.symbol.upper(), candle.interval)
        candles = self._live_candles.get(key, [])
        by_open_time = {item.open_time: item for item in candles}
        by_open_time[candle.open_time] = candle
        merged = sorted(by_open_time.values(), key=lambda item: item.open_time)
        merged = merged[-settings.candle_history_limit :]
        self._live_candles[key] = merged
        return merged


def build_combined_stream_url(base_url: str, symbol: str, intervals: list[str]) -> str:
    streams = "/".join(f"{symbol.lower()}@kline_{interval}" for interval in intervals)
    return f"{base_url.rstrip('/')}/stream?streams={streams}"


def parse_ws_candle(raw_message: str | bytes) -> Candle | None:
    payload = json.loads(raw_message)
    data: dict[str, Any] = payload.get("data", payload)
    if data.get("e") != "kline":
        return None
    kline = data.get("k")
    if not isinstance(kline, dict):
        return None
    interval = str(kline.get("i") or "")
    if interval not in settings.binance_intervals:
        return None
    return BinanceClient._parse_kline(
        symbol=str(data.get("s") or settings.binance_symbol).upper(),
        interval=interval,  # type: ignore[arg-type]
        row=[
            int(kline["t"]),
            kline["o"],
            kline["h"],
            kline["l"],
            kline["c"],
            kline["v"],
            int(kline["T"]),
        ],
    ).model_copy(update={"is_closed": bool(kline.get("x"))})


binance_monitor = BinanceMonitor()
