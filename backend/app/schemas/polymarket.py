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


class PolymarketAccountPosition(BaseModel):
    condition_id: str | None
    asset: str | None
    title: str | None
    slug: str | None
    event_slug: str | None
    outcome: str | None
    size: float | None
    avg_price: float | None
    cur_price: float | None
    current_value: float | None
    cash_pnl: float | None
    percent_pnl: float | None
    redeemable: bool
    mergeable: bool
    end_date: datetime | None
    raw: dict


class PolymarketAccountOrder(BaseModel):
    id: str
    market: str | None
    asset_id: str | None
    side: str | None
    price: float | None
    original_size: float | None
    size_matched: float | None
    remaining_size: float | None
    order_type: str | None
    status: str | None
    outcome: str | None
    created_at: datetime | None
    updated_at: datetime | None
    raw: dict


class PolymarketAccountTrade(BaseModel):
    id: str
    market: str | None
    asset_id: str | None
    side: str | None
    price: float | None
    size: float | None
    outcome: str | None
    timestamp: datetime | None
    order_id: str | None
    raw: dict


class PolymarketAccountState(BaseModel):
    wallet: str | None
    condition_id: str | None = None
    positions: list[PolymarketAccountPosition]
    orders: list[PolymarketAccountOrder]
    recent_trades: list[PolymarketAccountTrade]
    ws_state: str
    last_positions_refresh_at: datetime | None
    last_orders_refresh_at: datetime | None
    last_trade_at: datetime | None
    error: str | None = None


class PolymarketAccountStateWsMessage(BaseModel):
    type: str = "polymarket.account_state.snapshot"
    condition_id: str | None
    state: PolymarketAccountState
