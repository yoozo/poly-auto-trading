from typing import Any

import pytest
from starlette.websockets import WebSocketState

from app.services.polymarket_ws_hub import PolymarketWebSocketHub


class RecordingWebSocket:
    client_state = WebSocketState.CONNECTED

    async def accept(self) -> None:
        return None

    async def send_json(self, payload: dict[str, Any]) -> None:
        return None


@pytest.mark.asyncio
async def test_replace_subscription_moves_client_to_new_interval() -> None:
    hub = PolymarketWebSocketHub()
    websocket = RecordingWebSocket()
    await hub.connect(websocket, "5m")  # type: ignore[arg-type]

    await hub.replace_subscription(websocket, "5m", "15m")  # type: ignore[arg-type]

    assert "5m" not in hub._interval_clients
    assert hub._interval_clients["15m"] == {websocket}


@pytest.mark.asyncio
async def test_replace_market_subscription_tracks_active_market_ids() -> None:
    hub = PolymarketWebSocketHub()
    websocket = RecordingWebSocket()
    await hub.connect(websocket, "5m")  # type: ignore[arg-type]

    changed = await hub.replace_market_subscription(websocket, "5m", "market-1")  # type: ignore[arg-type]

    assert changed == {"market-1"}
    assert await hub.active_market_ids() == {"market-1"}
    assert hub._market_clients["market-1"] == {websocket}
