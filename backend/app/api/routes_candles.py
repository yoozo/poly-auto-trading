from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_websocket_auth
from app.core.config import settings
from app.db.session import get_session
from app.schemas.candle import Candle, IndicatorPoint, Interval
from app.services.binance_client import BinanceClient
from app.services.candle_backfill import (
    BINANCE_KLINE_LIMIT,
    INTERVAL_MS,
    CandleBackfillStatus,
    candle_backfill_runner,
)
from app.services.candle_store import list_candles, list_candles_between, upsert_candles
from app.services.indicators import calculate_indicator_points
from app.services.indicator_backfill import IndicatorBackfillStatus, indicator_backfill_runner
from app.services.market_ws_hub import market_ws_hub

router = APIRouter(tags=["candles"])

INDICATOR_WARMUP_BARS = 80


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
        start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        end = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
        cached = await list_candles_between(session, symbol=symbol, interval=interval, start=start, end=end)
        if has_matching_candle_count(cached, start_ms=start_ms, end_ms=end_ms, interval=interval):
            return cached
        await backfill_candles_between(
            session,
            symbol=symbol,
            interval=interval,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        return await list_candles_between(session, symbol=symbol, interval=interval, start=start, end=end)
    else:
        cached = await list_candles(session, symbol=symbol, interval=interval, limit=limit)
        # 最新窗口足够新时直接用数据库，避免前端刷新频繁打 Binance REST。
        if should_use_cached_candles(cached, interval=interval, limit=limit):
            return cached

    fetched = await BinanceClient().fetch_klines(
        symbol=symbol,
        interval=interval,
        limit=limit,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    await upsert_candles(session, fetched)
    return await list_candles(session, symbol=symbol, interval=interval, limit=limit)


def should_use_cached_candles(candles: list[Candle], interval: Interval, limit: int) -> bool:
    if len(candles) < limit:
        return False
    latest = candles[-1]
    freshness_window = timedelta(milliseconds=INTERVAL_MS[interval])
    return latest.close_time >= utc_now() - freshness_window


def has_matching_candle_count(
    candles: list[Candle],
    *,
    start_ms: int,
    end_ms: int,
    interval: Interval,
) -> bool:
    return len(candles) >= expected_candle_count(start_ms=start_ms, end_ms=end_ms, interval=interval)


def expected_candle_count(*, start_ms: int, end_ms: int, interval: Interval) -> int:
    interval_ms = INTERVAL_MS[interval]
    return ((end_ms - start_ms) // interval_ms) + 1


async def backfill_candles_between(
    session: AsyncSession,
    *,
    symbol: str,
    interval: Interval,
    start_ms: int,
    end_ms: int,
) -> int:
    fetched_count = 0
    interval_ms = INTERVAL_MS[interval]
    next_start_ms = start_ms
    client = BinanceClient()
    while next_start_ms <= end_ms:
        # Binance 单次最多返回 1000 根 K 线；这里按 open_time 游标分页，边拉边写库。
        candles = await client.fetch_klines(
            symbol=symbol,
            interval=interval,
            limit=BINANCE_KLINE_LIMIT,
            start_ms=next_start_ms,
            end_ms=end_ms,
        )
        if not candles:
            break
        await upsert_candles(session, candles)
        fetched_count += len(candles)
        following_start_ms = int(candles[-1].open_time.timestamp() * 1000) + interval_ms
        if following_start_ms <= next_start_ms:
            break
        next_start_ms = following_start_ms
        if len(candles) < BINANCE_KLINE_LIMIT:
            break
    return fetched_count


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/indicators", response_model=list[IndicatorPoint])
async def indicators(
    symbol: str = settings.binance_symbol,
    interval: Interval = Query("1m"),
    limit: int = Query(300, ge=1, le=1000),
    start_ms: int | None = Query(None, ge=0),
    end_ms: int | None = Query(None, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[IndicatorPoint]:
    if (start_ms is None) != (end_ms is None):
        raise HTTPException(status_code=400, detail="start_ms and end_ms must be provided together")
    if start_ms is not None and end_ms is not None and start_ms >= end_ms:
        raise HTTPException(status_code=400, detail="start_ms must be less than end_ms")

    if start_ms is not None and end_ms is not None:
        # 指标需要前置 K 线 warmup；响应仍只返回用户请求区间内的点。
        warmup_start_ms = max(0, start_ms - (INTERVAL_MS[interval] * INDICATOR_WARMUP_BARS))
        warmup_start = datetime.fromtimestamp(warmup_start_ms / 1000, tz=timezone.utc)
        start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        end = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
        candles = await list_candles_between(session, symbol=symbol, interval=interval, start=warmup_start, end=end)
        if not has_matching_candle_count(
            candles,
            start_ms=warmup_start_ms,
            end_ms=end_ms,
            interval=interval,
        ):
            await backfill_candles_between(
                session,
                symbol=symbol,
                interval=interval,
                start_ms=warmup_start_ms,
                end_ms=end_ms,
            )
            candles = await list_candles_between(session, symbol=symbol, interval=interval, start=warmup_start, end=end)
        points = calculate_indicator_points(candles, interval)
        return [point for candle, point in zip(candles, points) if start <= candle.open_time <= end]

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
    if not await require_websocket_auth(websocket):
        return
    normalized_symbol = symbol.upper()
    await market_ws_hub.connect(websocket, normalized_symbol, interval)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await market_ws_hub.disconnect(websocket, normalized_symbol, interval)
