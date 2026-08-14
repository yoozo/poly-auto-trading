from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
import websockets

from app.core.config import settings
from app.db.models import Candle as CandleModel
from app.db.models import ChainlinkTwapObservation as ChainlinkTwapObservationModel
from app.db.session import AsyncSessionLocal
from app.schemas.candle import Candle, Interval
from app.schemas.chainlink_twap import (
    ChainlinkTwapObservation,
    MarketPriceContext,
    MarketPricePoint,
)
from app.schemas.market_signal import MarketDataEvent
from app.services.candle_intervals import (
    align_interval_open_ms,
    standard_close_time,
)
from app.services.candle_store import (
    insert_candles_if_missing,
    list_candles,
    list_candles_between,
    upsert_candles,
)
from app.services.market_signal_pipeline import market_signal_pipeline
from app.services.service_health import service_health_store

logger = logging.getLogger(__name__)

TOPIC_WINDOWS = {
    "crypto_prices_twap_thirty": 30,
    "crypto_prices_twap_sixty": 60,
}
SYMBOL = "btc/usd"
E18 = Decimal(10) ** 18
BASELINE_TOLERANCE = timedelta(seconds=5)
SPOT_WINDOW = 0
POLYMARKET_CRYPTO_PRICE_URL = "https://polymarket.com/api/crypto/crypto-price"
POLYMARKET_PAST_RESULTS_URL = "https://polymarket.com/api/past-results"
POLYMARKET_EVENT_URL = "https://polymarket.com/event/{slug}"
_price_to_beat_cache: dict[str, tuple[Decimal | None, datetime]] = {}
_price_to_beat_lock = asyncio.Lock()
_past_result_cache: dict[str, "PolymarketPastResult"] = {}
_past_result_retry_after: dict[str, datetime] = {}
_past_result_lock = asyncio.Lock()
_persisted_past_result_market_ids: set[str] = set()
_past_result_persist_lock = asyncio.Lock()
CHAINLINK_CANDLE_SYMBOL = "BTCUSD"
CHAINLINK_CANDLE_INTERVALS: tuple[Interval, ...] = (
    "1m",
    "5m",
    "15m",
    "30m",
    "1h",
    "4h",
    "12h",
    "1d",
    "1w",
)


@dataclass(frozen=True)
class PolymarketPastResult:
    start_time: datetime
    end_time: datetime
    open_price: Decimal
    close_price: Decimal


def subscription_payload() -> dict[str, Any]:
    return {
        "action": "subscribe",
        "subscriptions": [
            {
                "topic": "crypto_prices_chainlink",
                "type": "*",
                "filters": json.dumps({"symbol": SYMBOL}, separators=(",", ":")),
            },
            *[
            {
                "topic": topic,
                "type": "update",
                "filters": json.dumps({"symbol": SYMBOL}, separators=(",", ":")),
            }
            for topic in TOPIC_WINDOWS
            ],
        ],
    }


def parse_twap_message(raw: str | bytes) -> ChainlinkTwapObservation | None:
    if raw in {"PING", "PONG", b"PING", b"PONG"}:
        return None
    topic = ""
    try:
        message = json.loads(raw)
        topic = str(message.get("topic") or "")
        payload = message.get("payload")
        if message.get("type") != "update" or not isinstance(payload, dict):
            return None
        if str(payload.get("symbol") or "").lower() != SYMBOL:
            return None
        if topic == "crypto_prices_chainlink":
            window_seconds = SPOT_WINDOW
            full_accuracy_value = None
            value = Decimal(str(payload["value"]))
        else:
            if topic not in TOPIC_WINDOWS:
                return None
            window_seconds = int(payload.get("window_s"))
            if window_seconds != TOPIC_WINDOWS[topic]:
                raise ValueError(
                    f"topic window mismatch: expected {TOPIC_WINDOWS[topic]}, got {window_seconds}"
                )
            full_accuracy_value = str(payload["full_accuracy_value"])
            value = Decimal(full_accuracy_value) / E18
        return ChainlinkTwapObservation(
            symbol=SYMBOL,
            window_seconds=window_seconds,
            value=value,
            full_accuracy_value=full_accuracy_value,
            observed_at=datetime.fromtimestamp(int(payload["timestamp"]) / 1000, timezone.utc),
            published_at=datetime.fromtimestamp(int(message["timestamp"]) / 1000, timezone.utc),
            topic=topic,
        )
    except (KeyError, TypeError, ValueError, InvalidOperation, json.JSONDecodeError) as exc:
        # RTDS 字段变化不能再静默表现为“等待数据”，日志只记录结构错误，不输出完整行情消息。
        logger.warning(
            "Invalid Chainlink RTDS message",
            extra={"topic": topic or "unknown", "error": str(exc)},
        )
        return None


async def upsert_observation(
    session: AsyncSession, observation: ChainlinkTwapObservation
) -> None:
    statement = insert(ChainlinkTwapObservationModel).values(
        symbol=observation.symbol,
        window_seconds=observation.window_seconds,
        value=observation.value,
        full_accuracy_value=observation.full_accuracy_value,
        observed_at=observation.observed_at,
        published_at=observation.published_at,
        topic=observation.topic,
    )
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_chainlink_twap_observation",
            set_={
                "value": statement.excluded.value,
                "full_accuracy_value": statement.excluded.full_accuracy_value,
                "published_at": statement.excluded.published_at,
                "topic": statement.excluded.topic,
            },
        )
    )
    await session.commit()


async def market_price_context(
    session: AsyncSession,
    market: Any,
    *,
    now: datetime | None = None,
) -> MarketPriceContext | None:
    window_seconds = getattr(market, "twap_window_seconds", None)
    if window_seconds is None or market.start_time is None or market.end_time is None:
        return None

    now = ensure_utc(now or datetime.now(timezone.utc))
    start = ensure_utc(market.start_time)
    end = ensure_utc(market.end_time)
    if now >= end:
        # 收盘必须回读 Polymarket 官方历史结果；最后一条实时 TWAP 不能代替 Final price。
        final_result = await finalize_closed_market(session, market, now=now)
        if final_result is None:
            return MarketPriceContext(
                market_id=market.id,
                interval=market.interval,
                twap_window_seconds=window_seconds,
                quality="waiting_polymarket_final",
                warning="正在等待 Polymarket 官方收盘价，暂不生成最终方向",
            )
        baseline_point = MarketPricePoint(
            source="polymarket",
            value=final_result.open_price,
            observed_at=final_result.start_time,
        )
        final_point = MarketPricePoint(
            source="polymarket",
            value=final_result.close_price,
            observed_at=final_result.end_time,
        )
        difference = final_result.close_price - final_result.open_price
        direction = "up" if difference >= 0 else "down"
        return MarketPriceContext(
            market_id=market.id,
            interval=market.interval,
            twap_window_seconds=window_seconds,
            quality="exact",
            baseline=baseline_point,
            current=final_point,
            difference=difference,
            direction=direction,
            settlement_twap=final_point,
            settlement_difference=difference,
            settlement_direction=direction,
        )

    # market 展示价、方向和结算都必须使用规则指定的同一 TWAP window；spot 只负责聚合 K 线。
    current = await latest_observation(
        session, window_seconds, start, min(now, end)
    )
    # Price To Beat 以 Polymarket 页面使用的 openPrice 为准；本地起始观测仅在接口不可用时兜底。
    price_to_beat = await polymarket_price_to_beat(market)
    baseline = None if price_to_beat is not None else await baseline_observation(session, window_seconds, start)
    baseline_point = (
        MarketPricePoint(source="polymarket", value=price_to_beat, observed_at=start)
        if price_to_beat is not None
        else observation_point(baseline)
        if baseline
        else await binance_baseline(session, start)
    )
    current_point = observation_point(current) if current else None

    if current is None:
        return MarketPriceContext(
            market_id=market.id,
            interval=market.interval,
            twap_window_seconds=window_seconds,
            quality="waiting_chainlink",
            warning=f"正在等待 Chainlink {window_seconds}s TWAP 实时数据，暂不生成方向信号",
            baseline=baseline_point,
            settlement_twap=current_point,
        )

    if now <= end and now - current.observed_at > timedelta(seconds=settings.chainlink_twap_stale_seconds):
        return MarketPriceContext(
            market_id=market.id,
            interval=market.interval,
            twap_window_seconds=window_seconds,
            quality="stale",
            warning="Chainlink TWAP 已过期，暂不生成方向信号",
            baseline=baseline_point,
            current=current_point,
            settlement_twap=current_point,
        )
    if baseline_point is None:
        return MarketPriceContext(
            market_id=market.id,
            interval=market.interval,
            twap_window_seconds=window_seconds,
            quality="unavailable",
            warning="缺少市场起始参考价，暂不生成方向信号",
            current=current_point,
            settlement_twap=current_point,
        )

    difference = current.value - baseline_point.value
    estimated = baseline_point.source == "binance"
    warning = None
    if baseline_point.source == "binance":
        warning = "启动时缺少 Chainlink 起始 TWAP，已用 Binance 1m 收盘价补齐，可能存在误差"
    return MarketPriceContext(
        market_id=market.id,
        interval=market.interval,
        twap_window_seconds=window_seconds,
        quality="estimated_baseline" if estimated else "exact",
        warning=warning,
        baseline=baseline_point,
        current=current_point,
        difference=difference,
        direction="up" if difference >= 0 else "down",
        settlement_twap=current_point,
        settlement_difference=difference,
        settlement_direction="up" if difference >= 0 else "down",
    )


async def polymarket_price_to_beat(market: Any) -> Decimal | None:
    now = datetime.now(timezone.utc)
    cached = _price_to_beat_cache.get(market.id)
    if cached and cached[1] > now:
        return cached[0]
    async with _price_to_beat_lock:
        cached = _price_to_beat_cache.get(market.id)
        if cached and cached[1] > now:
            return cached[0]
        variant = "fiveminute" if market.interval == "5m" else "fifteen"
        params = {
            "symbol": "BTC",
            "eventStartTime": iso_z(ensure_utc(market.start_time)),
            "variant": variant,
            "endDate": iso_z(ensure_utc(market.end_time)),
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                value = await polymarket_page_price_to_beat(client, market)
                payload: dict[str, Any] = {"completed": True}
                if value is None:
                    response = await client.get(
                        POLYMARKET_CRYPTO_PRICE_URL,
                        params=params,
                        headers={
                            "Accept": "application/json",
                            "Referer": "https://polymarket.com/",
                            "User-Agent": "poly-auto-trading/1.0",
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                    value = Decimal(str(payload["openPrice"]))
            # 未完成响应会继续变化，短缓存重试；完成后 Price To Beat 在 market 生命周期内不变。
            completed = payload.get("completed") is True and payload.get("incomplete") is not True
            expires_at = now + (timedelta(days=3650) if completed else timedelta(seconds=5))
        except (httpx.HTTPError, KeyError, TypeError, ValueError, InvalidOperation) as exc:
            logger.warning("Failed to fetch Polymarket Price to Beat", exc_info=exc)
            value = None
            expires_at = now + timedelta(seconds=10)
        _price_to_beat_cache[market.id] = (value, expires_at)
        return value


async def polymarket_past_result(market: Any) -> PolymarketPastResult | None:
    cached = _past_result_cache.get(market.id)
    if cached is not None:
        return cached
    now = datetime.now(timezone.utc)
    if _past_result_retry_after.get(market.id, datetime.min.replace(tzinfo=timezone.utc)) > now:
        return None
    if market.interval not in {"5m", "15m"} or market.start_time is None or market.end_time is None:
        return None

    async with _past_result_lock:
        cached = _past_result_cache.get(market.id)
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    POLYMARKET_PAST_RESULTS_URL,
                    params={
                        "symbol": "BTC",
                        "variant": "fiveminute" if market.interval == "5m" else "fifteen",
                        "assetType": "crypto",
                        # past-results 返回 reference time 之前的周期；用 end_time 才会包含刚结束的 market。
                        "currentEventStartTime": iso_z(ensure_utc(market.end_time)),
                        "count": 1,
                        "twapEnabled": "true",
                        "twapLookbackSeconds": int(market.twap_window_seconds),
                    },
                    headers={
                        "Accept": "application/json",
                        "Referer": POLYMARKET_EVENT_URL.format(slug=getattr(market, "slug", "") or ""),
                        "User-Agent": "poly-auto-trading/1.0",
                    },
                )
                response.raise_for_status()
                result = parse_polymarket_past_result(response.json(), market)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, InvalidOperation) as exc:
            logger.warning(
                "Failed to fetch Polymarket final price",
                extra={"market_id": market.id, "interval": market.interval},
                exc_info=exc,
            )
            result = None
        if result is None:
            _past_result_retry_after[market.id] = now + timedelta(seconds=5)
            return None
        _past_result_cache[market.id] = result
        _past_result_retry_after.pop(market.id, None)
        return result


def parse_polymarket_past_result(payload: Any, market: Any) -> PolymarketPastResult | None:
    rows = payload.get("data", {}).get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    expected_start = ensure_utc(market.start_time)
    expected_end = ensure_utc(market.end_time)
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            start = ensure_utc(datetime.fromisoformat(str(row["startTime"]).replace("Z", "+00:00")))
            end = ensure_utc(datetime.fromisoformat(str(row["endTime"]).replace("Z", "+00:00")))
            open_price = Decimal(str(row["openPrice"]))
            close_price = Decimal(str(row["closePrice"]))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        if start == expected_start and end == expected_end and open_price.is_finite() and close_price.is_finite():
            return PolymarketPastResult(start, end, open_price, close_price)
    return None


async def finalize_closed_market(
    session: AsyncSession,
    market: Any,
    *,
    now: datetime | None = None,
) -> PolymarketPastResult | None:
    if market.end_time is None or ensure_utc(now or datetime.now(timezone.utc)) < ensure_utc(market.end_time):
        return None
    result = await polymarket_past_result(market)
    if result is None:
        return None
    # 同一 market 的价格上下文会随盘口广播高频重算；官方收盘 K 线只尝试落库一次。
    if market.id not in _persisted_past_result_market_ids:
        async with _past_result_persist_lock:
            if market.id not in _persisted_past_result_market_ids:
                await insert_candles_if_missing(session, [past_result_candle(market, result)])
                _persisted_past_result_market_ids.add(market.id)
    return result


def past_result_candle(market: Any, result: PolymarketPastResult) -> Candle:
    open_price = float(result.open_price)
    close_price = float(result.close_price)
    return Candle(
        symbol=CHAINLINK_CANDLE_SYMBOL,
        interval=market.interval,
        open_time=result.start_time,
        close_time=standard_close_time(result.start_time, market.interval),
        open=open_price,
        high=max(open_price, close_price),
        low=min(open_price, close_price),
        close=close_price,
        volume=0,
        is_closed=True,
        source="polymarket",
        # past-results 没有区间内 high/low，仅保证开收盘和收盘指标准确。
        is_complete=False,
    )


async def polymarket_page_price_to_beat(
    client: httpx.AsyncClient, market: Any
) -> Decimal | None:
    if not market.slug:
        return None
    try:
        response = await client.get(
            POLYMARKET_EVENT_URL.format(slug=market.slug),
            headers={
                "Accept": "text/html",
                "User-Agent": "Mozilla/5.0 (compatible; poly-auto-trading/1.0)",
            },
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return parse_polymarket_page_open_price(response.text, market)


def parse_polymarket_page_open_price(page: str, market: Any) -> Decimal | None:
    start = re.escape(iso_z(ensure_utc(market.start_time)))
    end = re.escape(iso_z(ensure_utc(market.end_time)))
    variant = "fiveminute" if market.interval == "5m" else "fifteen"
    window_seconds = int(market.twap_window_seconds)
    pattern = re.compile(
        rf'\\"openPrice\\":(-?\d+(?:\.\d+)?).*?'
        rf'\\"queryKey\\":\[\\"crypto-prices\\",\\"price\\",\\"BTC\\",'
        rf'\\"{start}\\",\\"{variant}\\",\\"{end}\\",true,{window_seconds}\]',
        re.DOTALL,
    )
    match = pattern.search(page)
    if match is None:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:
        return None


def iso_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


async def latest_observation(
    session: AsyncSession,
    window_seconds: int,
    start: datetime,
    end: datetime,
) -> ChainlinkTwapObservationModel | None:
    if end < start:
        return None
    return await session.scalar(
        select(ChainlinkTwapObservationModel)
        .where(
            ChainlinkTwapObservationModel.symbol == SYMBOL,
            ChainlinkTwapObservationModel.window_seconds == window_seconds,
            ChainlinkTwapObservationModel.observed_at >= start,
            ChainlinkTwapObservationModel.observed_at <= end,
        )
        .order_by(ChainlinkTwapObservationModel.observed_at.desc())
        .limit(1)
    )


async def baseline_observation(
    session: AsyncSession, window_seconds: int, start: datetime
) -> ChainlinkTwapObservationModel | None:
    rows = await session.scalars(
        select(ChainlinkTwapObservationModel)
        .where(
            ChainlinkTwapObservationModel.symbol == SYMBOL,
            ChainlinkTwapObservationModel.window_seconds == window_seconds,
            ChainlinkTwapObservationModel.observed_at >= start - BASELINE_TOLERANCE,
            ChainlinkTwapObservationModel.observed_at <= start + BASELINE_TOLERANCE,
        )
        .order_by(ChainlinkTwapObservationModel.observed_at.asc())
    )
    candidates = list(rows.all())
    return min(candidates, key=lambda row: abs(row.observed_at - start)) if candidates else None


async def binance_baseline(session: AsyncSession, start: datetime) -> MarketPricePoint | None:
    expected_open = start - timedelta(minutes=1)
    candle = await session.scalar(
        select(CandleModel)
        .where(
            CandleModel.symbol == settings.binance_symbol.upper(),
            CandleModel.interval == "1m",
            CandleModel.open_time == expected_open,
            CandleModel.is_closed.is_(True),
        )
        .limit(1)
    )
    if candle is None:
        return None
    return MarketPricePoint(source="binance", value=candle.close, observed_at=candle.close_time)


def observation_point(observation: ChainlinkTwapObservationModel) -> MarketPricePoint:
    return MarketPricePoint(
        source="chainlink",
        value=observation.value,
        observed_at=observation.observed_at,
    )


def ensure_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def twap_stream_is_stale(
    last_received_at: dict[str, datetime],
    *,
    connected_at: datetime,
    now: datetime,
    stall_seconds: int,
) -> bool:
    latest_twap = max(
        [
            connected_at,
            *(last_received_at[topic] for topic in TOPIC_WINDOWS if topic in last_received_at),
        ],
    )
    return now - latest_twap > timedelta(seconds=stall_seconds)


class ChainlinkTwapMonitor:
    """只消费免费 RTDS 实时流；本地持久化负责重启后的历史基准复用。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._candle_aggregator = ChainlinkCandleAggregator()
        self._connected_at: datetime | None = None
        self._last_received_at: dict[str, datetime] = {}

    async def start(self) -> None:
        if not settings.chainlink_twap_enabled or self._task is not None:
            service_health_store.set("chainlink_twap", "idle")
            return
        self._task = asyncio.create_task(self.run(), name="chainlink-twap-rtds")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        service_health_store.set("chainlink_twap", "stopped")

    async def run(self) -> None:
        while True:
            try:
                async with websockets.connect(
                    settings.chainlink_twap_ws_url,
                    ping_interval=None,
                    open_timeout=10,
                ) as socket:
                    await socket.send(json.dumps(subscription_payload(), separators=(",", ":")))
                    self._connected_at = datetime.now(timezone.utc)
                    self._last_received_at = {}
                    service_health_store.set(
                        "chainlink_twap", "running", metadata=self.health_metadata()
                    )
                    heartbeat = asyncio.create_task(self.heartbeat(socket))
                    watchdog = asyncio.create_task(self.stale_watchdog(socket))
                    try:
                        async for raw in socket:
                            observation = parse_twap_message(raw)
                            if observation is not None:
                                await self.handle_observation(observation)
                    finally:
                        heartbeat.cancel()
                        watchdog.cancel()
                        await asyncio.gather(heartbeat, watchdog, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Chainlink TWAP RTDS disconnected", exc_info=exc)
                service_health_store.set("chainlink_twap", "error", last_error=str(exc))
                await asyncio.sleep(3)

    async def heartbeat(self, socket: Any) -> None:
        while True:
            await asyncio.sleep(5)
            await socket.send("PING")

    async def stale_watchdog(self, socket: Any) -> None:
        stall_seconds = max(60, settings.chainlink_twap_stale_seconds * 3)
        while True:
            await asyncio.sleep(5)
            now = datetime.now(timezone.utc)
            if twap_stream_is_stale(
                self._last_received_at,
                connected_at=self._connected_at or now,
                now=now,
                stall_seconds=stall_seconds,
            ):
                # Spot 仍有数据也不能掩盖 TWAP 全部停流；关闭后由外层循环重新订阅。
                metadata = self.health_metadata(now)
                metadata["reason"] = "twap_stream_stale"
                service_health_store.set("chainlink_twap", "reconnecting", metadata=metadata)
                logger.warning("Chainlink TWAP topics are stale; reconnecting", extra=metadata)
                await socket.close(code=1012, reason="TWAP stream stale")
                return
            service_health_store.set(
                "chainlink_twap", "running", metadata=self.health_metadata(now)
            )

    def health_metadata(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        return {
            "endpoint": settings.chainlink_twap_ws_url,
            "last_received_at": {
                topic: received_at.isoformat()
                for topic, received_at in sorted(self._last_received_at.items())
            },
            "missing_topics": [
                topic for topic in TOPIC_WINDOWS if topic not in self._last_received_at
            ],
            "checked_at": now.isoformat(),
        }

    async def handle_observation(self, observation: ChainlinkTwapObservation) -> None:
        async with AsyncSessionLocal() as session:
            await upsert_observation(session, observation)
        self._last_received_at[observation.topic] = datetime.now(timezone.utc)
        if observation.window_seconds == SPOT_WINDOW:
            await self._candle_aggregator.handle(observation)
        if observation.window_seconds == SPOT_WINDOW:
            intervals = ("5m", "15m")
        else:
            intervals = ("5m",) if observation.window_seconds == 30 else ("15m",)
        # 价格变化复用现有 market 快照通道，前端无需再维护第三条 WS。
        from app.services.polymarket_monitor import polymarket_market_monitor

        for interval in intervals:
            await polymarket_market_monitor.broadcast_active_market_snapshots(interval)


class ChainlinkCandleAggregator:
    """把 Chainlink spot tick 聚合成 OHLC；TWAP 数据不参与普通 K 线。"""

    def __init__(self) -> None:
        self._initialized = False
        self._lock = asyncio.Lock()
        self._current: dict[Interval, Candle] = {}
        self._last_tick_at: dict[Interval, datetime] = {}

    async def handle(self, observation: ChainlinkTwapObservation) -> None:
        async with self._lock:
            await self._initialize()
            updated: list[Candle] = []
            closed: list[Candle] = []
            for interval in CHAINLINK_CANDLE_INTERVALS:
                previous = self._current.get(interval)
                open_time = interval_open_time(observation.observed_at, interval)
                if previous is None or previous.open_time != open_time:
                    if previous is not None:
                        last_tick = self._last_tick_at.get(interval)
                        complete = bool(
                            previous.is_complete
                            and last_tick
                            and previous.close_time - last_tick
                            <= timedelta(seconds=settings.chainlink_twap_stale_seconds)
                        )
                        closed.append(
                            previous.model_copy(
                                update={"is_closed": True, "is_complete": complete}
                            )
                        )
                    current = aggregate_spot_candle(None, observation, interval)
                else:
                    current = aggregate_spot_candle(previous, observation, interval)
                self._current[interval] = current
                self._last_tick_at[interval] = observation.observed_at
                updated.append(current)

            # 1m 是重放权威数据；高周期只在闭合时物化，实时态始终可由 1m 重建。
            persisted = candles_to_persist(closed, updated)
            async with AsyncSessionLocal() as session:
                await upsert_candles(session, persisted)
            for candle in closed:
                await market_signal_pipeline.handle_market_event(
                    MarketDataEvent(source="chainlink_spot", candle=candle)
                )
            for candle in updated:
                await market_signal_pipeline.handle_market_event(
                    MarketDataEvent(source="chainlink_spot", candle=candle),
                    notify=False,
                )

    async def _initialize(self) -> None:
        if self._initialized:
            return
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            oldest_current_open = min(
                interval_open_time(now, interval)
                for interval in CHAINLINK_CANDLE_INTERVALS
            )
            minute_window = await list_candles_between(
                session,
                symbol=CHAINLINK_CANDLE_SYMBOL,
                interval="1m",
                start=oldest_current_open,
                end=now,
            )
            for interval in CHAINLINK_CANDLE_INTERVALS:
                candles = await list_candles(
                    session,
                    symbol=CHAINLINK_CANDLE_SYMBOL,
                    interval=interval,
                    limit=settings.candle_history_limit,
                )
                if interval != "1m":
                    current = aggregate_one_minute_window(
                        minute_window, interval, now=now
                    )
                    if current is not None:
                        candles = [
                            candle
                            for candle in candles
                            if candle.open_time != current.open_time
                        ]
                        candles.append(current)
                market_signal_pipeline.replace_live_candles(
                    CHAINLINK_CANDLE_SYMBOL, interval, candles
                )
                if candles and not candles[-1].is_closed:
                    self._current[interval] = candles[-1]
        self._initialized = True


def interval_open_time(observed_at: datetime, interval: Interval) -> datetime:
    observed_ms = int(ensure_utc(observed_at).timestamp() * 1000)
    open_ms = align_interval_open_ms(observed_ms, interval)
    return datetime.fromtimestamp(open_ms / 1000, timezone.utc)


def aggregate_spot_candle(
    previous: Candle | None,
    observation: ChainlinkTwapObservation,
    interval: Interval,
) -> Candle:
    price = float(observation.value)
    open_time = interval_open_time(observation.observed_at, interval)
    if previous is not None and previous.open_time == open_time:
        return previous.model_copy(
            update={
                "high": max(previous.high, price),
                "low": min(previous.low, price),
                "close": price,
            }
        )
    return Candle(
        symbol=CHAINLINK_CANDLE_SYMBOL,
        interval=interval,
        open_time=open_time,
        close_time=standard_close_time(open_time, interval),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=None,
        is_closed=False,
        source="chainlink",
        is_complete=observation.observed_at - open_time
        <= timedelta(seconds=settings.chainlink_twap_stale_seconds),
    )


def candles_to_persist(closed: list[Candle], updated: list[Candle]) -> list[Candle]:
    """1m 每个 tick 更新以抗重启；高周期仅保存闭合物化结果。"""
    return [*closed, *(candle for candle in updated if candle.interval == "1m")]


def aggregate_one_minute_window(
    candles: list[Candle], interval: Interval, *, now: datetime
) -> Candle | None:
    if not candles:
        return None
    open_time = interval_open_time(now, interval)
    rows = sorted(
        (candle for candle in candles if candle.open_time >= open_time),
        key=lambda candle: candle.open_time,
    )
    if not rows:
        return None
    expected_time = open_time
    complete = True
    for candle in rows:
        if candle.open_time != expected_time or not candle.is_complete:
            complete = False
        expected_time = candle.open_time + timedelta(minutes=1)
    return Candle(
        symbol=CHAINLINK_CANDLE_SYMBOL,
        interval=interval,
        open_time=open_time,
        close_time=standard_close_time(open_time, interval),
        open=rows[0].open,
        high=max(candle.high for candle in rows),
        low=min(candle.low for candle in rows),
        close=rows[-1].close,
        volume=None,
        is_closed=False,
        source="chainlink",
        is_complete=complete and rows[0].open_time == open_time,
    )


chainlink_twap_monitor = ChainlinkTwapMonitor()
