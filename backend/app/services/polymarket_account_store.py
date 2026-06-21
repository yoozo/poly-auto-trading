from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core.config import settings
from app.schemas.polymarket import (
    PolymarketAccountBalance,
    PolymarketAccountOrder,
    PolymarketAccountPosition,
    PolymarketAccountState,
    PolymarketAccountTrade,
)

MAX_RECENT_TRADES = 50
TRADE_PENDING = "pending"
TRADE_CONFIRMED = "confirmed"
TRADE_REFRESH_FAILED = "refresh_failed"


class PolymarketAccountStore:
    """私有账户状态缓存：REST 快照定基准，User WS 事件增量修正并驱动广播。"""

    def __init__(self) -> None:
        self._positions: list[PolymarketAccountPosition] = []
        self._orders_by_id: dict[str, PolymarketAccountOrder] = {}
        self._recent_trades: list[PolymarketAccountTrade] = []
        self._balance: PolymarketAccountBalance | None = None
        self._ws_state = "idle"
        self._error: str | None = None
        self._last_positions_refresh_at: datetime | None = None
        self._last_orders_refresh_at: datetime | None = None
        self._last_trade_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def replace_positions(self, positions: list[PolymarketAccountPosition]) -> None:
        async with self._lock:
            self._positions = positions
            self._last_positions_refresh_at = utc_now()

    async def replace_orders(self, orders: list[PolymarketAccountOrder]) -> None:
        async with self._lock:
            self._orders_by_id = {order.id: order for order in orders}
            self._last_orders_refresh_at = utc_now()

    async def replace_balance(self, balance: PolymarketAccountBalance) -> None:
        async with self._lock:
            self._balance = balance

    async def apply_order(self, order: PolymarketAccountOrder) -> None:
        async with self._lock:
            if order_is_open(order):
                self._orders_by_id[order.id] = order
            else:
                self._orders_by_id.pop(order.id, None)
            self._last_orders_refresh_at = utc_now()

    async def apply_trade(self, trade: PolymarketAccountTrade) -> None:
        now = utc_now()
        async with self._lock:
            pending_trade = trade.model_copy(
                update={
                    "confirmation_status": TRADE_PENDING,
                    "received_at": trade.received_at or now,
                    "confirmed_at": None,
                }
            )
            self._recent_trades = dedupe_recent_trade([pending_trade, *self._recent_trades])[:MAX_RECENT_TRADES]
            self._last_trade_at = trade.timestamp or now

    async def mark_trades_confirmation(self, trade_ids: set[str], status: str) -> None:
        if not trade_ids:
            return
        now = utc_now()
        confirmed_at = now if status == TRADE_CONFIRMED else None
        async with self._lock:
            # trade 先由 User WS 写入 pending；REST 快照完成后只更新确认状态，不覆盖成交明细。
            self._recent_trades = [
                trade.model_copy(update={"confirmation_status": status, "confirmed_at": confirmed_at})
                if trade.id in trade_ids and trade.confirmation_status == TRADE_PENDING
                else trade
                for trade in self._recent_trades
            ]

    async def set_ws_state(self, state: str, error: str | None = None) -> None:
        async with self._lock:
            self._ws_state = state
            self._error = error

    async def set_error(self, error: str | None) -> None:
        async with self._lock:
            self._error = error

    async def snapshot(self, condition_id: str | None = None) -> PolymarketAccountState:
        normalized_condition = normalize_key(condition_id)
        async with self._lock:
            positions = list(self._positions)
            orders = list(self._orders_by_id.values())
            recent_trades = list(self._recent_trades)
            balance = self._balance
            ws_state = self._ws_state
            error = self._error
            last_positions_refresh_at = self._last_positions_refresh_at
            last_orders_refresh_at = self._last_orders_refresh_at
            last_trade_at = self._last_trade_at
        if normalized_condition:
            positions = [
                position
                for position in positions
                if normalize_key(position.condition_id) == normalized_condition
            ]
            orders = [order for order in orders if normalize_key(order.market) == normalized_condition]
            recent_trades = [
                trade
                for trade in recent_trades
                if normalize_key(trade.market) == normalized_condition
            ]
        return PolymarketAccountState(
            wallet=settings.polymarket_position_wallet.lower() or None,
            clob_address=settings.polymarket_clob_address.lower() or None,
            # 余额是账户级快照，condition_id 只过滤市场相关的 positions/orders/trades。
            balance=balance,
            condition_id=condition_id,
            positions=positions,
            orders=orders,
            recent_trades=recent_trades,
            ws_state=ws_state,
            last_positions_refresh_at=last_positions_refresh_at,
            last_orders_refresh_at=last_orders_refresh_at,
            last_trade_at=last_trade_at,
            error=error,
        )


def order_is_open(order: PolymarketAccountOrder) -> bool:
    status = (order.status or "").lower()
    if status in {"", "live", "open", "active", "partially_filled", "partially-filled"}:
        return (order.remaining_size or 0) > 0 or order.remaining_size is None
    return status not in {"filled", "matched", "cancelled", "canceled", "expired", "failed"}


def dedupe_recent_trade(trades: list[PolymarketAccountTrade]) -> list[PolymarketAccountTrade]:
    seen: set[str] = set()
    result: list[PolymarketAccountTrade] = []
    for trade in trades:
        if trade.id in seen:
            continue
        seen.add(trade.id)
        result.append(trade)
    return result


def normalize_key(value: str | None) -> str | None:
    return value.lower() if value else None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


polymarket_account_store = PolymarketAccountStore()
