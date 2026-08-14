from datetime import datetime, timezone
import json
from typing import Any, Literal, TypeAlias, cast, get_args

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_websocket_auth
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.db.session import get_session
from app.schemas.candle import Candle, Interval, MarketCandle
from app.services.candle_backfill import (
    CandleBackfillStatus,
    candle_backfill_runner,
    candle_sync_service,
)
from app.services.candle_intervals import CANDLE_INTERVAL_MS
from app.services.candle_store import (
    get_earliest_candle_time,
    list_candles,
    list_candles_between,
)
from app.services.indicator_backfill import IndicatorBackfillStatus, indicator_backfill_runner
from app.services.indicators import calculate_indicator_points
from app.services.market_signal_pipeline import market_signal_pipeline
from app.services.market_ws_hub import market_ws_hub

router = APIRouter(tags=["candles"])
VALID_INTERVALS = set(get_args(Interval))
CHAINLINK_CANDLE_SYMBOL = "BTCUSD"

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


@router.get("/candles", response_model=list[MarketCandle])
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
            # 浏览器不能发 WebSocket 协议层 ping；应用层 ping 用于前端诊断客户到后端的 WS RTT。
            if await send_market_pong(websocket, raw_message):
                continue
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
    def __init__(self, mode: Literal["latest", "range"], candles: list[MarketCandle]) -> None:
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


async def send_market_pong(websocket: WebSocket, raw_message: str) -> bool:
    try:
        payload = json.loads(raw_message)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict) or payload.get("type") != "market.ping":
        return False
    await websocket.send_json(
        {
            "type": "market.pong",
            "request_id": payload.get("request_id"),
        }
    )
    return True


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
        warmup_start_ms = max(
            0,
            start_ms - ((settings.candle_history_limit - 1) * CANDLE_INTERVAL_MS[interval]),
        )
        sync_symbol = settings.binance_symbol if symbol == CHAINLINK_CANDLE_SYMBOL else symbol
        await candle_sync_service.ensure_range(
            session,
            symbol=sync_symbol,
            interval=interval,
            start_ms=warmup_start_ms,
            end_ms=end_ms,
        )
        warmup_start = datetime.fromtimestamp(warmup_start_ms / 1000, tz=timezone.utc)
        target_start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        end = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
        source_candles = await list_candles_between(
            session,
            symbol=symbol,
            interval=interval,
            start=warmup_start,
            end=end,
        )
        if symbol == CHAINLINK_CANDLE_SYMBOL:
            chainlink_started_at = await get_earliest_candle_time(
                session, CHAINLINK_CANDLE_SYMBOL, "1m"
            )
            binance_candles = await list_candles_between(
                session,
                symbol=settings.binance_symbol,
                interval=interval,
                start=warmup_start,
                end=end,
            )
            source_candles = merge_chainlink_history(
                binance_candles,
                source_candles,
                chainlink_started_at=chainlink_started_at,
            )
        candles = [candle for candle in source_candles if candle.open_time >= target_start]
        # warmup 只负责还原目标区间起点的指标状态，不能作为用户历史数据返回并画到图上。
        return CandleSnapshot(
            "range",
            attach_indicators(candles, interval, source_candles=source_candles),
        )

    # 指标递归依赖历史状态，快照内部使用后端实时窗口计算，再只返回请求的最后 limit 根。
    indicator_limit = max(limit, settings.candle_history_limit)
    sync_symbol = settings.binance_symbol if symbol == CHAINLINK_CANDLE_SYMBOL else symbol
    await candle_sync_service.ensure_latest_window(
        session, symbol=sync_symbol, interval=interval, limit=indicator_limit
    )
    cached = await list_candles(session, symbol=symbol, interval=interval, limit=indicator_limit)
    if symbol == CHAINLINK_CANDLE_SYMBOL:
        chainlink_started_at = await get_earliest_candle_time(
            session, CHAINLINK_CANDLE_SYMBOL, "1m"
        )
        binance_candles = await list_candles(
            session,
            symbol=settings.binance_symbol,
            interval=interval,
            limit=indicator_limit,
        )
        cached = merge_chainlink_history(
            binance_candles,
            cached,
            chainlink_started_at=chainlink_started_at,
        )
    live_candles = market_signal_pipeline.get_live_candles(symbol, interval, limit=indicator_limit)
    candles = merge_live_candles(cached, live_candles, indicator_limit)
    return CandleSnapshot("latest", attach_indicators(candles[-limit:], interval, source_candles=candles))


def merge_chainlink_history(
    binance_candles: list[Candle],
    chainlink_candles: list[Candle],
    *,
    chainlink_started_at: datetime | None = None,
) -> list[Candle]:
    # Chainlink 部署前没有历史：同一 open_time 优先 Chainlink，其余用 Binance 补齐并显式标源。
    by_open_time = {
        candle.open_time: candle.model_copy(
            update={
                "symbol": CHAINLINK_CANDLE_SYMBOL,
                "source": "binance_fallback",
            }
        )
        for candle in binance_candles
        if chainlink_started_at is None or candle.open_time < chainlink_started_at
    }
    for candle in chainlink_candles:
        by_open_time[candle.open_time] = candle
    return sorted(by_open_time.values(), key=lambda candle: candle.open_time)


def merge_live_candles(cached: list[Candle], live_candles: list[Candle], limit: int) -> list[Candle]:
    # DB 只保存已闭合 K 线；latest 接口在出口合并内存态，首屏可直接带出当前未收盘 K 线。
    by_open_time = {candle.open_time: candle for candle in cached}
    for candle in live_candles:
        by_open_time[candle.open_time] = candle
    return sorted(by_open_time.values(), key=lambda candle: candle.open_time)[-limit:]


def attach_indicators(
    candles: list[Candle],
    interval: Interval,
    *,
    source_candles: list[Candle] | None = None,
) -> list[MarketCandle]:
    calculation_candles = source_candles or candles
    source_index_by_time = {
        candle.open_time: index for index, candle in enumerate(calculation_candles)
    }
    targets_by_window_start: dict[int, list[tuple[Candle, int]]] = {}
    for candle in candles:
        source_index = source_index_by_time.get(candle.open_time)
        if source_index is None:
            continue
        # TG 在收盘时使用“截至该根的最后 500 根”；历史快照也必须按目标 K 线截断窗口，避免页面窗口变长后指标漂移。
        window_start = max(0, source_index + 1 - settings.candle_history_limit)
        targets_by_window_start.setdefault(window_start, []).append((candle, source_index))

    indicators_by_time = {}
    for window_start, targets in targets_by_window_start.items():
        # 同一历史窗口内的 RSI/EMA/BOLL 是递推序列；一次计算到该组最大目标，避免 300 根快照重复跑 300 次。
        max_source_index = max(source_index for _, source_index in targets)
        points = calculate_indicator_points(calculation_candles[window_start : max_source_index + 1], interval)
        for candle, source_index in targets:
            indicators_by_time[candle.open_time] = points[source_index - window_start]
    return [
        MarketCandle.model_validate(
            {
                **candle.model_dump(),
                "indicator": indicators_by_time.get(candle.open_time),
            }
        )
        for candle in candles
    ]
