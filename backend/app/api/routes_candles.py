from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session
from app.schemas.candle import Candle, IndicatorPoint, Interval
from app.services.binance_client import BinanceClient
from app.services.candle_store import list_candles, list_candles_between, upsert_candles
from app.services.indicators import calculate_indicator_points
from app.services.market_ws_hub import market_ws_hub

router = APIRouter(tags=["candles"])


@router.get("/candles", response_model=list[Candle])
async def candles(
    symbol: str = settings.binance_symbol,
    interval: Interval = Query("1m"),
    limit: int = Query(300, ge=1, le=1000),
    start_ms: int | None = Query(None, ge=0),
    end_ms: int | None = Query(None, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[Candle]:
    if (start_ms is None) != (end_ms is None):
        raise HTTPException(status_code=400, detail="start_ms and end_ms must be provided together")
    if start_ms is not None and end_ms is not None and start_ms >= end_ms:
        raise HTTPException(status_code=400, detail="start_ms must be less than end_ms")

    fetched = await BinanceClient().fetch_klines(
        symbol=symbol,
        interval=interval,
        limit=limit,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    await upsert_candles(session, fetched)
    if start_ms is not None and end_ms is not None:
        start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        end = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
        return await list_candles_between(session, symbol=symbol, interval=interval, start=start, end=end)
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


@router.websocket("/ws/market")
async def market_websocket(
    websocket: WebSocket,
    symbol: str = settings.binance_symbol,
    interval: Interval = Query("1m"),
) -> None:
    normalized_symbol = symbol.upper()
    await market_ws_hub.connect(websocket, normalized_symbol, interval)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await market_ws_hub.disconnect(websocket, normalized_symbol, interval)
