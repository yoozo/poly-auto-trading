from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PolymarketOrderLevel(BaseModel):
    price: float | None
    size: float | None

    model_config = ConfigDict(from_attributes=True)


class PolymarketOutcomeQuote(BaseModel):
    name: str
    token_id: str | None
    price: float | None
    buy_price: float | None
    sell_price: float | None
    best_bid: float | None
    best_ask: float | None
    last_trade_price: float | None
    updated_at: datetime | None
    bids: list[PolymarketOrderLevel]
    asks: list[PolymarketOrderLevel]

    model_config = ConfigDict(from_attributes=True)


class PolymarketUpDownMarket(BaseModel):
    id: str
    condition_id: str | None
    slug: str | None
    title: str
    series_slug: str | None
    interval: str
    start_time: datetime | None
    end_time: datetime | None
    window: str
    seconds_to_start: int | None
    seconds_to_end: int | None
    accepting_orders: bool
    volume: float | None
    liquidity: float | None
    updated_at: datetime | None
    outcome_quotes: list[PolymarketOutcomeQuote]

    model_config = ConfigDict(from_attributes=True)
