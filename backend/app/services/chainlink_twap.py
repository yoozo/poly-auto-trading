from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
import websockets

from app.core.config import settings
from app.db.models import Candle as CandleModel
from app.db.models import ChainlinkTwapObservation as ChainlinkTwapObservationModel
from app.db.session import AsyncSessionLocal
from app.schemas.chainlink_twap import (
    ChainlinkTwapObservation,
    MarketPriceContext,
    MarketPricePoint,
)
from app.services.polymarket_market_store import polymarket_up_down_store
from app.services.service_health import service_health_store

logger = logging.getLogger(__name__)

TOPIC_WINDOWS = {
    "crypto_prices_twap_thirty": 30,
    "crypto_prices_twap_sixty": 60,
}
INTERVAL_WINDOWS = {"5m": 30, "15m": 60}
SYMBOL = "btc/usd"
E18 = Decimal(10) ** 18
BASELINE_TOLERANCE = timedelta(seconds=5)


def subscription_payload() -> dict[str, Any]:
    return {
        "action": "subscribe",
        "subscriptions": [
            {
                "topic": topic,
                "type": "update",
                "filters": json.dumps({"symbol": SYMBOL}, separators=(",", ":")),
            }
            for topic in TOPIC_WINDOWS
        ],
    }


def parse_twap_message(raw: str | bytes) -> ChainlinkTwapObservation | None:
    try:
        message = json.loads(raw)
        topic = str(message.get("topic") or "")
        payload = message.get("payload")
        if topic not in TOPIC_WINDOWS or message.get("type") != "update" or not isinstance(payload, dict):
            return None
        if str(payload.get("symbol") or "").lower() != SYMBOL:
            return None
        window_seconds = int(payload.get("window_s"))
        if window_seconds != TOPIC_WINDOWS[topic]:
            return None
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
    except (KeyError, TypeError, ValueError, InvalidOperation, json.JSONDecodeError):
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
    window_seconds = INTERVAL_WINDOWS.get(market.interval)
    if window_seconds is None or market.start_time is None or market.end_time is None:
        return None

    now = ensure_utc(now or datetime.now(timezone.utc))
    start = ensure_utc(market.start_time)
    end = ensure_utc(market.end_time)
    current = await latest_observation(session, window_seconds, start, min(now, end))
    baseline = await baseline_observation(session, window_seconds, start)
    baseline_point = observation_point(baseline) if baseline else await binance_baseline(session, start)

    if current is None:
        return MarketPriceContext(
            market_id=market.id,
            interval=market.interval,
            twap_window_seconds=window_seconds,
            quality="waiting_chainlink",
            warning="正在等待 Chainlink TWAP 实时数据，暂不生成方向信号",
            baseline=baseline_point,
        )

    current_point = observation_point(current)
    if now <= end and now - current.observed_at > timedelta(seconds=settings.chainlink_twap_stale_seconds):
        return MarketPriceContext(
            market_id=market.id,
            interval=market.interval,
            twap_window_seconds=window_seconds,
            quality="stale",
            warning="Chainlink TWAP 已过期，暂不生成方向信号",
            baseline=baseline_point,
            current=current_point,
        )
    if baseline_point is None:
        return MarketPriceContext(
            market_id=market.id,
            interval=market.interval,
            twap_window_seconds=window_seconds,
            quality="unavailable",
            warning="缺少市场起始参考价，暂不生成方向信号",
            current=current_point,
        )

    difference = current.value - baseline_point.value
    estimated = baseline_point.source == "binance"
    return MarketPriceContext(
        market_id=market.id,
        interval=market.interval,
        twap_window_seconds=window_seconds,
        quality="estimated_baseline" if estimated else "exact",
        warning="启动时缺少 Chainlink 起始价，已用 Binance 1m 收盘价补齐，可能存在误差" if estimated else None,
        baseline=baseline_point,
        current=current_point,
        difference=difference,
        direction="up" if difference >= 0 else "down",
    )


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


class ChainlinkTwapMonitor:
    """只消费免费 RTDS 实时流；本地持久化负责重启后的历史基准复用。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

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
                    service_health_store.set("chainlink_twap", "running")
                    heartbeat = asyncio.create_task(self.heartbeat(socket))
                    try:
                        async for raw in socket:
                            observation = parse_twap_message(raw)
                            if observation is not None:
                                await self.handle_observation(observation)
                    finally:
                        heartbeat.cancel()
                        await asyncio.gather(heartbeat, return_exceptions=True)
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

    async def handle_observation(self, observation: ChainlinkTwapObservation) -> None:
        async with AsyncSessionLocal() as session:
            await upsert_observation(session, observation)
        interval = "5m" if observation.window_seconds == 30 else "15m"
        # 价格变化复用现有 market 快照通道，前端无需再维护第三条 WS。
        from app.services.polymarket_monitor import polymarket_market_monitor

        await polymarket_market_monitor.broadcast_active_market_snapshots(interval)
        await self.notify_current_market(interval)

    async def notify_current_market(self, interval: str) -> None:
        market = await polymarket_up_down_store.current_market(interval)
        if market is None:
            return
        async with AsyncSessionLocal() as session:
            context = await market_price_context(session, market)
            if context and context.direction and context.quality in {"exact", "estimated_baseline"}:
                from app.services.chainlink_twap_notifications import notify_twap_direction

                await notify_twap_direction(session, context)


chainlink_twap_monitor = ChainlinkTwapMonitor()
