import logging
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.services.market_ws_hub import MarketWebSocketHub


class DisconnectingWebSocket:
    client_state = WebSocketState.CONNECTED

    async def accept(self) -> None:
        return None

    async def send_json(self, payload: dict[str, Any]) -> None:
        raise WebSocketDisconnect(code=1006)


@pytest.mark.asyncio
async def test_broadcast_removes_disconnected_client_without_error_log(caplog: pytest.LogCaptureFixture) -> None:
    hub = MarketWebSocketHub()
    websocket = DisconnectingWebSocket()
    await hub.connect(websocket, "BTCUSDT", "1m")  # type: ignore[arg-type]

    with caplog.at_level(logging.ERROR, logger="app.services.market_ws_hub"):
        await hub.broadcast("BTCUSDT", "1m", {"type": "signal"})

    assert hub._clients == {}
    assert "Market websocket broadcast failed" not in caplog.text
