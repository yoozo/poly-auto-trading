from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import websockets

from app.core.config import settings
from app.schemas import OrderbookLevel, OrderbookSnapshot
from app.services.state_store import StateStore, state_store


class PolymarketMarketWsService:
    def __init__(self, store: StateStore = state_store) -> None:
        self._store = store

    async def run_ws_forever(self) -> None:
        backoff_seconds = 1.0
        while True:
            token_ids = self._store.get_market_token_ids()
            if not token_ids:
                self._store.set_service_health("polymarket_market_ws", "idle")
                await asyncio.sleep(2)
                continue

            try:
                await self._run_ws_once(token_ids)
                backoff_seconds = 1.0
            except asyncio.CancelledError:
                self._store.set_service_health("polymarket_market_ws", "stopped")
                raise
            except Exception as exc:
                self._store.set_service_health("polymarket_market_ws", "reconnecting", last_error=str(exc))
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 30.0)

    async def _run_ws_once(self, token_ids: list[str]) -> None:
        subscribed = sorted(set(token_ids))
        self._store.set_service_health("polymarket_market_ws", "reconnecting")
        async with websockets.connect(
            settings.polymarket_market_ws_url,
            ping_interval=None,
            ping_timeout=None,
        ) as websocket:
            await self._subscribe(websocket, subscribed)
            self._store.set_service_health("polymarket_market_ws", "connected")
            ping_task = asyncio.create_task(self._ping_loop(websocket))
            resubscribe_task = asyncio.create_task(self._resubscribe_loop(websocket, subscribed))
            try:
                async for raw_message in websocket:
                    self._handle_message(raw_message)
            finally:
                for task in (ping_task, resubscribe_task):
                    task.cancel()
                for task in (ping_task, resubscribe_task):
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    async def _subscribe(self, websocket: Any, token_ids: list[str]) -> None:
        if not token_ids:
            return
        await websocket.send(json.dumps({"type": "market", "assets_ids": token_ids, "custom_feature_enabled": True}))

    async def _ping_loop(self, websocket: Any) -> None:
        while True:
            await asyncio.sleep(settings.polymarket_ws_ping_seconds)
            await websocket.send("PING")

    async def _resubscribe_loop(self, websocket: Any, subscribed: list[str]) -> None:
        while True:
            await asyncio.sleep(settings.polymarket_ws_resubscribe_seconds)
            current = sorted(set(self._store.get_market_token_ids()))
            new_tokens = [token_id for token_id in current if token_id not in subscribed]
            if new_tokens:
                subscribed.extend(new_tokens)
                subscribed.sort()
                await self._subscribe(websocket, new_tokens)

    def _handle_message(self, raw_message: str | bytes) -> None:
        if raw_message in ("PONG", b"PONG"):
            return

        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            return

        messages = payload if isinstance(payload, list) else [payload]
        for message in messages:
            if isinstance(message, dict):
                self._handle_event(message)

    def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type") or event.get("type") or "")
        if event_type in {"book", "orderbook"} or ("bids" in event and "asks" in event):
            snapshot = self._snapshot_from_book(event)
            if snapshot:
                self._store.set_orderbook(snapshot)
            return

        if event_type == "best_bid_ask":
            snapshot = self._snapshot_from_best_bid_ask(event)
            if snapshot:
                self._store.set_orderbook(snapshot)
            return

        if event_type in {"price_change", "last_trade_price"} or "changes" in event or "price_changes" in event:
            for snapshot in self._snapshots_from_price_change(event):
                self._store.set_orderbook(snapshot)

    def _snapshot_from_book(self, payload: dict[str, Any]) -> OrderbookSnapshot | None:
        token_id = _token_id(payload)
        if not token_id:
            return None

        bids = _levels(payload.get("bids") or payload.get("buys") or [])
        asks = _levels(payload.get("asks") or payload.get("sells") or [])
        return _build_snapshot(
            token_id=token_id,
            bids=bids,
            asks=asks,
            fallback_best_bid=_maybe_float(payload.get("best_bid") or payload.get("bestBid")),
            fallback_best_ask=_maybe_float(payload.get("best_ask") or payload.get("bestAsk")),
        )

    def _snapshots_from_price_change(self, payload: dict[str, Any]) -> list[OrderbookSnapshot]:
        changes = payload.get("price_changes") or payload.get("changes")
        if not isinstance(changes, list):
            changes = [payload]

        snapshots_by_token: dict[str, OrderbookSnapshot] = {}
        for change in changes:
            if not isinstance(change, dict):
                continue
            token_id = _token_id(change) or _token_id(payload)
            if not token_id:
                continue

            existing = snapshots_by_token.get(token_id) or self._store.get_orderbook(token_id)
            if existing is None and not _has_orderbook_value(change, payload):
                continue

            best_bid = _maybe_float(
                change.get("best_bid")
                or change.get("bestBid")
                or payload.get("best_bid")
                or payload.get("bestBid")
            )
            best_ask = _maybe_float(
                change.get("best_ask")
                or change.get("bestAsk")
                or payload.get("best_ask")
                or payload.get("bestAsk")
            )

            bids = list(existing.bids) if existing else []
            asks = list(existing.asks) if existing else []
            _apply_level_change(bids, asks, change)
            snapshots_by_token[token_id] = _build_snapshot(
                token_id=token_id,
                bids=bids,
                asks=asks,
                fallback_best_bid=best_bid if best_bid is not None else (existing.best_bid if existing else None),
                fallback_best_ask=best_ask if best_ask is not None else (existing.best_ask if existing else None),
                prefer_fallback_best=best_bid is not None or best_ask is not None,
            )
        return list(snapshots_by_token.values())

    def _snapshot_from_best_bid_ask(self, payload: dict[str, Any]) -> OrderbookSnapshot | None:
        token_id = _token_id(payload)
        if not token_id:
            return None
        existing = self._store.get_orderbook(token_id)
        return _build_snapshot(
            token_id=token_id,
            bids=list(existing.bids) if existing else [],
            asks=list(existing.asks) if existing else [],
            fallback_best_bid=_maybe_float(payload.get("best_bid") or payload.get("bestBid")),
            fallback_best_ask=_maybe_float(payload.get("best_ask") or payload.get("bestAsk")),
            prefer_fallback_best=True,
        )


def _build_snapshot(
    token_id: str,
    bids: list[OrderbookLevel],
    asks: list[OrderbookLevel],
    fallback_best_bid: float | None = None,
    fallback_best_ask: float | None = None,
    prefer_fallback_best: bool = False,
) -> OrderbookSnapshot:
    sorted_bids = sorted(bids, key=lambda level: level.price, reverse=True)[:20]
    sorted_asks = sorted(asks, key=lambda level: level.price)[:20]
    best_bid = fallback_best_bid if prefer_fallback_best and fallback_best_bid is not None else (sorted_bids[0].price if sorted_bids else fallback_best_bid)
    best_ask = fallback_best_ask if prefer_fallback_best and fallback_best_ask is not None else (sorted_asks[0].price if sorted_asks else fallback_best_ask)
    spread = round(best_ask - best_bid, 4) if best_bid is not None and best_ask is not None else None
    liquidity = round(sum(level.size for level in sorted_bids + sorted_asks), 4) if sorted_bids or sorted_asks else None
    return OrderbookSnapshot(
        token_id=token_id,
        best_bid=best_bid,
        best_ask=best_ask,
        spread=spread,
        liquidity=liquidity,
        updated_at=datetime.now(timezone.utc),
        bids=sorted_bids,
        asks=sorted_asks,
    )


def _apply_level_change(
    bids: list[OrderbookLevel],
    asks: list[OrderbookLevel],
    change: dict[str, Any],
) -> None:
    side = str(change.get("side") or change.get("book_side") or "").lower()
    price = _maybe_float(change.get("price") or change.get("p"))
    size = _maybe_float(change.get("size") or change.get("s"))
    if price is None or size is None:
        return

    if side in {"buy", "bid", "bids"}:
        _upsert_level(bids, price, size)
    elif side in {"sell", "ask", "asks"}:
        _upsert_level(asks, price, size)


def _upsert_level(levels: list[OrderbookLevel], price: float, size: float) -> None:
    for index, level in enumerate(levels):
        if level.price == price:
            if size <= 0:
                levels.pop(index)
            else:
                levels[index] = OrderbookLevel(price=price, size=size)
            return

    if size > 0:
        levels.append(OrderbookLevel(price=price, size=size))


def _has_orderbook_value(change: dict[str, Any], payload: dict[str, Any]) -> bool:
    keys = ("best_bid", "bestBid", "best_ask", "bestAsk", "price", "p")
    return any(change.get(key) not in (None, "") or payload.get(key) not in (None, "") for key in keys)


def _levels(raw_levels: Any) -> list[OrderbookLevel]:
    levels: list[OrderbookLevel] = []
    if not isinstance(raw_levels, list):
        return levels

    for raw_level in raw_levels:
        price: float | None = None
        size: float | None = None
        if isinstance(raw_level, dict):
            price = _maybe_float(raw_level.get("price") or raw_level.get("p"))
            size = _maybe_float(raw_level.get("size") or raw_level.get("s"))
        elif isinstance(raw_level, list | tuple) and len(raw_level) >= 2:
            price = _maybe_float(raw_level[0])
            size = _maybe_float(raw_level[1])

        if price is not None and size is not None:
            levels.append(OrderbookLevel(price=price, size=size))
    return levels


def _token_id(payload: dict[str, Any]) -> str:
    return str(
        payload.get("asset_id")
        or payload.get("assetId")
        or payload.get("token_id")
        or payload.get("tokenId")
        or ""
    )


def _maybe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


polymarket_market_ws_service = PolymarketMarketWsService()
