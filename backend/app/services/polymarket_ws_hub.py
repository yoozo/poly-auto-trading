from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

logger = logging.getLogger(__name__)


@dataclass
class PolymarketClientSubscription:
    interval: str
    market_id: str | None = None


class PolymarketWebSocketHub:
    def __init__(self) -> None:
        self._clients: dict[WebSocket, PolymarketClientSubscription] = {}
        self._interval_clients: dict[str, set[WebSocket]] = defaultdict(set)
        self._market_clients: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, interval: str) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients[websocket] = PolymarketClientSubscription(interval=interval)
            self._interval_clients[interval].add(websocket)

    async def replace_interval_subscription(self, websocket: WebSocket, previous_interval: str | None, next_interval: str) -> None:
        async with self._lock:
            if previous_interval is not None:
                previous_clients = self._interval_clients.get(previous_interval)
                if previous_clients:
                    previous_clients.discard(websocket)
                    if not previous_clients:
                        self._interval_clients.pop(previous_interval, None)
            current = self._clients.get(websocket)
            previous_market_id = current.market_id if current else None
            if previous_market_id:
                self._remove_market_client(websocket, previous_market_id)
            self._clients[websocket] = PolymarketClientSubscription(interval=next_interval)
            self._interval_clients[next_interval].add(websocket)

    async def replace_subscription(self, websocket: WebSocket, previous_interval: str | None, next_interval: str) -> None:
        await self.replace_interval_subscription(websocket, previous_interval, next_interval)

    async def replace_market_subscription(self, websocket: WebSocket, interval: str, market_id: str) -> set[str]:
        async with self._lock:
            current = self._clients.get(websocket)
            previous_market_ids = set(self._market_clients)
            if current is None or current.interval != interval:
                if current is not None:
                    previous_clients = self._interval_clients.get(current.interval)
                    if previous_clients:
                        previous_clients.discard(websocket)
                        if not previous_clients:
                            self._interval_clients.pop(current.interval, None)
                self._interval_clients[interval].add(websocket)
                current = PolymarketClientSubscription(interval=interval)
                self._clients[websocket] = current
            if current.market_id and current.market_id != market_id:
                self._remove_market_client(websocket, current.market_id)
            current.market_id = market_id
            self._market_clients[market_id].add(websocket)
            current_market_ids = set(self._market_clients)
        return previous_market_ids ^ current_market_ids

    async def disconnect(self, websocket: WebSocket, interval: str) -> None:
        async with self._lock:
            current = self._clients.pop(websocket, None)
            interval_key = current.interval if current else interval
            clients = self._interval_clients.get(interval_key)
            if clients:
                clients.discard(websocket)
                if not clients:
                    self._interval_clients.pop(interval_key, None)
            if current and current.market_id:
                self._remove_market_client(websocket, current.market_id)

    async def active_market_ids(self) -> set[str]:
        async with self._lock:
            return set(self._market_clients)

    async def broadcast_markets(self, interval: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._interval_clients.get(interval, set()))
        await self._send_to_clients(clients, payload, extra={"interval": interval})

    async def broadcast_market(self, market_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._market_clients.get(market_id, set()))
        await self._send_to_clients(clients, payload, extra={"market_id": market_id})

    async def broadcast(self, interval: str, payload: dict[str, Any]) -> None:
        await self.broadcast_markets(interval, payload)

    async def _send_to_clients(self, clients: list[WebSocket], payload: dict[str, Any], *, extra: dict[str, Any]) -> None:
        if not clients:
            return

        disconnected: list[WebSocket] = []
        for websocket in clients:
            if websocket.client_state != WebSocketState.CONNECTED:
                disconnected.append(websocket)
                continue
            try:
                await websocket.send_json(payload)
            except WebSocketDisconnect:
                disconnected.append(websocket)
            except Exception:
                logger.exception("Polymarket websocket broadcast failed", extra=extra)
                disconnected.append(websocket)

        for websocket in disconnected:
            await self.disconnect(websocket, "")

    def _remove_market_client(self, websocket: WebSocket, market_id: str) -> None:
        market_clients = self._market_clients.get(market_id)
        if not market_clients:
            return
        market_clients.discard(websocket)
        if not market_clients:
            self._market_clients.pop(market_id, None)


polymarket_ws_hub = PolymarketWebSocketHub()
