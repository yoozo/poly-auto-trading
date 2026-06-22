from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

logger = logging.getLogger(__name__)


class PolymarketAccountWebSocketHub:
    def __init__(self) -> None:
        self._clients: dict[str | None, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, condition_id: str | None) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients[normalize_condition(condition_id)].add(websocket)

    async def disconnect(self, websocket: WebSocket, condition_id: str | None) -> None:
        key = normalize_condition(condition_id)
        async with self._lock:
            clients = self._clients.get(key)
            if not clients:
                return
            clients.discard(websocket)
            if not clients:
                self._clients.pop(key, None)

    async def broadcast(self, payload: dict[str, Any], condition_id: str | None = None) -> None:
        keys = [normalize_condition(condition_id)]
        clients: list[tuple[str | None, WebSocket]] = []
        async with self._lock:
            for key in keys:
                clients.extend((key, websocket) for websocket in self._clients.get(key, set()))
        disconnected: list[tuple[str | None, WebSocket]] = []
        for key, websocket in clients:
            if websocket.client_state != WebSocketState.CONNECTED:
                disconnected.append((key, websocket))
                continue
            try:
                await websocket.send_json(payload)
            except WebSocketDisconnect:
                disconnected.append((key, websocket))
            except Exception:
                logger.exception("Polymarket account websocket broadcast failed", extra={"condition_id": condition_id})
                disconnected.append((key, websocket))
        for key, websocket in disconnected:
            await self.disconnect(websocket, key)


def normalize_condition(condition_id: str | None) -> str | None:
    return condition_id.lower() if condition_id else None


polymarket_account_ws_hub = PolymarketAccountWebSocketHub()
