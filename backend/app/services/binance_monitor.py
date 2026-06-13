from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import websockets

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.schemas.candle import Candle, Interval
from app.schemas.market_signal import MarketDataEvent
from app.services.binance_client import BinanceClient
from app.services.candle_store import get_latest_candle, list_candles, upsert_candles
from app.services.market_signal_pipeline import market_signal_pipeline
from app.services.service_health import service_health_store

logger = logging.getLogger(__name__)

BINANCE_KLINE_LIMIT = 1000
INTERVAL_MS: dict[Interval, int] = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


class BinanceMonitor:
    """Binance 数据源接入层：只负责补历史、连 WS、解析 K 线，不直接计算信号。"""

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
        service_health_store.set("binance_rest", "running", metadata={"operation": "backfill"})
        for interval in settings.binance_intervals:
            await self.backfill_interval(settings.binance_symbol, interval)  # type: ignore[arg-type]

    async def backfill_interval(self, symbol: str, interval: Interval) -> None:
        async with AsyncSessionLocal() as session:
            latest = await get_latest_candle(session, symbol=symbol, interval=interval)

        if latest is None:
            # 首次启动没有数据库窗口时，直接拉最近一段历史作为指标 warmup 基础。
            candles = await self._client.fetch_klines(
                symbol=symbol,
                interval=interval,
                limit=settings.candle_history_limit,
            )
            async with AsyncSessionLocal() as session:
                await upsert_candles(session, candles)
                cached = await list_candles(session, symbol=symbol, interval=interval, limit=settings.candle_history_limit)
            market_signal_pipeline.replace_live_candles(symbol, interval, cached)
            return

        interval_ms = INTERVAL_MS[interval]
        start_ms = to_ms(latest.open_time)
        end_ms = to_ms(utc_now())
        while start_ms <= end_ms:
            # Binance 单次 K 线查询有上限，按 open_time 分页补齐中断期间缺失的数据。
            candles = await self._client.fetch_klines(
                symbol=symbol,
                interval=interval,
                limit=BINANCE_KLINE_LIMIT,
                start_ms=start_ms,
                end_ms=end_ms,
            )
            if not candles:
                break
            async with AsyncSessionLocal() as session:
                await upsert_candles(session, candles)
            next_start_ms = to_ms(candles[-1].open_time) + interval_ms
            if next_start_ms <= start_ms:
                break
            start_ms = next_start_ms
            if len(candles) < BINANCE_KLINE_LIMIT:
                break

        async with AsyncSessionLocal() as session:
            cached = await list_candles(session, symbol=symbol, interval=interval, limit=settings.candle_history_limit)
        # backfill 完成后刷新信号 pipeline 的内存窗口，后续 WS 增量才能基于完整历史计算指标。
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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


binance_monitor = BinanceMonitor()
