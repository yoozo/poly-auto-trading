from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.schemas.candle import Candle, Interval
from app.schemas.market_signal import MarketDataEvent
from app.services.candle_backfill import candle_sync_service
from app.services.binance_client import BinanceClient
from app.services.candle_store import list_candles
from app.services.market_signal_pipeline import market_signal_pipeline
from app.services.service_health import service_health_store

logger = logging.getLogger(__name__)

class BinanceMonitor:
    """Binance 数据源接入层：触发后台补数、连 WS、解析 K 线，不直接写 K 线库。"""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        if not settings.binance_ws_enabled:
            service_health_store.set("binance_ws", "idle")
            return
        self._tasks = [
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
        service_health_store.set("binance_rest", "running", metadata={"operation": "live_window_sync"})
        for interval in settings.binance_intervals:
            await self.refresh_live_window(settings.binance_symbol, interval)  # type: ignore[arg-type]
        service_health_store.set(
            "binance_rest",
            "idle",
            metadata={"operation": "live_window_sync"},
        )

    async def refresh_live_window(self, symbol: str, interval: Interval) -> None:
        async with AsyncSessionLocal() as session:
            # monitor 每分钟只补最近窗口，历史大缺口仍由 system_task 接管，避免和全量任务互相抢占。
            await candle_sync_service.ensure_latest_window(
                session,
                symbol=symbol,
                interval=interval,
                limit=settings.candle_history_limit,
            )
            cached = await list_candles(session, symbol=symbol, interval=interval, limit=settings.candle_history_limit)
        market_signal_pipeline.replace_live_candles(symbol, interval, cached)

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
                # Binance WS 事件转换成统一市场事件，后续信号逻辑不再依赖 Binance payload。
                await market_signal_pipeline.handle_market_event(
                    MarketDataEvent(source="binance_ws", candle=candle)
                )


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
    row = [
        int(kline["t"]),
        kline["o"],
        kline["h"],
        kline["l"],
        kline["c"],
        kline["v"],
        int(kline["T"]),
    ]
    candle = BinanceClient._parse_kline(
        symbol=str(data.get("s") or settings.binance_symbol).upper(),
        interval=interval,  # type: ignore[arg-type]
        row=row,
    ).model_copy(update={"is_closed": bool(kline.get("x"))})
    return candle


binance_monitor = BinanceMonitor()
