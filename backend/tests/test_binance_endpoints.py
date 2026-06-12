from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services import binance_client
from app.services.binance_client import BinanceClient
from app.services.binance_monitor import build_combined_stream_url


KLINE_ROW = [
    1767225600000,
    "100",
    "101",
    "99",
    "100.5",
    "12.3",
    1767225659999,
]


class FakeResponse:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._rows


def make_fake_async_client(failures: dict[str, Exception], calls: list[str]):
    class FakeAsyncClient:
        def __init__(self, base_url: str, timeout: float) -> None:
            self.base_url = base_url
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, path: str, params: dict):
            calls.append(self.base_url)
            if self.base_url in failures:
                raise failures[self.base_url]
            assert path == "/api/v3/klines"
            assert params["symbol"] == "BTCUSDT"
            return FakeResponse([KLINE_ROW])

    return FakeAsyncClient


@pytest.mark.asyncio
async def test_fetch_klines_falls_back_to_next_endpoint(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        binance_client.httpx,
        "AsyncClient",
        make_fake_async_client({"https://bad.example": RuntimeError("down")}, calls),
    )

    candles = await BinanceClient(
        base_urls=["https://bad.example", "https://good.example"]
    ).fetch_klines(symbol="BTCUSDT", interval="1m")

    assert calls == ["https://bad.example", "https://good.example"]
    assert len(candles) == 1
    assert candles[0].close == 100.5


@pytest.mark.asyncio
async def test_fetch_klines_raises_last_error_when_all_endpoints_fail(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        binance_client.httpx,
        "AsyncClient",
        make_fake_async_client(
            {
                "https://first.example": RuntimeError("first failed"),
                "https://second.example": RuntimeError("second failed"),
            },
            calls,
        ),
    )

    with pytest.raises(RuntimeError, match="second failed"):
        await BinanceClient(
            base_urls=["https://first.example", "https://second.example"]
        ).fetch_klines(symbol="BTCUSDT", interval="1m")

    assert calls == ["https://first.example", "https://second.example"]


def test_binance_endpoint_config_parses_csv_values() -> None:
    settings = Settings(
        _env_file=None,
        binance_rest_base_urls_raw=" https://a.example , , https://b.example/ ",
        binance_ws_base_urls_raw=" wss://a.example/ws , , wss://b.example/ws/ ",
    )

    assert settings.binance_rest_base_urls == ["https://a.example", "https://b.example"]
    assert settings.binance_ws_base_urls == ["wss://a.example/ws", "wss://b.example/ws"]


def test_binance_endpoint_config_keeps_legacy_single_endpoint_first() -> None:
    settings = Settings(_env_file=None, binance_rest_base_url="https://custom.example")

    assert settings.binance_rest_base_urls[0] == "https://custom.example"
    assert "https://api.binance.com" in settings.binance_rest_base_urls


def test_build_combined_stream_url() -> None:
    url = build_combined_stream_url("wss://stream.binance.com:9443/", "BTCUSDT", ["1m", "5m"])

    assert url == (
        "wss://stream.binance.com:9443/stream?"
        "streams=btcusdt@kline_1m/btcusdt@kline_5m"
    )
