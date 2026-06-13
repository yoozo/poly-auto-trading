from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import websockets
from fastapi.encoders import jsonable_encoder

from app.core.config import settings
from app.services.polymarket_client import PolymarketClient, UP_DOWN_INTERVAL_TAGS
from app.services.polymarket_market_store import polymarket_up_down_store
from app.services.polymarket_ws_hub import polymarket_ws_hub
from app.services.service_health import service_health_store

logger = logging.getLogger(__name__)


class PolymarketSubscriptionChanged(RuntimeError):
    pass


class PolymarketMarketMonitor:
    """Polymarket marketChannel 接入层：发现 BTC Up/Down 市场，并维护实时盘口缓存。"""

    def __init__(self) -> None:
        self._client = PolymarketClient()
        self._tasks: list[asyncio.Task] = []
        self._token_change_event = asyncio.Event()
        self._refresh_event = asyncio.Event()
        self._last_market_refresh_at: datetime | None = None
        self._broadcast_lock = asyncio.Lock()
        self._pending_broadcast_intervals: set[str] = set()

    async def start(self) -> None:
        if not settings.polymarket_ws_enabled:
            service_health_store.set("polymarket_ws", "idle")
            return
        self._tasks = [
            asyncio.create_task(self.refresh_loop(), name="polymarket-market-refresh"),
            asyncio.create_task(self.ws_loop(), name="polymarket-market-ws"),
            asyncio.create_task(self.broadcast_loop(), name="polymarket-market-broadcast"),
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
        service_health_store.set("polymarket_ws", "stopped")

    async def refresh_loop(self) -> None:
        while True:
            try:
                await self.refresh_markets_once()
                self._last_market_refresh_at = datetime.now(timezone.utc)
                self._refresh_event.clear()
                await self.wait_until_next_refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Polymarket market refresh failed")
                service_health_store.set("polymarket", "error", last_error=str(exc), metadata={"operation": "refresh"})
                await asyncio.sleep(10)

    async def wait_until_next_refresh(self) -> None:
        delay = await self.next_refresh_delay()
        try:
            await asyncio.wait_for(self._refresh_event.wait(), timeout=delay)
        except TimeoutError:
            return

        self._refresh_event.clear()
        signal_delay = self.signal_refresh_delay()
        if signal_delay <= 0:
            return

        # WS 的市场集合事件可能连续到达；这里只保留“需要刷新一次”的语义，避免事件流退化成高频轮询。
        throttled_delay = min(signal_delay, await self.next_refresh_delay())
        await asyncio.sleep(throttled_delay)

    async def next_refresh_delay(self) -> float:
        now = datetime.now(timezone.utc)
        return calculate_next_refresh_delay(
            now=now,
            next_boundary=await polymarket_up_down_store.next_market_boundary(now),
            market_count=await polymarket_up_down_store.market_count(),
            fallback_seconds=settings.polymarket_market_refresh_seconds,
            boundary_window_seconds=settings.polymarket_market_boundary_refresh_window_seconds,
            empty_retry_seconds=settings.polymarket_market_empty_retry_seconds,
        )

    def signal_refresh_delay(self) -> float:
        return calculate_signal_refresh_delay(
            now=datetime.now(timezone.utc),
            last_refresh_at=self._last_market_refresh_at,
            min_interval_seconds=settings.polymarket_market_signal_refresh_min_seconds,
        )

    async def refresh_markets_once(self) -> None:
        previous_tokens = set(await polymarket_up_down_store.token_ids())
        for interval in UP_DOWN_INTERVAL_TAGS:
            markets = await self._client.fetch_btc_up_down_markets(
                interval=interval,
                limit=12,
                include_recent_closed=True,
            )
            await polymarket_up_down_store.replace_markets(interval, markets)
            await self.broadcast_snapshot(interval)
        current_tokens = set(await polymarket_up_down_store.token_ids())
        if current_tokens != previous_tokens:
            self._token_change_event.set()

    async def broadcast_loop(self) -> None:
        # WS 事件会先落入内存缓存；前端快照按固定节奏合并推送，避免盘口高频抖动造成 UI 过载。
        while True:
            await asyncio.sleep(settings.polymarket_ws_broadcast_interval_seconds)
            async with self._broadcast_lock:
                intervals = sorted(self._pending_broadcast_intervals)
                self._pending_broadcast_intervals.clear()
            for interval in intervals:
                await self.broadcast_snapshot(interval)

    async def ws_loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self._ws_once()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except PolymarketSubscriptionChanged:
                service_health_store.set("polymarket_ws", "reconnecting", metadata={"reason": "subscription_changed"})
                backoff = 1.0
            except Exception as exc:
                logger.exception("Polymarket market websocket failed")
                service_health_store.set("polymarket_ws", "reconnecting", last_error=str(exc))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _ws_once(self) -> None:
        token_ids = await self._wait_for_token_ids()
        service_health_store.set(
            "polymarket_ws",
            "connecting",
            metadata={"token_count": len(token_ids), "endpoint": settings.polymarket_ws_market_url},
        )
        async with websockets.connect(settings.polymarket_ws_market_url, ping_interval=None) as websocket:
            await websocket.send(json.dumps(subscription_payload(token_ids)))
            service_health_store.set("polymarket_ws", "running", metadata={"token_count": len(token_ids)})
            ping_task = asyncio.create_task(self._ping_loop(websocket), name="polymarket-market-ping")
            token_task = asyncio.create_task(self._token_change_event.wait(), name="polymarket-token-change")
            receive_task = asyncio.create_task(websocket.recv(), name="polymarket-market-recv")
            try:
                while True:
                    done, pending = await asyncio.wait(
                        {token_task, receive_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if token_task in done:
                        self._token_change_event.clear()
                        raise PolymarketSubscriptionChanged("Polymarket token subscription changed")
                    if receive_task in done:
                        raw_message = receive_task.result()
                        await self.handle_raw_message(raw_message)
                        receive_task = asyncio.create_task(websocket.recv(), name="polymarket-market-recv")
                    for task in pending:
                        if task.done():
                            task.result()
            finally:
                ping_task.cancel()
                token_task.cancel()
                receive_task.cancel()
                await cancel_tasks(ping_task, token_task, receive_task)

    async def _wait_for_token_ids(self) -> list[str]:
        while True:
            token_ids = await polymarket_up_down_store.token_ids()
            if token_ids:
                return token_ids
            await self.refresh_markets_once()
            await asyncio.sleep(3)

    async def _ping_loop(self, websocket: Any) -> None:
        while True:
            await asyncio.sleep(10)
            await websocket.send("PING")

    async def handle_raw_message(self, raw_message: str | bytes) -> None:
        text = raw_message.decode() if isinstance(raw_message, bytes) else raw_message
        if text.upper() in {"PONG", "PING"}:
            return
        payload = json.loads(text)
        messages = payload if isinstance(payload, list) else [payload]
        changed_intervals: set[str] = set()
        should_refresh_markets = False
        for message in messages:
            if not isinstance(message, dict):
                continue
            event_type = str(message.get("event_type") or "")
            if event_type in {"new_market", "market_resolved"}:
                should_refresh_markets = True
            intervals = await polymarket_up_down_store.apply_ws_message(message)
            changed_intervals.update(intervals)
        if should_refresh_markets:
            # WS 只能提示市场集合变化；真实 token/窗口仍由 refresh 统一发现并触发重订阅。
            self._refresh_event.set()
        await self.queue_broadcast(changed_intervals)

    async def queue_broadcast(self, intervals: set[str]) -> None:
        if not intervals:
            return
        async with self._broadcast_lock:
            self._pending_broadcast_intervals.update(intervals)

    async def broadcast_snapshot(self, interval: str) -> None:
        markets = await polymarket_up_down_store.list_markets(interval, limit=12)
        await polymarket_ws_hub.broadcast(
            interval,
            {
                "type": "polymarket.btc_up_down.snapshot",
                "interval": interval,
                "markets": jsonable_encoder(markets),
            },
        )


def subscription_payload(token_ids: list[str]) -> dict[str, Any]:
    return {
        "assets_ids": token_ids,
        "type": "market",
        "custom_feature_enabled": True,
    }


def calculate_next_refresh_delay(
    *,
    now: datetime,
    next_boundary: datetime | None,
    market_count: int,
    fallback_seconds: float,
    boundary_window_seconds: float,
    empty_retry_seconds: float,
) -> float:
    if market_count <= 0:
        return max(1.0, empty_retry_seconds)

    fallback = max(1.0, fallback_seconds)
    boundary_window = max(0.0, boundary_window_seconds)
    if next_boundary is None:
        return fallback

    boundary = next_boundary.astimezone(timezone.utc) if next_boundary.tzinfo else next_boundary.replace(tzinfo=timezone.utc)
    current = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    seconds_to_boundary = (boundary - current).total_seconds()
    if seconds_to_boundary <= 0:
        return 1.0
    if seconds_to_boundary <= boundary_window:
        return min(fallback, max(1.0, seconds_to_boundary + boundary_window))
    return min(fallback, max(1.0, seconds_to_boundary - boundary_window))


def calculate_signal_refresh_delay(
    *,
    now: datetime,
    last_refresh_at: datetime | None,
    min_interval_seconds: float,
) -> float:
    min_interval = max(0.0, min_interval_seconds)
    if last_refresh_at is None or min_interval <= 0:
        return 0.0

    last_refresh = last_refresh_at.astimezone(timezone.utc) if last_refresh_at.tzinfo else last_refresh_at.replace(tzinfo=timezone.utc)
    current = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return max(0.0, min_interval - (current - last_refresh).total_seconds())


async def cancel_tasks(*tasks: asyncio.Task) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


polymarket_market_monitor = PolymarketMarketMonitor()
