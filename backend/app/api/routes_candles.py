import asyncio
import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.core.config import settings
from app.db.session import get_session
from app.schemas.candle import Candle, IndicatorPoint, Interval
from app.services.binance_client import BinanceClient
from app.services.candle_store import list_candles, upsert_candles
from app.services.indicators import calculate_indicator_points

router = APIRouter(tags=["candles"])


@router.get("/candles", response_model=list[Candle])
async def candles(
    symbol: str = settings.binance_symbol,
    interval: Interval = Query("1m"),
    limit: int = Query(300, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[Candle]:
    fetched = await BinanceClient().fetch_klines(symbol=symbol, interval=interval, limit=limit)
    await upsert_candles(session, fetched)
    return await list_candles(session, symbol=symbol, interval=interval, limit=limit)


@router.get("/indicators", response_model=list[IndicatorPoint])
async def indicators(
    symbol: str = settings.binance_symbol,
    interval: Interval = Query("1m"),
    limit: int = Query(300, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[IndicatorPoint]:
    candles = await list_candles(session, symbol=symbol, interval=interval, limit=limit)
    if len(candles) < min(limit, 30):
        fetched = await BinanceClient().fetch_klines(symbol=symbol, interval=interval, limit=limit)
        await upsert_candles(session, fetched)
        candles = await list_candles(session, symbol=symbol, interval=interval, limit=limit)
    return calculate_indicator_points(candles, interval)


@router.get("/events/stream")
async def event_stream(
    symbol: str = settings.binance_symbol,
    interval: Interval = Query("1m"),
) -> StreamingResponse:
    async def generate():
        while True:
            async with get_session_context() as session:
                candles = await list_candles(session, symbol=symbol, interval=interval, limit=300)
            points = calculate_indicator_points(candles, interval)
            latest_candle = candles[-1].model_dump(mode="json") if candles else None
            latest_indicator = points[-1].model_dump(mode="json") if points else None
            payload = {
                "type": "candle.updated",
                "symbol": symbol.upper(),
                "interval": interval,
                "candle": latest_candle,
                "indicator": latest_indicator,
            }
            yield f"event: candle.updated\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(generate(), media_type="text/event-stream")


class get_session_context:
    def __init__(self) -> None:
        self._generator = get_session()

    async def __aenter__(self) -> AsyncSession:
        return await self._generator.__anext__()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._generator.aclose()
