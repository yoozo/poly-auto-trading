from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.services.binance_monitor import binance_monitor
from app.services.polymarket_monitor import polymarket_market_monitor


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await binance_monitor.start()
    await polymarket_market_monitor.start()
    try:
        yield
    finally:
        await polymarket_market_monitor.stop()
        await binance_monitor.stop()
