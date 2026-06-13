from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.candle import Candle, IndicatorPoint


class MarketDataEvent(BaseModel):
    # 数据源统一入口：Binance、Polymarket、链上数据等都先包装成事件。
    source: str
    candle: Candle
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class SignalInput(BaseModel):
    # 信号计算的完整上下文，避免规则层直接依赖某一个具体数据源。
    candle: Candle
    indicator: IndicatorPoint | None = None
    market_events: list[MarketDataEvent] = Field(default_factory=list)
    # 扩展因子入口：资金费率、盘口、链上指标、Polymarket 数据等放这里。
    factors: dict[str, Any] = Field(default_factory=dict)
