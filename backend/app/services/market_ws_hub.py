from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

logger = logging.getLogger(__name__)


class MarketWebSocketHub:
    def __init__(self) -> None:
        self._clients: dict[tuple[str, str], set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, symbol: str, interval: str) -> None:
        await websocket.accept()
        await self.subscribe(websocket, symbol, [interval])

    async def subscribe(self, websocket: WebSocket, symbol: str, intervals: list[str]) -> None:
        normalized_symbol = symbol.upper()
        async with self._lock:
            for interval in intervals:
                self._clients[(normalized_symbol, interval)].add(websocket)

    async def replace_subscription(
        self,
        websocket: WebSocket,
        symbol: str,
        previous_interval: str | None,
        next_interval: str,
    ) -> None:
        normalized_symbol = symbol.upper()
        async with self._lock:
            if previous_interval is not None:
                previous_key = (normalized_symbol, previous_interval)
                previous_clients = self._clients.get(previous_key)
                if previous_clients:
                    previous_clients.discard(websocket)
                    if not previous_clients:
                        self._clients.pop(previous_key, None)
            self._clients[(normalized_symbol, next_interval)].add(websocket)

    async def disconnect(self, websocket: WebSocket, symbol: str, interval: str) -> None:
        await self.disconnect_many(websocket, symbol, [interval])

    async def disconnect_many(self, websocket: WebSocket, symbol: str, intervals: list[str]) -> None:
        async with self._lock:
            for interval in intervals:
                key = (symbol.upper(), interval)
                clients = self._clients.get(key)
                if not clients:
                    continue
                clients.discard(websocket)
                if not clients:
                    self._clients.pop(key, None)

    async def broadcast(self, symbol: str, interval: str, payload: dict[str, Any]) -> None:
        key = (symbol.upper(), interval)
        async with self._lock:
            clients = list(self._clients.get(key, set()))
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
                logger.exception("Market websocket broadcast failed", extra={"symbol": symbol, "interval": interval})
                disconnected.append(websocket)

        for websocket in disconnected:
            await self.disconnect(websocket, symbol, interval)


market_ws_hub = MarketWebSocketHub()
