from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.services.binance_monitor import binance_monitor


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await binance_monitor.start()
    try:
        yield
    finally:
        await binance_monitor.stop()

