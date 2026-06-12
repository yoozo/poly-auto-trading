from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.schemas.candle import Candle, Interval


class BinanceClient:
    def __init__(self, base_url: str | None = None, timeout: float = 10.0) -> None:
        self._base_url = (base_url or settings.binance_rest_base_url).rstrip("/")
        self._timeout = timeout

    async def fetch_klines(self, symbol: str, interval: Interval, limit: int = 300) -> list[Candle]:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            response = await client.get(
                "/api/v3/klines",
                params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
            )
            response.raise_for_status()
            rows = response.json()
        return [self._parse_kline(symbol.upper(), interval, row) for row in rows]

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

