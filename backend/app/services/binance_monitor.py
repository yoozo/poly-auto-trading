from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
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


@dataclass
class WsEndpointScore:
    latency_ms: float | None = None
    failures: int = 0
    last_probe_at: float = 0.0


class BinanceMonitor:
    """Binance 数据源接入层：触发后台补数、连 WS、解析 K 线，不直接写 K 线库。"""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self._ws_endpoint_scores: dict[str, WsEndpointScore] = {}
        self._ws_probe_lock = asyncio.Lock()

    async def start(self) -> None:
        if not settings.binance_ws_enabled:
            service_health_store.set("binance_ws", "idle")
            return
        # 先把后端指标计算所需的历史窗口装入内存，再接收 WS 增量，避免服务刚启动时用不完整窗口触发通知。
        try:
            await self.backfill_once()
        except Exception:
            logger.exception("Failed to prime Binance live candle windows")
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
            base_url = await self._select_ws_endpoint(base_urls)
            try:
                await self._ws_once(base_url)
                backoff = 1.0
                self._ws_endpoint_scores.setdefault(base_url, WsEndpointScore()).failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                score = self._ws_endpoint_scores.setdefault(base_url, WsEndpointScore())
                score.failures += 1
                score.latency_ms = None
                score.last_probe_at = 0.0
                next_base_url = await self._select_ws_endpoint(base_urls, force_probe=True)
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
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _select_ws_endpoint(self, base_urls: list[str], force_probe: bool = False) -> str:
        if not settings.binance_ws_adaptive_enabled:
            return base_urls[0]
        now = time.monotonic()
        needs_probe = force_probe or any(
            self._ws_endpoint_scores.get(url) is None
            or now - self._ws_endpoint_scores[url].last_probe_at
            >= settings.binance_ws_probe_interval_seconds
            for url in base_urls
        )
        if needs_probe:
            await self._probe_ws_endpoints(base_urls)
        # 未探测成功的入口排在最后；全部失败时仍保留配置顺序，给重连逻辑机会恢复。
        return min(
            base_urls,
            key=lambda url: (
                self._ws_endpoint_scores.get(url, WsEndpointScore()).latency_ms is None,
                self._ws_endpoint_scores.get(url, WsEndpointScore()).latency_ms or float("inf"),
                self._ws_endpoint_scores.get(url, WsEndpointScore()).failures,
            ),
        )

    async def _probe_ws_endpoints(self, base_urls: list[str]) -> None:
        async with self._ws_probe_lock:
            now = time.monotonic()
            if not any(
                self._ws_endpoint_scores.get(url) is None
                or now - self._ws_endpoint_scores[url].last_probe_at
                >= settings.binance_ws_probe_interval_seconds
                for url in base_urls
            ):
                return
            results = await asyncio.gather(
                *(self._probe_ws_endpoint(url) for url in base_urls),
                return_exceptions=True,
            )
            for url, result in zip(base_urls, results, strict=True):
                score = self._ws_endpoint_scores.setdefault(url, WsEndpointScore())
                score.last_probe_at = time.monotonic()
                if isinstance(result, Exception):
                    score.latency_ms = None
                    score.failures += 1
                    logger.warning(
                        "Binance websocket endpoint probe failed",
                        extra={"endpoint": url},
                        exc_info=(type(result), result, result.__traceback__),
                    )
                else:
                    score.latency_ms = result
                    score.failures = 0
            service_health_store.set(
                "binance_ws",
                "probing",
                metadata={
                    "endpoints": {
                        url: self._ws_endpoint_scores[url].latency_ms for url in base_urls
                    }
                },
            )

    async def _probe_ws_endpoint(self, base_url: str) -> float:
        url = build_combined_stream_url(base_url, settings.binance_symbol, settings.binance_intervals)
        started_at = time.monotonic()
        async with websockets.connect(
            url,
            open_timeout=settings.binance_ws_probe_timeout_seconds,
            ping_interval=None,
        ) as websocket:
            pong_waiter = await websocket.ping()
            await asyncio.wait_for(pong_waiter, timeout=settings.binance_ws_probe_timeout_seconds)
        return round((time.monotonic() - started_at) * 1000, 2)

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
