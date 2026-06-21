from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_websocket_auth
from app.core.config import settings
from app.db.session import get_session
from app.schemas.candle import Candle, Interval
from app.services.candle_backfill import (
    CandleBackfillStatus,
    candle_backfill_runner,
    candle_sync_service,
)
from app.services.candle_store import list_candles, list_candles_between
from app.services.indicator_backfill import IndicatorBackfillStatus, indicator_backfill_runner
from app.services.market_ws_hub import market_ws_hub

router = APIRouter(tags=["candles"])

@router.get("/candles/backfill", response_model=CandleBackfillStatus)
async def candle_backfill_status() -> CandleBackfillStatus:
    return await candle_backfill_runner.status()


@router.post("/candles/backfill", response_model=CandleBackfillStatus)
async def start_candle_backfill(symbol: str = settings.binance_symbol) -> CandleBackfillStatus:
    return await candle_backfill_runner.start_all(symbol=symbol)


@router.get("/indicators/backfill", response_model=IndicatorBackfillStatus)
async def indicator_backfill_status() -> IndicatorBackfillStatus:
    return await indicator_backfill_runner.status()


@router.post("/indicators/backfill", response_model=IndicatorBackfillStatus)
async def start_indicator_backfill(symbol: str = settings.binance_symbol) -> IndicatorBackfillStatus:
    return await indicator_backfill_runner.start_all(symbol=symbol)


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

    if start_ms is not None and end_ms is not None:
        await candle_sync_service.ensure_range(
            session,
            symbol=symbol,
            interval=interval,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        end = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
        return await list_candles_between(session, symbol=symbol, interval=interval, start=start, end=end)
    await candle_sync_service.ensure_latest_window(session, symbol=symbol, interval=interval, limit=limit)
    return await list_candles(session, symbol=symbol, interval=interval, limit=limit)


@router.websocket("/ws/market")
async def market_websocket(
    websocket: WebSocket,
    symbol: str = settings.binance_symbol,
    interval: Interval = Query("1m"),
) -> None:
    if not await require_websocket_auth(websocket):
        return
    normalized_symbol = symbol.upper()
    await market_ws_hub.connect(websocket, normalized_symbol, interval)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await market_ws_hub.disconnect(websocket, normalized_symbol, interval)
