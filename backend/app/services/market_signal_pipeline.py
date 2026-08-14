from __future__ import annotations

import logging
from datetime import datetime
from typing import cast

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.schemas.candle import Candle, IndicatorPoint, Interval
from app.schemas.market_signal import MarketDataEvent, SignalInput
from app.services.indicators import calculate_indicator_points
from app.services.market_ws_hub import market_ws_hub
from app.services.notifications import process_signal_notifications
from app.services.service_health import service_health_store
from app.services.signal_analysis import record_signal_input_analysis

logger = logging.getLogger(__name__)


class MarketSignalPipeline:
    """市场信号流水线：把各数据源事件转成信号上下文，再分发给下游。"""

    def __init__(self) -> None:
        # 实时窗口只保存内存态，用于给指标计算提供最近 N 根 K 线。
        self._live_candles: dict[tuple[str, str], list[Candle]] = {}
        self._pending_close_corrections: dict[tuple[str, str], datetime] = {}

    async def handle_market_event(
        self, event: MarketDataEvent, *, notify: bool = True
    ) -> SignalInput:
        # 入口保持数据源无关：调用方只需要提交 MarketDataEvent。
        candles = self._merge_live_candle(event.candle)
        self._resolve_close_correction(event)
        if self.close_correction_pending(event.candle.symbol, event.candle.interval):
            # Price To Beat 尚未校正时不计算指标，避免 TG 使用错误 close。
            return SignalInput(
                candle=event.candle,
                market_events=[event],
                factors={"technical_indicators": None, "sources": [event.source]},
            )
        signal_input = self.build_signal_input(event, candles)
        if notify:
            await self.dispatch(signal_input)
        else:
            await self.dispatch(signal_input, notify=False)
        return signal_input

    def build_signal_input(self, event: MarketDataEvent, candles: list[Candle]) -> SignalInput:
        # 当前版本先聚合技术指标；后续多源因子也应在这里汇总到 SignalInput。
        indicator_points = calculate_indicator_points(candles, event.candle.interval)
        indicator = next(
            (
                point
                for point in reversed(indicator_points)
                if point.candle_time == event.candle.open_time
            ),
            None,
        )
        return SignalInput(
            candle=event.candle,
            indicator=indicator,
            market_events=[event],
            factors={
                "technical_indicators": indicator.model_dump(mode="json") if indicator else None,
                "sources": [event.source],
            },
        )

    async def dispatch(self, signal_input: SignalInput, *, notify: bool = True) -> None:
        # 下游只消费 SignalInput，避免通知、WS 等模块回头依赖 Binance。
        if notify:
            await self._check_notifications(signal_input)
        await self._broadcast_market_update(signal_input)

    def replace_live_candles(self, symbol: str, interval: str, candles: list[Candle]) -> None:
        # REST backfill 后用数据库中的最近窗口重置内存态，保证 WS 增量计算有历史。
        key = (symbol.upper(), interval)
        self._live_candles[key] = candles[-settings.candle_history_limit :]

    def mark_close_correction_pending(self, candle: Candle) -> None:
        self._pending_close_corrections[(candle.symbol.upper(), candle.interval)] = candle.open_time

    def close_correction_pending(self, symbol: str, interval: str) -> bool:
        return (symbol.upper(), interval) in self._pending_close_corrections

    def indicators_ready_through(
        self,
        symbol: str,
        interval: str,
        candle_time: datetime,
    ) -> bool:
        pending_time = self._pending_close_corrections.get((symbol.upper(), interval))
        return pending_time is None or candle_time < pending_time

    def _resolve_close_correction(self, event: MarketDataEvent) -> None:
        if event.metadata.get("price_to_beat_corrected") is not True:
            return
        key = (event.candle.symbol.upper(), event.candle.interval)
        if self._pending_close_corrections.get(key) == event.candle.open_time:
            self._pending_close_corrections.pop(key, None)

    def get_live_candles(self, symbol: str, interval: str, limit: int | None = None) -> list[Candle]:
        key = (symbol.upper(), interval)
        candles = self._live_candles.get(key, [])
        return list(candles[-limit:] if limit is not None else candles)

    def latest_market_payload(self, symbol: str, interval: str) -> dict[str, object] | None:
        candles = self.get_live_candles(symbol, interval)
        return self.market_payload_from_candles(symbol, interval, candles)

    def market_payload_from_candles(
        self,
        symbol: str,
        interval: str,
        candles: list[Candle],
    ) -> dict[str, object] | None:
        if not candles:
            return None
        if not self.indicators_ready_through(symbol, interval, candles[-1].open_time):
            # 首屏也遵守校正屏障：K 线仍可显示，但不能携带由旧 close 算出的指标。
            return self._serialize_market_candle(symbol, interval, candles[-1])
        indicator_points = calculate_indicator_points(candles, cast(Interval, interval))
        return self._serialize_market_candle(
            symbol,
            interval,
            candles[-1],
            indicator_points[-1] if indicator_points else None,
        )

    def _merge_live_candle(self, candle: Candle) -> list[Candle]:
        # 同一根未收盘 K 线会被多次推送，用 open_time 去重并保留最新值。
        key = (candle.symbol.upper(), candle.interval)
        candles = self._live_candles.get(key, [])
        by_open_time = {item.open_time: item for item in candles}
        by_open_time[candle.open_time] = candle
        merged = sorted(by_open_time.values(), key=lambda item: item.open_time)
        merged = merged[-settings.candle_history_limit :]
        self._live_candles[key] = merged
        return merged

    async def _check_notifications(self, signal_input: SignalInput) -> None:
        candle = signal_input.candle
        try:
            # 分析信号先落库，再由通知层按一组信号决定是否聚合投递。
            async with AsyncSessionLocal() as session:
                signals = await record_signal_input_analysis(session, signal_input)
                await process_signal_notifications(session, signals)
        except Exception as exc:
            logger.exception(
                "Signal analysis or notification delivery failed",
                extra={"symbol": candle.symbol, "interval": candle.interval},
            )
            service_health_store.set("telegram", "error", last_error=str(exc))

    async def _broadcast_market_update(self, signal_input: SignalInput) -> None:
        # 前端优先消费这里的后端指标，保证图表和 Telegram 使用同一计算窗口与结果。
        candle = signal_input.candle
        await market_ws_hub.broadcast(
            candle.symbol,
            candle.interval,
            self._serialize_market_candle(
                candle.symbol,
                candle.interval,
                candle,
                signal_input.indicator,
            ),
        )

    def _serialize_market_candle(
        self,
        symbol: str,
        interval: str,
        candle: Candle,
        indicator: IndicatorPoint | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": "market.candle",
            "symbol": symbol.upper(),
            "interval": interval,
            "candle": candle.model_dump(mode="json"),
        }
        if indicator is not None:
            payload["indicator"] = indicator.model_dump(mode="json")
        return payload


market_signal_pipeline = MarketSignalPipeline()
