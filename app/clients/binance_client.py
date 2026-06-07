from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.schemas import Candle, Interval


class BinanceClient:
    def __init__(self, rest_base_urls: list[str], timeout: float = 10.0) -> None:
        if not rest_base_urls:
            raise ValueError("At least one Binance REST base URL is required")
        self._rest_base_urls = [url.rstrip("/") for url in rest_base_urls]
        self._timeout = timeout

    async def fetch_klines(self, symbol: str, interval: Interval, limit: int) -> list[Candle]:
        payload = await self._request_with_failover(
            path="/api/v3/klines",
            params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
        )
        return [self._parse_kline(symbol=symbol.upper(), interval=interval, row=row) for row in payload]

    async def _request_with_failover(self, path: str, params: dict[str, str | int]) -> list[Any]:
        errors: list[str] = []
        for base_url in self._rest_base_urls:
            try:
                async with httpx.AsyncClient(base_url=base_url, timeout=self._timeout) as client:
                    response = await client.get(path, params=params)
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPError as exc:
                errors.append(f"{base_url}: {exc}")
        raise RuntimeError(f"All Binance REST endpoints failed: {'; '.join(errors)}")

    @staticmethod
    def _parse_kline(symbol: str, interval: Interval, row: list[Any]) -> Candle:
        close_time = _from_ms(row[6])
        return Candle(
            symbol=symbol,
            interval=interval,
            open_time=_from_ms(row[0]),
            close_time=close_time,
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            is_closed=close_time <= datetime.now(timezone.utc),
        )


def _from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
