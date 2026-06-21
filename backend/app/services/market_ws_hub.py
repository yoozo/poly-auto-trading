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
        async with self._lock:
            self._clients[(symbol.upper(), interval)].add(websocket)

    async def disconnect(self, websocket: WebSocket, symbol: str, interval: str) -> None:
        async with self._lock:
            key = (symbol.upper(), interval)
            clients = self._clients.get(key)
            if not clients:
                return
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
