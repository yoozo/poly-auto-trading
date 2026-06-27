from datetime import datetime, timezone
import json
from typing import Any, Literal, TypeAlias, cast, get_args

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
VALID_INTERVALS = set(get_args(Interval))

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
    snapshot = await load_candles_snapshot(
        session,
        symbol=symbol,
        interval=interval,
        limit=limit,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    return snapshot.candles


@router.websocket("/ws/market")
async def market_websocket(
    websocket: WebSocket,
    symbol: str = settings.binance_symbol,
    interval: Interval = Query("1m"),
) -> None:
    if not await require_websocket_auth(websocket):
        return
    normalized_symbol = symbol.upper()
    current_interval: Interval = interval
    await market_ws_hub.connect(websocket, normalized_symbol, current_interval)
    await send_initial_market_payload(websocket, normalized_symbol, current_interval)
    try:
        while True:
            raw_message = await websocket.receive_text()
            client_message = parse_market_ws_message(raw_message)
            if client_message is None:
                continue
            if client_message["type"] == "market.candles.request":
                await send_candles_snapshot(websocket, client_message)
                continue
            next_interval = client_message["interval"]
            if next_interval == current_interval:
                await send_initial_market_payload(websocket, normalized_symbol, current_interval)
                continue
            # 同一条 WS 连接只订阅一个周期；切换周期时替换 hub 注册并立刻补发新周期首帧。
            await market_ws_hub.replace_subscription(websocket, normalized_symbol, current_interval, next_interval)
            current_interval = next_interval
            await send_initial_market_payload(websocket, normalized_symbol, current_interval)
    except WebSocketDisconnect:
        await market_ws_hub.disconnect(websocket, normalized_symbol, current_interval)


class CandleSnapshot:
    def __init__(self, mode: Literal["latest", "range"], candles: list[Candle]) -> None:
        self.mode = mode
        self.candles = candles


MarketSubscribeMessage: TypeAlias = dict[Literal["type", "interval"], Literal["market.subscribe"] | Interval]
MarketCandlesRequestMessage: TypeAlias = dict[str, Any]
MarketWsClientMessage: TypeAlias = MarketSubscribeMessage | MarketCandlesRequestMessage


def parse_market_subscribe_message(raw_message: str) -> Interval | None:
    message = parse_market_ws_message(raw_message)
    if message is None or message["type"] != "market.subscribe":
        return None
    return cast(Interval, message["interval"])


def parse_market_ws_message(raw_message: str) -> MarketWsClientMessage | None:
    try:
        payload = json.loads(raw_message)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("type") == "market.candles.request":
        return parse_market_candles_request(payload)
    if payload.get("type") != "market.subscribe":
        return None
    interval = payload.get("interval")
    if not isinstance(interval, str) or interval not in VALID_INTERVALS:
        return None
    return {"type": "market.subscribe", "interval": cast(Interval, interval)}


def parse_market_candles_request(payload: dict[str, object]) -> MarketCandlesRequestMessage:
    request_id = payload.get("request_id")
    symbol = payload.get("symbol", settings.binance_symbol)
    interval = payload.get("interval")
    limit = payload.get("limit", 300)
    start_ms = payload.get("start_ms")
    end_ms = payload.get("end_ms")
    message: MarketCandlesRequestMessage = {
        "type": "market.candles.request",
        "request_id": request_id if isinstance(request_id, str) else "",
        "symbol": symbol.upper() if isinstance(symbol, str) else settings.binance_symbol,
        "interval": interval if isinstance(interval, str) else "",
        "limit": limit,
        "start_ms": start_ms,
        "end_ms": end_ms,
    }
    return message


async def send_candles_snapshot(websocket: WebSocket, message: MarketCandlesRequestMessage) -> None:
    request_id = cast(str, message.get("request_id") or "")
    try:
        symbol = validate_market_request_symbol(message.get("symbol"))
        interval = validate_market_request_interval(message.get("interval"))
        limit = validate_market_request_limit(message.get("limit"))
        start_ms, end_ms = validate_market_request_range(message.get("start_ms"), message.get("end_ms"))
        async with AsyncSessionLocal() as session:
            snapshot = await load_candles_snapshot(
                session,
                symbol=symbol,
                interval=interval,
                limit=limit,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        # BTC watch 的历史窗口也走这条 WS 响应；语义保持和 REST /api/candles 一致。
        await websocket.send_json(
            {
                "type": "market.candles.snapshot",
                "request_id": request_id,
                "symbol": symbol,
                "interval": interval,
                "mode": snapshot.mode,
                "candles": [candle.model_dump(mode="json") for candle in snapshot.candles],
            }
        )
    except ValueError as exc:
        await websocket.send_json(
            {
                "type": "market.candles.error",
                "request_id": request_id,
                "message": str(exc),
            }
        )


def validate_market_request_symbol(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("symbol must be a non-empty string")
    return value.upper()


def validate_market_request_interval(value: object) -> Interval:
    if not isinstance(value, str) or value not in VALID_INTERVALS:
        raise ValueError("interval must be a valid candle interval")
    return cast(Interval, value)


def validate_market_request_limit(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 1000:
        raise ValueError("limit must be between 1 and 1000")
    return value


def validate_market_request_range(start_value: object, end_value: object) -> tuple[int | None, int | None]:
    if (start_value is None) != (end_value is None):
        raise ValueError("start_ms and end_ms must be provided together")
    if start_value is None and end_value is None:
        return None, None
    if (
        not isinstance(start_value, int)
        or isinstance(start_value, bool)
        or not isinstance(end_value, int)
        or isinstance(end_value, bool)
    ):
        raise ValueError("start_ms and end_ms must be integer timestamps")
    if start_value < 0 or end_value < 0:
        raise ValueError("start_ms and end_ms must be greater than or equal to 0")
    if start_value >= end_value:
        raise ValueError("start_ms must be less than end_ms")
    return start_value, end_value


async def send_initial_market_payload(websocket: WebSocket, symbol: str, interval: Interval) -> None:
    initial_payload = await initial_market_payload(symbol, interval)
    if initial_payload is not None:
        # WS 新连接或订阅切换后先补一帧快照，避免前端等下一次 Binance tick 才看到 K 线。
        await websocket.send_json(initial_payload)


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


async def load_candles_snapshot(
    session: AsyncSession,
    *,
    symbol: str,
    interval: Interval,
    limit: int,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> CandleSnapshot:
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
        candles = await list_candles_between(session, symbol=symbol, interval=interval, start=start, end=end)
        return CandleSnapshot("range", candles)

    await candle_sync_service.ensure_latest_window(session, symbol=symbol, interval=interval, limit=limit)
    cached = await list_candles(session, symbol=symbol, interval=interval, limit=limit)
    live_candles = market_signal_pipeline.get_live_candles(symbol, interval, limit=limit)
    return CandleSnapshot("latest", merge_live_candles(cached, live_candles, limit))


def merge_live_candles(cached: list[Candle], live_candles: list[Candle], limit: int) -> list[Candle]:
    # DB 只保存已闭合 K 线；latest 接口在出口合并内存态，首屏可直接带出当前未收盘 K 线。
    by_open_time = {candle.open_time: candle for candle in cached}
    for candle in live_candles:
        by_open_time[candle.open_time] = candle
    return sorted(by_open_time.values(), key=lambda candle: candle.open_time)[-limit:]
