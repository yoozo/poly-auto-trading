from __future__ import annotations

import logging

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.schemas.candle import Candle
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

    async def handle_market_event(self, event: MarketDataEvent) -> SignalInput:
        # 入口保持数据源无关：调用方只需要提交 MarketDataEvent。
        candles = self._merge_live_candle(event.candle)
        signal_input = self.build_signal_input(event, candles)
        await self.dispatch(signal_input)
        return signal_input

    def build_signal_input(self, event: MarketDataEvent, candles: list[Candle]) -> SignalInput:
        # 当前版本先聚合技术指标；后续多源因子也应在这里汇总到 SignalInput。
        indicator_points = calculate_indicator_points(candles, event.candle.interval)
        indicator = indicator_points[-1] if indicator_points else None
        return SignalInput(
            candle=event.candle,
            indicator=indicator,
            market_events=[event],
            factors={
                "technical_indicators": indicator.model_dump(mode="json") if indicator else None,
                "sources": [event.source],
            },
        )

    async def dispatch(self, signal_input: SignalInput) -> None:
        # 下游只消费 SignalInput，避免通知、WS 等模块回头依赖 Binance。
        await self._check_notifications(signal_input)
        await self._broadcast_market_update(signal_input)

    def replace_live_candles(self, symbol: str, interval: str, candles: list[Candle]) -> None:
        # REST backfill 后用数据库中的最近窗口重置内存态，保证 WS 增量计算有历史。
        key = (symbol.upper(), interval)
        self._live_candles[key] = candles[-settings.candle_history_limit :]

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
        # 兼容旧前端字段 candle/indicator，同时额外输出完整 signal_input。
        candle = signal_input.candle
        indicator = signal_input.indicator
        await market_ws_hub.broadcast(
            candle.symbol,
            candle.interval,
            {
                "type": "market.candle",
                "symbol": candle.symbol,
                "interval": candle.interval,
                "candle": candle.model_dump(mode="json"),
                "indicator": indicator.model_dump(mode="json") if indicator else None,
                "signal_input": signal_input.model_dump(mode="json"),
            },
        )


market_signal_pipeline = MarketSignalPipeline()
