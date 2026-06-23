from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_websocket_auth
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.db.session import get_session
from app.schemas.candle import Candle, Interval
from app.services.candle_backfill import (
    CandleBackfillStatus,
    candle_backfill_runner,
    candle_sync_service,
)
from app.services.candle_store import list_candles, list_candles_between
from app.services.indicator_backfill import IndicatorBackfillStatus, indicator_backfill_runner
from app.services.market_signal_pipeline import market_signal_pipeline
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
    cached = await list_candles(session, symbol=symbol, interval=interval, limit=limit)
    live_candles = market_signal_pipeline.get_live_candles(symbol, interval, limit=limit)
    return merge_live_candles(cached, live_candles, limit)


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
    initial_payload = await initial_market_payload(normalized_symbol, interval)
    if initial_payload is not None:
        # WS 新连接先补一帧快照，避免前端等下一次 Binance tick 才看到 K 线。
        await websocket.send_json(initial_payload)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await market_ws_hub.disconnect(websocket, normalized_symbol, interval)


async def initial_market_payload(symbol: str, interval: Interval) -> dict[str, object] | None:
    live_payload = market_signal_pipeline.latest_market_payload(symbol, interval)
    if live_payload is not None:
        return live_payload
    async with AsyncSessionLocal() as session:
        cached = await list_candles(session, symbol=symbol, interval=interval, limit=settings.candle_history_limit)
    # live window 冷启动时用 DB 最近窗口兜底，让 WS 建连后立即有首帧；后续 Binance WS 会覆盖未收盘 K 线。
    return market_signal_pipeline.market_payload_from_candles(
        symbol,
        interval,
        cached,
    )


def merge_live_candles(cached: list[Candle], live_candles: list[Candle], limit: int) -> list[Candle]:
    # DB 只保存已闭合 K 线；latest 接口在出口合并内存态，首屏可直接带出当前未收盘 K 线。
    by_open_time = {candle.open_time: candle for candle in cached}
    for candle in live_candles:
        by_open_time[candle.open_time] = candle
    return sorted(by_open_time.values(), key=lambda candle: candle.open_time)[-limit:]
