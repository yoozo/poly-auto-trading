from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class ChainlinkTwapObservation(BaseModel):
    symbol: str
    window_seconds: Literal[30, 60]
    value: Decimal
    full_accuracy_value: str
    observed_at: datetime
    published_at: datetime
    topic: str


class MarketPricePoint(BaseModel):
    source: Literal["chainlink", "binance"]
    value: Decimal
    observed_at: datetime


class MarketPriceContext(BaseModel):
    market_id: str
    interval: Literal["5m", "15m"]
    twap_window_seconds: Literal[30, 60]
    quality: Literal[
        "exact", "estimated_baseline", "waiting_chainlink", "stale", "unavailable"
    ]
    warning: str | None = None
    baseline: MarketPricePoint | None = None
    current: MarketPricePoint | None = None
    difference: Decimal | None = None
    direction: Literal["up", "down"] | None = None
