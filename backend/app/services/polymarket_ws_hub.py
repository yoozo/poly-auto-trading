from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

logger = logging.getLogger(__name__)


class PolymarketWebSocketHub:
    def __init__(self) -> None:
        self._clients: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, interval: str) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients[interval].add(websocket)

    async def disconnect(self, websocket: WebSocket, interval: str) -> None:
        async with self._lock:
            clients = self._clients.get(interval)
            if not clients:
                return
            clients.discard(websocket)
            if not clients:
                self._clients.pop(interval, None)

    async def broadcast(self, interval: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients.get(interval, set()))
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
                logger.exception("Polymarket websocket broadcast failed", extra={"interval": interval})
                disconnected.append(websocket)

        for websocket in disconnected:
            await self.disconnect(websocket, interval)


polymarket_ws_hub = PolymarketWebSocketHub()
