from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.services.polymarket_client import (
    PolymarketOrderLevel,
    PolymarketOutcomeQuote,
    PolymarketUpDownMarket,
    best_ask,
    best_bid,
    decimal_or_none,
    market_window,
    normalize_order_levels,
    seconds_between,
)

ORDER_BOOK_DEPTH = 10


class PolymarketUpDownStore:
    """Polymarket 实时缓存：REST 负责发现市场，marketChannel WS 负责覆盖盘口。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._markets_by_interval: dict[str, list[PolymarketUpDownMarket]] = {}

    async def replace_markets(self, interval: str, markets: list[PolymarketUpDownMarket]) -> None:
        async with self._lock:
            existing_quotes = {
                quote.token_id: quote
                for market in self._markets_by_interval.get(interval, [])
                for quote in market.outcome_quotes
                if quote.token_id
            }
            self._markets_by_interval[interval] = [
                merge_market_quotes(market, existing_quotes)
                for market in markets
            ]

    async def list_markets(self, interval: str, *, limit: int | None = None) -> list[PolymarketUpDownMarket]:
        async with self._lock:
            markets = list(self._markets_by_interval.get(interval, []))
        refreshed = [refresh_market_window(market) for market in markets]
        if limit is not None:
            return refreshed[:limit]
        return refreshed

    async def get_market(self, market_id: str) -> PolymarketUpDownMarket | None:
        async with self._lock:
            markets = [
                market
                for interval_markets in self._markets_by_interval.values()
                for market in interval_markets
            ]
        for market in markets:
            if market.id == market_id:
                return refresh_market_window(market)
        return None

    async def token_ids(self) -> list[str]:
        async with self._lock:
            ids = [
                quote.token_id
                for markets in self._markets_by_interval.values()
                for market in markets
                for quote in market.outcome_quotes
                if quote.token_id
            ]
        return sorted(set(ids))

    async def market_count(self) -> int:
        async with self._lock:
            return sum(len(markets) for markets in self._markets_by_interval.values())

    async def next_market_boundary(self, now: datetime) -> datetime | None:
        now = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
        async with self._lock:
            boundaries = [
                boundary
                for markets in self._markets_by_interval.values()
                for market in markets
                for boundary in (market.start_time, market.end_time)
                if boundary is not None and boundary > now
            ]
        return min(boundaries) if boundaries else None

    async def apply_ws_message(self, message: dict[str, Any]) -> list[str]:
        event_type = str(message.get("event_type") or "")
        if event_type == "book":
            return await self._apply_book(message)
        if event_type == "price_change":
            return await self._apply_price_change(message)
        if event_type == "best_bid_ask":
            return await self._apply_best_bid_ask(message)
        if event_type == "last_trade_price":
            return await self._apply_last_trade_price(message)
        if event_type in {"new_market", "market_resolved"}:
            return list(self._markets_by_interval.keys())
        return []

    async def _apply_book(self, message: dict[str, Any]) -> list[str]:
        token_id = string_or_none(message.get("asset_id"))
        if not token_id:
            return []
        bids = sorted_levels(normalize_order_levels(message.get("bids")), reverse=True)
        asks = sorted_levels(normalize_order_levels(message.get("asks")), reverse=False)
        updated_at = timestamp_from_ms(message.get("timestamp"))
        return await self._update_quote(
            token_id,
            lambda quote: replace(
                quote,
                bids=bids[:ORDER_BOOK_DEPTH],
                asks=asks[:ORDER_BOOK_DEPTH],
                best_bid=best_bid(bids),
                best_ask=best_ask(asks),
                buy_price=best_ask(asks),
                sell_price=best_bid(bids),
                updated_at=updated_at,
            ),
        )

    async def _apply_price_change(self, message: dict[str, Any]) -> list[str]:
        changed_intervals: set[str] = set()
        changes = message.get("price_changes")
        if not isinstance(changes, list):
            return []
        for change in changes:
            if not isinstance(change, dict):
                continue
            token_id = string_or_none(change.get("asset_id"))
            if not token_id:
                continue
            updated_at = timestamp_from_ms(message.get("timestamp"))
            intervals = await self._update_quote(
                token_id,
                lambda quote, row=change: update_quote_level(quote, row, updated_at),
            )
            changed_intervals.update(intervals)
        return sorted(changed_intervals)

    async def _apply_best_bid_ask(self, message: dict[str, Any]) -> list[str]:
        token_id = string_or_none(message.get("asset_id"))
        if not token_id:
            return []
        bid = decimal_or_none(message.get("best_bid"))
        ask = decimal_or_none(message.get("best_ask"))
        updated_at = timestamp_from_ms(message.get("timestamp"))
        return await self._update_quote(
            token_id,
            lambda quote: replace(
                quote,
                best_bid=bid,
                best_ask=ask,
                buy_price=ask,
                sell_price=bid,
                updated_at=updated_at,
            ),
        )

    async def _apply_last_trade_price(self, message: dict[str, Any]) -> list[str]:
        token_id = string_or_none(message.get("asset_id"))
        if not token_id:
            return []
        price = decimal_or_none(message.get("price"))
        updated_at = timestamp_from_ms(message.get("timestamp"))
        return await self._update_quote(
            token_id,
            lambda quote: replace(quote, last_trade_price=price, updated_at=updated_at),
        )

    async def _update_quote(self, token_id: str, updater: Any) -> list[str]:
        changed_intervals: list[str] = []
        async with self._lock:
            for interval, markets in self._markets_by_interval.items():
                updated_markets: list[PolymarketUpDownMarket] = []
                interval_changed = False
                for market in markets:
                    updated_quotes = []
                    market_changed = False
                    for quote in market.outcome_quotes:
                        if quote.token_id == token_id:
                            updated_quotes.append(updater(quote))
                            market_changed = True
                        else:
                            updated_quotes.append(quote)
                    if market_changed:
                        interval_changed = True
                        updated_markets.append(
                            replace(market, outcome_quotes=updated_quotes, updated_at=datetime.now(timezone.utc))
                        )
                    else:
                        updated_markets.append(market)
                if interval_changed:
                    changed_intervals.append(interval)
                    self._markets_by_interval[interval] = updated_markets
        return changed_intervals


def merge_market_quotes(
    market: PolymarketUpDownMarket,
    existing_quotes: dict[str | None, PolymarketOutcomeQuote],
) -> PolymarketUpDownMarket:
    quotes = [
        merge_quote(quote, existing_quotes.get(quote.token_id))
        for quote in market.outcome_quotes
    ]
    return replace(market, outcome_quotes=quotes)


def merge_quote(
    fresh: PolymarketOutcomeQuote,
    existing: PolymarketOutcomeQuote | None,
) -> PolymarketOutcomeQuote:
    if existing is None:
        return fresh
    return replace(
        fresh,
        buy_price=existing.buy_price,
        sell_price=existing.sell_price,
        best_bid=existing.best_bid,
        best_ask=existing.best_ask,
        last_trade_price=existing.last_trade_price,
        updated_at=existing.updated_at,
        bids=existing.bids,
        asks=existing.asks,
    )


def refresh_market_window(market: PolymarketUpDownMarket) -> PolymarketUpDownMarket:
    now = datetime.now(timezone.utc)
    return replace(
        market,
        window=market_window(start_time=market.start_time, end_time=market.end_time, now=now),
        seconds_to_start=seconds_between(now, market.start_time),
        seconds_to_end=seconds_between(now, market.end_time),
    )


def update_quote_level(
    quote: PolymarketOutcomeQuote,
    change: dict[str, Any],
    updated_at: datetime | None,
) -> PolymarketOutcomeQuote:
    price = decimal_or_none(change.get("price"))
    size = decimal_or_none(change.get("size"))
    side = str(change.get("side") or "").upper()
    bids = quote.bids
    asks = quote.asks
    if price is not None and side == "BUY":
        bids = update_levels(bids, price, size, reverse=True)
    if price is not None and side == "SELL":
        asks = update_levels(asks, price, size, reverse=False)
    bid = decimal_or_none(change.get("best_bid")) or best_bid(bids)
    ask = decimal_or_none(change.get("best_ask")) or best_ask(asks)
    return replace(
        quote,
        bids=bids,
        asks=asks,
        best_bid=bid,
        best_ask=ask,
        buy_price=ask,
        sell_price=bid,
        updated_at=updated_at,
    )


def update_levels(
    levels: list[PolymarketOrderLevel],
    price: Decimal,
    size: Decimal | None,
    *,
    reverse: bool,
) -> list[PolymarketOrderLevel]:
    by_price = {level.price: level for level in levels if level.price is not None}
    if size is None or size == Decimal("0"):
        by_price.pop(price, None)
    else:
        by_price[price] = PolymarketOrderLevel(price=price, size=size)
    return sorted_levels(list(by_price.values()), reverse=reverse)[:ORDER_BOOK_DEPTH]


def sorted_levels(levels: list[PolymarketOrderLevel], *, reverse: bool) -> list[PolymarketOrderLevel]:
    return sorted(
        [level for level in levels if level.price is not None],
        key=lambda level: level.price or Decimal("0"),
        reverse=reverse,
    )


def timestamp_from_ms(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


polymarket_up_down_store = PolymarketUpDownStore()
