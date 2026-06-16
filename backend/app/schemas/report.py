from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


TaskStatus = Literal["running", "done", "error"]


class AnalyzeAccountRequest(BaseModel):
    input: str = Field(min_length=1, max_length=255)
    activity_limit: int = 5000


class AnalyzeAccountResponse(BaseModel):
    task_id: str
    status: TaskStatus


class ReportTask(BaseModel):
    id: str
    account_id: str | None = None
    status: TaskStatus
    message: str
    percent: int
    result: dict[str, Any]
    error: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReportAccount(BaseModel):
    id: str
    input: str
    normalized_user: str
    proxy_wallet: str
    profile: dict[str, Any]
    favorite: bool
    note: str
    last_downloaded_at: datetime | None = None
    activity_count: int = 0
    latest_activity_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UpdateReportAccountRequest(BaseModel):
    note: str | None = Field(default=None, max_length=255)
    favorite: bool | None = None


class RecentPerformance(BaseModel):
    days: int
    market_count: int
    settled_market_count: int
    unsettled_market_count: int
    cost: float
    recovery: float
    pnl: float
    roi: float | None
    win_rate: float | None
    unsettled_exposure: float


class DailyPerformance(BaseModel):
    date: str
    cost: float
    recovery: float
    pnl: float
    roi: float | None


class AccountSummary(BaseModel):
    account_id: str
    activity_count: int
    market_count: int
    data_start: datetime | None = None
    data_end: datetime | None = None
    generated_at: datetime
    total_cost: float
    total_recovery: float
    total_pnl: float
    total_pnl_with_rebate: float
    total_roi: float | None
    maker_rebate_count: int
    maker_rebate_amount: float
    settled_market_count: int
    unsettled_market_count: int
    unsettled_exposure: float
    win_market_count: int
    loss_market_count: int
    breakeven_market_count: int
    win_rate: float | None
    average_cost: float | None
    median_cost: float | None
    max_cost: float | None
    average_profit: float | None
    average_loss: float | None
    incomplete_market_count: int
    recent: list[RecentPerformance]
    daily_last_7d: list[DailyPerformance]


class MarketPerformance(BaseModel):
    market_id: str
    title: str
    slug: str | None = None
    condition_id: str | None = None
    event_slug: str | None = None
    result: str
    position_status: str
    activity_count: int
    redeem_count: int
    merge_count: int
    market_date: datetime | None = None
    redeem_time: datetime | None = None
    up_cost: float
    up_shares: float
    up_average_cost: float | None
    down_cost: float
    down_shares: float
    down_average_cost: float | None
    cost: float
    recovery: float
    merge_return: float
    maker_rebate: float
    pnl: float
    pnl_with_rebate: float
    roi: float | None
    if_up_pnl: float | None
    if_up_roi: float | None
    if_down_pnl: float | None
    if_down_roi: float | None
    incomplete: bool


class MarketPerformancePage(BaseModel):
    items: list[MarketPerformance]
    total: int
    offset: int
    limit: int


class MarketActivityDetail(BaseModel):
    id: str
    timestamp: datetime
    type: str
    condition_id: str | None = None
    slug: str | None = None
    event_slug: str | None = None
    title: str | None = None
    side: str | None = None
    outcome: str | None = None
    asset: str | None = None
    price: float | None = None
    size: float | None = None
    usdc_size: float | None = None
    transaction_hash: str | None = None
    raw: dict[str, Any]


class MarketMetadataDetail(BaseModel):
    slug: str
    closed: bool
    outcome: str | None = None
    raw_outcome: str | None = None
    event: dict[str, Any]
    market: dict[str, Any]
    fetched_at: datetime | None = None
    updated_at: datetime | None = None


class MarketDetailResponse(BaseModel):
    market: MarketPerformance
    activities: list[MarketActivityDetail]
    metadata: MarketMetadataDetail | None = None
