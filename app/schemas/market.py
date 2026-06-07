from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Interval = Literal["1m", "5m", "15m", "30m", "1h", "4h"]
ServiceState = Literal["idle", "backfilling", "connected", "reconnecting", "running", "error", "stopped"]
TrendState = Literal["up", "down", "flat", "insufficient_data"]
SignalSide = Literal["BUY_YES", "BUY_NO", "HOLD"]


class Candle(BaseModel):
    symbol: str
    interval: Interval
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True

    model_config = ConfigDict(from_attributes=True)


class BollingerBands(BaseModel):
    upper: float | None = None
    middle: float | None = None
    lower: float | None = None


class IndicatorInterval(BaseModel):
    interval: Interval
    rsi: float | None = None
    bollinger: BollingerBands
    trend: TrendState
    updated_at: datetime | None = None


class IndicatorSnapshot(BaseModel):
    symbol: str
    updated_at: datetime
    intervals: dict[Interval, IndicatorInterval]


class PolyMarket(BaseModel):
    id: str
    title: str
    interval: Literal["5m", "15m"] | str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    end_time: datetime | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    spread: float | None = None
    liquidity: float | None = None
    status: str
    event_id: str | None = None
    event_slug: str | None = None
    event_title: str | None = None
    outcomes: list[str] = Field(default_factory=list)
    outcome_prices: list[float | None] = Field(default_factory=list)
    winning_outcome: str | None = None
    result_status: Literal["open", "pending", "resolved"] = "open"


class MarketResult(BaseModel):
    event_slug: str
    market_id: str | None = None
    title: str
    end_time: datetime | None = None
    outcomes: list[str] = Field(default_factory=list)
    outcome_prices: list[float | None] = Field(default_factory=list)
    winning_outcome: str | None = None
    result_status: Literal["open", "pending", "resolved"]


class OrderbookLevel(BaseModel):
    price: float
    size: float


class OrderbookSnapshot(BaseModel):
    token_id: str
    best_bid: float | None = None
    best_ask: float | None = None
    spread: float | None = None
    liquidity: float | None = None
    updated_at: datetime | None = None
    bids: list[OrderbookLevel] = Field(default_factory=list)
    asks: list[OrderbookLevel] = Field(default_factory=list)


class ServiceHealth(BaseModel):
    name: str
    state: ServiceState
    last_update: datetime | None = None
    last_error: str | None = None


class RuntimeStatus(BaseModel):
    services: dict[str, ServiceHealth]
    scheduler: str
    tracked_markets: int
    last_error: str | None = None
    updated_at: datetime


class PreviewSignal(BaseModel):
    id: str
    symbol: str
    side: SignalSide
    confidence: float
    reason: str
    actionable: bool = False
    uses_closed_candle: bool = False
    created_at: datetime
    source: str = "preview"
    indicator_snapshot: dict[str, str | float | int | bool | None] = Field(default_factory=dict)
