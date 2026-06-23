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
        return self._serialize_market_candle(symbol, interval, candles[-1])

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
        # 前端图表只消费 candle，指标由浏览器基于 candle 窗口计算；WS 不再推送冗余 SignalInput。
        candle = signal_input.candle
        await market_ws_hub.broadcast(
            candle.symbol,
            candle.interval,
            self._serialize_market_candle(candle.symbol, candle.interval, candle),
        )

    def _serialize_market_candle(self, symbol: str, interval: str, candle: Candle) -> dict[str, object]:
        return {
            "type": "market.candle",
            "symbol": symbol.upper(),
            "interval": interval,
            "candle": candle.model_dump(mode="json"),
        }


market_signal_pipeline = MarketSignalPipeline()
