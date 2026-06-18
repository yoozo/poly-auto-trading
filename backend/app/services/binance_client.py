from datetime import datetime, timezone
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.schemas.candle import Candle, Interval
from app.services.external_http import with_retry
from app.services.service_health import service_health_store

logger = logging.getLogger(__name__)


class BinanceClient:
    def __init__(
        self,
        base_url: str | None = None,
        base_urls: list[str] | None = None,
        timeout: float = 10.0,
    ) -> None:
        if base_urls is not None:
            self._base_urls = [url.rstrip("/") for url in base_urls if url.strip()]
        elif base_url is not None:
            self._base_urls = [base_url.rstrip("/")]
        else:
            self._base_urls = settings.binance_rest_base_urls
        self._timeout = timeout

    async def fetch_klines(
        self,
        symbol: str,
        interval: Interval,
        limit: int = 300,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[Candle]:
        params: dict[str, str | int] = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit,
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        last_error: Exception | None = None
        for base_url in self._base_urls:
            try:
                async with httpx.AsyncClient(base_url=base_url, timeout=self._timeout) as client:
                    response = await with_retry(
                        lambda: fetch_raised(client, "/api/v3/klines", params=params)
                    )
                    rows = response.json()
                service_health_store.set(
                    "binance_rest",
                    "running",
                    metadata={"endpoint": base_url, "symbol": symbol.upper(), "interval": interval},
                )
                return [self._parse_kline(symbol.upper(), interval, row) for row in rows]
            except Exception as exc:
                last_error = RuntimeError(f"{base_url}: {type(exc).__name__}: {exc or 'connection failed'}")
                logger.warning(
                    "Binance REST endpoint failed",
                    extra={
                        "endpoint": base_url,
                        "symbol": symbol.upper(),
                        "interval": interval,
                    },
                    exc_info=exc,
                )
                service_health_store.set(
                    "binance_rest",
                    "error",
                    last_error=str(last_error),
                    metadata={"endpoint": base_url, "symbol": symbol.upper(), "interval": interval},
                )
                continue
        if last_error is None:
            last_error = RuntimeError("No Binance REST endpoints configured")
        logger.error(
            "All Binance REST endpoints failed",
            extra={"symbol": symbol.upper(), "interval": interval},
            exc_info=(type(last_error), last_error, last_error.__traceback__),
        )
        raise last_error

    @staticmethod
    def _parse_kline(symbol: str, interval: Interval, row: list[Any]) -> Candle:
        try:
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
        except Exception as exc:
            logger.warning(
                "Rejecting invalid Binance kline",
                extra={"symbol": symbol, "interval": interval, "row": row},
                exc_info=exc,
            )
            raise ValueError("Invalid Binance kline") from exc


def _from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


async def fetch_raised(
    client: httpx.AsyncClient,
    path: str,
    *,
    params: dict[str, str | int],
) -> httpx.Response:
    response = await client.get(path, params=params)
    response.raise_for_status()
    return response
