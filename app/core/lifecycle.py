from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.services.binance_data import binance_data_service
from app.services.polymarket_market import polymarket_market_service
from app.services.polymarket_ws import polymarket_market_ws_service


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await binance_data_service.backfill_all_intervals()
    binance_ws_task = asyncio.create_task(binance_data_service.run_ws_forever())
    polymarket_refresh_task = asyncio.create_task(polymarket_market_service.run_refresh_loop())
    polymarket_ws_task = asyncio.create_task(polymarket_market_ws_service.run_ws_forever())
    try:
        yield
    finally:
        for task in (binance_ws_task, polymarket_refresh_task, polymarket_ws_task):
            task.cancel()
        for task in (binance_ws_task, polymarket_refresh_task, polymarket_ws_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
