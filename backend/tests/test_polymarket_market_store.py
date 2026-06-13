from datetime import datetime, timedelta, timezone

import pytest

from app.services.polymarket_client import normalize_up_down_market
from app.services.polymarket_market_store import PolymarketUpDownStore


@pytest.mark.asyncio
async def test_store_applies_book_snapshot_and_best_bid_ask() -> None:
    store = PolymarketUpDownStore()
    market = normalize_up_down_market(
        make_event("btc-updown-5m-1", "2026-06-13T05:05:00Z", "2026-06-13T05:10:00Z"),
        interval="5m",
        books={},
        now=datetime(2026, 6, 13, 5, 6, tzinfo=timezone.utc),
    )
    await store.replace_markets("5m", [market])

    changed = await store.apply_ws_message(
        {
            "event_type": "book",
            "asset_id": "up-token",
            "bids": [{"price": "0.31", "size": "10"}],
            "asks": [{"price": "0.33", "size": "7"}],
            "timestamp": "1781327160000",
        }
    )

    assert changed == ["5m"]
    snapshot = await store.list_markets("5m")
    quote = snapshot[0].outcome_quotes[0]
    assert str(quote.sell_price) == "0.31"
    assert str(quote.buy_price) == "0.33"

    await store.apply_ws_message(
        {
            "event_type": "best_bid_ask",
            "asset_id": "up-token",
            "best_bid": "0.32",
            "best_ask": "0.34",
            "timestamp": "1781327161000",
        }
    )

    snapshot = await store.list_markets("5m")
    quote = snapshot[0].outcome_quotes[0]
    assert str(quote.sell_price) == "0.32"
    assert str(quote.buy_price) == "0.34"


@pytest.mark.asyncio
async def test_store_applies_price_change_remove_and_last_trade_per_token() -> None:
    store = PolymarketUpDownStore()
    market = normalize_up_down_market(
        make_event("btc-updown-5m-1", "2026-06-13T05:05:00Z", "2026-06-13T05:10:00Z"),
        interval="5m",
        books={
            "up-token": {
                "asset_id": "up-token",
                "bids": [{"price": "0.31", "size": "10"}],
                "asks": [{"price": "0.33", "size": "7"}],
            },
            "down-token": {
                "asset_id": "down-token",
                "bids": [{"price": "0.66", "size": "10"}],
                "asks": [{"price": "0.68", "size": "7"}],
            },
        },
        now=datetime(2026, 6, 13, 5, 6, tzinfo=timezone.utc),
    )
    await store.replace_markets("5m", [market])

    await store.apply_ws_message(
        {
            "event_type": "price_change",
            "timestamp": "1781327160000",
            "price_changes": [
                {"asset_id": "up-token", "side": "BUY", "price": "0.31", "size": "0"},
                {"asset_id": "up-token", "side": "SELL", "price": "0.34", "size": "12"},
            ],
        }
    )
    await store.apply_ws_message(
        {
            "event_type": "last_trade_price",
            "asset_id": "down-token",
            "price": "0.67",
            "timestamp": "1781327161000",
        }
    )

    snapshot = await store.list_markets("5m")
    up, down = snapshot[0].outcome_quotes
    assert up.bids == []
    assert str(up.asks[0].price) == "0.33"
    assert str(up.asks[1].price) == "0.34"
    assert up.last_trade_price is None
    assert str(down.last_trade_price) == "0.67"


@pytest.mark.asyncio
async def test_store_returns_nearest_future_market_boundary() -> None:
    store = PolymarketUpDownStore()
    now = datetime(2026, 6, 13, 5, 6, tzinfo=timezone.utc)
    expired = normalize_up_down_market(
        make_event("expired", "2026-06-13T05:00:00Z", "2026-06-13T05:05:00Z"),
        interval="5m",
        books={},
        now=now,
    )
    future = normalize_up_down_market(
        make_event("future", "2026-06-13T05:10:00Z", "2026-06-13T05:15:00Z"),
        interval="5m",
        books={},
        now=now,
    )
    later = normalize_up_down_market(
        make_event("later", "2026-06-13T05:30:00Z", "2026-06-13T05:35:00Z"),
        interval="15m",
        books={},
        now=now,
    )

    await store.replace_markets("5m", [expired, future])
    await store.replace_markets("15m", [later])

    assert await store.market_count() == 3
    assert await store.next_market_boundary(now) == now + timedelta(minutes=4)


def make_event(slug: str, start_time: str, end_time: str) -> dict:
    return {
        "id": slug,
        "slug": slug,
        "title": "Bitcoin Up or Down - June 13, 1:05AM-1:10AM ET",
        "seriesSlug": "btc-up-or-down-5m",
        "endDate": end_time,
        "markets": [
            {
                "id": f"{slug}-market",
                "question": "Bitcoin Up or Down - June 13, 1:05AM-1:10AM ET",
                "conditionId": "0xabc",
                "slug": slug,
                "eventStartTime": start_time,
                "endDate": end_time,
                "outcomes": '["Up", "Down"]',
                "outcomePrices": '["0.48", "0.52"]',
                "clobTokenIds": '["up-token", "down-token"]',
                "acceptingOrders": True,
                "volumeNum": 100,
                "liquidityNum": 200,
            }
        ],
    }
