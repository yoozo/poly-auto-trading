from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.schemas.candle import Candle, Interval
from app.services.binance_client import BinanceClient
from app.services.candle_store import upsert_candles
from app.services.service_health import service_health_store

logger = logging.getLogger(__name__)


class BinanceMonitor:
    def __init__(self) -> None:
        self._client = BinanceClient()
        self._tasks: list[asyncio.Task] = []

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
        service_health_store.set("binance_rest", "running")
        for interval in settings.binance_intervals:
            candles = await self._client.fetch_klines(
                symbol=settings.binance_symbol,
                interval=interval,  # type: ignore[arg-type]
                limit=settings.candle_history_limit,
            )
            async with AsyncSessionLocal() as session:
                await upsert_candles(session, candles)
        service_health_store.set("binance_rest", "running")

    async def ws_loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self._ws_once()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Binance websocket failed")
                service_health_store.set("binance_ws", "reconnecting", last_error=str(exc))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _ws_once(self) -> None:
        streams = "/".join(
            f"{settings.binance_symbol.lower()}@kline_{interval}"
            for interval in settings.binance_intervals
        )
        url = f"{settings.binance_ws_base_url.rstrip('/')}/stream?streams={streams}"
        service_health_store.set("binance_ws", "connecting")
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as websocket:
            service_health_store.set("binance_ws", "running")
            async for raw_message in websocket:
                candle = parse_ws_candle(raw_message)
                if candle is None:
                    continue
                async with AsyncSessionLocal() as session:
                    await upsert_candles(session, [candle])


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

